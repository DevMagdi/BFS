from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import BinaryIO, Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, null, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_context.models import (
    Employee,
    Project,
)
from businessflow.company_context.schemas import (
    EmployeeCreate,
    EmployeeSkillCreate,
    ProjectCreate,
    ResourceAssignmentCreate,
    SkillCreate,
    WeeklyCapacityCreate,
)
from businessflow.config import Settings
from businessflow.data_imports.catalog import (
    DATASETS,
    field_definitions,
    suggest_mapping,
    suggested_datasets,
)
from businessflow.data_imports.models import DataImport, DataImportRecordLink, DataImportRow
from businessflow.data_imports.parsers import (
    ImportFileError,
    ParsedFile,
    parse_file,
    read_limited_upload,
)
from businessflow.data_imports.schemas import (
    ImportApplyResult,
    ImportConfirmRequest,
    ImportCounts,
    ImportDataset,
    ImportInspection,
    ImportInspectionResponse,
    ImportIssueCount,
    ImportMappingRequest,
    ImportMappingResponse,
    ImportPreviewPage,
    ImportPreviewRow,
    ImportPurgeResult,
    ImportReport,
    ImportRowIssue,
    ImportStatus,
    ImportSummary,
    ImportSummaryPage,
)
from businessflow.data_imports.validation import (
    PlannedRow,
    ValidationResult,
    _entity_fingerprint,
    validate_staged_import,
)
from businessflow.errors import ApiError, CommitAndRaise
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.services.company_projects import create_project, create_resource_assignment
from businessflow.services.company_workforce import (
    create_employee,
    create_employee_skill,
    create_skill,
    record_weekly_capacity,
)


class CanonicalRecord(Protocol):
    id: UUID


TERMINAL_IMPORT_STATUSES = frozenset(
    {"applied", "applied_with_warnings", "cancelled", "expired", "failed"}
)
STAGED_IMPORT_STATUSES = frozenset({"inspected", "mapped", "validated", "ready", "confirmed"})
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._~:-]{16,128}$")


def _safe_file_name(filename: str | None) -> str:
    if not filename:
        raise ApiError(
            422, "invalid_file_name", "A file name with a CSV or XLSX extension is required."
        )
    leaf = filename.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[\x00-\x1f\x7f]", "_", leaf).strip(" .")
    if not safe or len(safe) > 255:
        raise ApiError(422, "invalid_file_name", "The file name is invalid or too long.")
    return safe


def _row_hash(sheet_name: str, row_number: int, values: list[str | None]) -> str:
    payload = json.dumps(
        {"worksheet": sheet_name, "row_number": row_number, "values": values},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _inspection(parsed: ParsedFile, max_rows: int) -> dict[str, object]:
    return {
        "file_format": parsed.file_format,
        "worksheets": [
            {
                "name": sheet.name,
                "headers": sheet.headers,
                "row_count": len(sheet.rows),
                "blank_rows_ignored": sheet.blank_rows_ignored,
            }
            for sheet in parsed.worksheets
        ],
        "total_rows": parsed.total_rows,
        "blank_rows_ignored": sum(sheet.blank_rows_ignored for sheet in parsed.worksheets),
        "max_rows": max_rows,
    }


def _prior_import_for_idempotency(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    key_hash: str,
    user_id: UUID,
    file_hash: str,
) -> DataImport | None:
    prior = session.scalar(
        select(DataImport).where(
            DataImport.tenant_id == tenant_id,
            DataImport.company_id == company_id,
            DataImport.idempotency_key_hash == key_hash,
        )
    )
    if prior is not None and (prior.uploaded_by_user_id != user_id or prior.file_hash != file_hash):
        raise ApiError(
            409,
            "idempotency_key_conflict",
            "This Idempotency-Key is already associated with a different import request.",
        )
    return prior


def stage_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    upload: BinaryIO,
    filename: str | None,
    idempotency_key: str,
    request_id: UUID,
    settings: Settings,
) -> DataImport:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        raise ApiError(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must be 16 to 128 ASCII letters, digits, or . _ ~ : - characters.",
        )
    safe_name = _safe_file_name(filename)
    try:
        content = read_limited_upload(upload, settings.import_max_file_bytes)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    file_hash = hashlib.sha256(content).hexdigest()
    key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
    prior = _prior_import_for_idempotency(
        session, tenant_id, access.company.id, key_hash, user.id, file_hash
    )
    if prior is not None:
        return prior

    try:
        parsed = parse_file(safe_name, content, settings.import_max_rows)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    if not parsed.total_rows:
        raise ApiError(422, "empty_dataset", "The file contains no non-empty data rows.")
    now = datetime.now(UTC)
    import_record = DataImport(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        uploaded_by_user_id=user.id,
        idempotency_key_hash=key_hash,
        file_name=safe_name,
        file_format=parsed.file_format,
        file_hash=file_hash,
        file_size_bytes=len(content),
        inspection=_inspection(parsed, settings.import_max_rows),
        ignored_columns=[],
        mapping_version=0,
        status="inspected",
        total_rows=parsed.total_rows,
        staging_expires_at=now + timedelta(hours=settings.import_staging_ttl_hours),
    )
    try:
        with session.begin_nested():
            session.add(import_record)
            session.flush()
    except IntegrityError as exc:
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint_name != "uq_data_imports_idempotency":
            raise
        prior = _prior_import_for_idempotency(
            session, tenant_id, access.company.id, key_hash, user.id, file_hash
        )
        if prior is not None:
            return prior
        raise
    for sheet in parsed.worksheets:
        for source_row in sheet.rows:
            session.add(
                DataImportRow(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    company_id=access.company.id,
                    import_id=import_record.id,
                    worksheet_name=sheet.name,
                    row_number=source_row.row_number,
                    row_hash=_row_hash(sheet.name, source_row.row_number, source_row.values),
                    raw_values=source_row.values,
                    issues=[],
                    row_status="pending",
                )
            )
    record_event(
        session,
        "data_import.uploaded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={
            "file_hash": file_hash,
            "file_format": parsed.file_format,
            "file_size_bytes": len(content),
            "row_count": parsed.total_rows,
            "worksheet_count": len(parsed.worksheets),
        },
    )
    session.flush()
    return import_record


def _get_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    *,
    write: bool,
    lock: bool = False,
) -> DataImport:
    permission = "company_context:write" if write else "company_context:read"
    access = require_company_context_access(session, user, tenant_id, permission)
    statement = select(DataImport).where(
        DataImport.id == import_id,
        DataImport.tenant_id == tenant_id,
        DataImport.company_id == access.company.id,
    )
    if lock:
        statement = statement.with_for_update()
    import_record = session.scalar(statement)
    if import_record is None:
        raise ApiError(404, "import_not_found", "The import was not found.")
    return import_record


def _summary(import_record: DataImport) -> ImportSummary:
    status = import_record.status
    if status in STAGED_IMPORT_STATUSES and import_record.staging_expires_at <= datetime.now(UTC):
        status = "expired"
    return ImportSummary(
        id=import_record.id,
        tenant_id=import_record.tenant_id,
        company_id=import_record.company_id,
        uploaded_by_user_id=import_record.uploaded_by_user_id,
        confirmed_by_user_id=import_record.confirmed_by_user_id,
        applied_by_user_id=import_record.applied_by_user_id,
        file_name=import_record.file_name,
        file_format=import_record.file_format,
        file_hash=import_record.file_hash,
        file_size_bytes=import_record.file_size_bytes,
        dataset_type=cast(ImportDataset | None, import_record.dataset_type),
        selected_worksheet=import_record.selected_worksheet,
        field_mapping=import_record.field_mapping,
        ignored_columns=import_record.ignored_columns,
        mapping_version=import_record.mapping_version,
        status=cast(ImportStatus, status),
        counts=ImportCounts(
            total_rows=import_record.total_rows,
            valid_rows=import_record.valid_rows,
            invalid_rows=import_record.invalid_rows,
            warning_rows=import_record.warning_rows,
            duplicate_rows=import_record.duplicate_rows,
            eligible_rows=import_record.eligible_rows,
            skipped_rows=import_record.skipped_rows,
            created_rows=import_record.created_rows,
            updated_rows=import_record.updated_rows,
        ),
        acknowledge_warnings=import_record.acknowledge_warnings,
        allow_partial=import_record.allow_partial,
        validation_hash=import_record.validation_hash,
        staging_expires_at=import_record.staging_expires_at,
        confirmed_at=import_record.confirmed_at,
        applied_at=import_record.applied_at,
        created_at=import_record.created_at,
        updated_at=import_record.updated_at,
    )


def _inspection_model(import_record: DataImport) -> ImportInspection:
    return ImportInspection.model_validate(import_record.inspection)


def _expire_staging(
    session: Session, user: UserAccount, import_record: DataImport, request_id: UUID
) -> None:
    if import_record.status not in STAGED_IMPORT_STATUSES:
        return
    import_record.status = "expired"
    session.execute(
        update(DataImportRow)
        .where(
            DataImportRow.tenant_id == import_record.tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
        )
        .values(raw_values=null())
    )
    record_event(
        session,
        "data_import.expired",
        tenant_id=import_record.tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={"file_hash": import_record.file_hash},
    )
    session.flush()


def _ensure_not_expired(
    session: Session, user: UserAccount, import_record: DataImport, request_id: UUID
) -> None:
    if import_record.status == "expired":
        raise ApiError(410, "import_staging_expired", "The staged import has expired.")
    if (
        import_record.status in STAGED_IMPORT_STATUSES
        and import_record.staging_expires_at <= datetime.now(UTC)
    ):
        _expire_staging(session, user, import_record, request_id)
        raise CommitAndRaise(
            ApiError(
                410,
                "import_staging_expired",
                "The staged import has expired and its source rows were purged.",
            )
        )


def _worksheet(import_record: DataImport, worksheet_name: str) -> tuple[list[str], int]:
    worksheets = import_record.inspection.get("worksheets", [])
    if not isinstance(worksheets, list):
        raise ApiError(409, "import_snapshot_invalid", "The staged worksheet metadata is invalid.")
    for item in worksheets:
        if isinstance(item, dict) and item.get("name") == worksheet_name:
            headers = item.get("headers")
            row_count = item.get("row_count")
            if isinstance(headers, list) and isinstance(row_count, int):
                return cast(list[str], headers), row_count
    raise ApiError(
        422, "worksheet_not_found", "The selected worksheet was not found in this import."
    )


def _mapping_shape(import_record: DataImport, request: ImportMappingRequest) -> list[str]:
    headers, _ = _worksheet(import_record, request.worksheet_name)
    spec = DATASETS[request.dataset_type]
    known_fields = set(spec.by_name)
    unknown_fields = set(request.field_mapping) - known_fields
    if unknown_fields:
        raise ApiError(
            422, "invalid_import_mapping", "The mapping contains unsupported dataset fields."
        )
    if any(not column for column in request.field_mapping.values()):
        raise ApiError(
            422, "invalid_import_mapping", "Mapped source column names may not be blank."
        )
    invalid_headers = set(request.field_mapping.values()) - set(headers)
    if invalid_headers or set(request.ignored_columns) - set(headers):
        raise ApiError(
            422,
            "invalid_import_mapping",
            "Every mapped or ignored column must match a worksheet header exactly.",
        )
    mapped_headers = list(request.field_mapping.values())
    if len(set(mapped_headers)) != len(mapped_headers):
        raise ApiError(
            422,
            "invalid_import_mapping",
            "Each source column may map to at most one canonical field.",
        )
    if set(mapped_headers) & set(request.ignored_columns):
        raise ApiError(
            422,
            "invalid_import_mapping",
            "A source column may not be both mapped and explicitly ignored.",
        )
    missing_required = spec.required - set(request.field_mapping)
    if missing_required:
        raise ApiError(
            422,
            "required_import_mapping_missing",
            "Map all required fields before saving this mapping.",
        )
    for alternatives in spec.required_one_of:
        if len(alternatives & set(request.field_mapping)) != 1:
            raise ApiError(
                422,
                "required_import_reference_mapping",
                "Choose exactly one mapped reference column from each required alternative group.",
            )
    for alternatives in spec.mutually_exclusive:
        if len(alternatives & set(request.field_mapping)) > 1:
            raise ApiError(
                422,
                "ambiguous_import_reference_mapping",
                "Choose at most one reference column from each alternative group.",
            )
    unaccounted = set(headers) - set(mapped_headers) - set(request.ignored_columns)
    if unaccounted:
        raise ApiError(
            422,
            "unaccounted_import_columns",
            "Map or explicitly ignore every source column; "
            "unmapped data is never silently discarded.",
        )
    return headers


def _mapping_response(
    import_record: DataImport,
    dataset_type: ImportDataset,
    worksheet_name: str,
    headers: list[str],
    mapping: dict[str, str],
    ignored_columns: list[str],
) -> ImportMappingResponse:
    suggestions = suggest_mapping(headers, dataset_type)
    unmapped = sorted(set(headers) - set(mapping.values()) - set(ignored_columns))
    return ImportMappingResponse(
        import_record=_summary(import_record),
        field_definitions=field_definitions(dataset_type),
        required_one_of=[sorted(group) for group in DATASETS[dataset_type].required_one_of],
        mutually_exclusive=[sorted(group) for group in DATASETS[dataset_type].mutually_exclusive],
        suggested_mapping=suggestions,
        unmapped_columns=unmapped,
        ignored_columns=ignored_columns,
    )


def inspect_import_mapping(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    dataset_type: ImportDataset,
    worksheet_name: str,
) -> ImportMappingResponse:
    import_record = _get_import(session, user, tenant_id, import_id, write=False)
    if import_record.status == "expired" or (
        import_record.status in STAGED_IMPORT_STATUSES
        and import_record.staging_expires_at <= datetime.now(UTC)
    ):
        raise ApiError(
            410,
            "import_staging_expired",
            "The staged import has expired and its rows are no longer available.",
        )
    headers, _ = _worksheet(import_record, worksheet_name)
    stored_mapping = import_record.field_mapping or {}
    if (
        import_record.dataset_type == dataset_type
        and import_record.selected_worksheet == worksheet_name
    ):
        mapping = stored_mapping
        ignored_columns = import_record.ignored_columns
    else:
        mapping = suggest_mapping(headers, dataset_type)
        ignored_columns = []
    return _mapping_response(
        import_record, dataset_type, worksheet_name, headers, mapping, ignored_columns
    )


def get_import(
    session: Session, user: UserAccount, tenant_id: UUID, import_id: UUID
) -> ImportInspectionResponse:
    import_record = _get_import(session, user, tenant_id, import_id, write=False)
    inspection = _inspection_model(import_record)
    candidates: list[ImportDataset] = []
    for worksheet in inspection.worksheets:
        for dataset in suggested_datasets(worksheet.headers):
            if dataset not in candidates:
                candidates.append(dataset)
    return ImportInspectionResponse(
        import_record=_summary(import_record), inspection=inspection, suggested_datasets=candidates
    )


def list_imports(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
) -> ImportSummaryPage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [DataImport.tenant_id == tenant_id, DataImport.company_id == access.company.id]
    total = session.scalar(select(func.count()).select_from(DataImport).where(*filters)) or 0
    rows = session.scalars(
        select(DataImport)
        .where(*filters)
        .order_by(DataImport.created_at.desc(), DataImport.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return ImportSummaryPage(
        items=[_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


def purge_expired_imports(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    request_id: UUID,
    *,
    limit: int,
) -> ImportPurgeResult:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    now = datetime.now(UTC)
    expired_imports = list(
        session.scalars(
            select(DataImport)
            .where(
                DataImport.tenant_id == tenant_id,
                DataImport.company_id == access.company.id,
                DataImport.status.in_(STAGED_IMPORT_STATUSES),
                DataImport.staging_expires_at <= now,
            )
            .order_by(DataImport.staging_expires_at, DataImport.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    purged_rows = 0
    for import_record in expired_imports:
        row_filters = (
            DataImportRow.tenant_id == tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
        )
        import_rows_purged = (
            session.scalar(
                select(func.count())
                .select_from(DataImportRow)
                .where(*row_filters, DataImportRow.raw_values.is_not(None))
            )
            or 0
        )
        purged_rows += import_rows_purged
        session.execute(update(DataImportRow).where(*row_filters).values(raw_values=null()))
        import_record.status = "expired"
        record_event(
            session,
            "data_import.expired",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="data_import",
            target_id=import_record.id,
            request_id=request_id,
            details={
                "file_hash": import_record.file_hash,
                "source_rows_purged": import_rows_purged,
            },
        )
    session.flush()
    return ImportPurgeResult(imports_purged=len(expired_imports), source_rows_purged=purged_rows)


def set_import_mapping(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    request: ImportMappingRequest,
    request_id: UUID,
) -> ImportMappingResponse:
    import_record = _get_import(session, user, tenant_id, import_id, write=True, lock=True)
    _ensure_not_expired(session, user, import_record, request_id)
    if import_record.status not in {"inspected", "mapped", "validated", "ready"}:
        raise ApiError(
            409,
            "import_mapping_locked",
            "The mapping cannot be changed after confirmation or application.",
        )
    headers = _mapping_shape(import_record, request)
    # Store only a validated one-to-one map, plus explicit decisions to ignore source columns.
    import_record.dataset_type = request.dataset_type
    import_record.selected_worksheet = request.worksheet_name
    import_record.field_mapping = dict(request.field_mapping)
    import_record.ignored_columns = list(request.ignored_columns)
    import_record.mapping_version += 1
    import_record.validation_report = None
    import_record.validation_hash = None
    import_record.status = "mapped"
    import_record.total_rows = _worksheet(import_record, request.worksheet_name)[1]
    import_record.valid_rows = 0
    import_record.invalid_rows = 0
    import_record.warning_rows = 0
    import_record.duplicate_rows = 0
    import_record.eligible_rows = 0
    import_record.skipped_rows = 0
    import_record.created_rows = 0
    import_record.updated_rows = 0
    import_record.acknowledge_warnings = False
    import_record.allow_partial = False
    import_record.confirmed_by_user_id = None
    import_record.confirmed_validation_hash = None
    import_record.confirmed_at = None
    import_record.applied_by_user_id = None
    import_record.applied_at = None
    rows = session.scalars(
        select(DataImportRow).where(
            DataImportRow.tenant_id == tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
        )
    )
    for row in rows:
        row.issues = []
        row.row_status = "pending"
        row.canonical_type = None
        row.canonical_id = None
    record_event(
        session,
        "data_import.mapped",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={
            "dataset_type": request.dataset_type,
            "worksheet_name": request.worksheet_name,
            "mapping_version": import_record.mapping_version,
            "mapped_field_count": len(request.field_mapping),
            "ignored_column_count": len(request.ignored_columns),
        },
    )
    session.flush()
    return _mapping_response(
        import_record,
        request.dataset_type,
        request.worksheet_name,
        headers,
        request.field_mapping,
        request.ignored_columns,
    )


def _issue_counts(session: Session, import_record: DataImport) -> list[ImportIssueCount]:
    rows = session.scalars(
        select(DataImportRow).where(
            DataImportRow.tenant_id == import_record.tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
            DataImportRow.worksheet_name == import_record.selected_worksheet,
        )
    )
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        for issue in row.issues:
            if isinstance(issue, dict):
                counts[
                    (str(issue.get("code", "unknown")), str(issue.get("severity", "warning")))
                ] += 1
    return [
        ImportIssueCount(
            code=code, severity=cast(Literal["error", "warning"], severity), count=count
        )
        for (code, severity), count in sorted(counts.items())
        if severity in {"error", "warning"}
    ]


def get_import_report(
    session: Session, user: UserAccount, tenant_id: UUID, import_id: UUID
) -> ImportReport:
    import_record = _get_import(session, user, tenant_id, import_id, write=False)
    return ImportReport(
        import_record=_summary(import_record),
        inspection=_inspection_model(import_record),
        issue_counts=_issue_counts(session, import_record),
        validation_report=import_record.validation_report,
        retention_note=(
            "Staged row values are purged after a successful apply, cancellation, or expiry; "
            "row hashes, validation issues, canonical links, and the import report are retained."
        ),
    )


def _report_after_validation(session: Session, import_record: DataImport) -> ImportReport:
    return ImportReport(
        import_record=_summary(import_record),
        inspection=_inspection_model(import_record),
        issue_counts=_issue_counts(session, import_record),
        validation_report=import_record.validation_report,
        retention_note=(
            "Staged row values are purged after a successful apply, cancellation, or expiry; "
            "row hashes, validation issues, canonical links, and the import report are retained."
        ),
    )


def _validate_ready_import(
    session: Session, user: UserAccount, import_record: DataImport, request_id: UUID
) -> ValidationResult:
    _ensure_not_expired(session, user, import_record, request_id)
    if import_record.status not in {"mapped", "validated", "ready"}:
        raise ApiError(
            409,
            "import_not_validatable",
            "The import must have a saved mapping and not be confirmed or applied.",
        )
    result = validate_staged_import(session, import_record)
    record_event(
        session,
        "data_import.validated",
        tenant_id=import_record.tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={
            "validation_hash": result.validation_hash,
            "total_rows": result.total_rows,
            "valid_rows": result.valid_rows,
            "invalid_rows": result.invalid_rows,
            "warning_rows": result.warning_rows,
            "duplicate_rows": result.duplicate_rows,
            "eligible_rows": result.eligible_rows,
        },
    )
    session.flush()
    return result


def validate_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    request_id: UUID,
) -> ImportReport:
    import_record = _get_import(session, user, tenant_id, import_id, write=True, lock=True)
    _validate_ready_import(session, user, import_record, request_id)
    return _report_after_validation(session, import_record)


def _stale_validation(
    session: Session,
    user: UserAccount,
    import_record: DataImport,
    request_id: UUID,
    old_hash: str,
    new_hash: str,
) -> None:
    import_record.confirmed_by_user_id = None
    import_record.confirmed_validation_hash = None
    import_record.confirmed_at = None
    import_record.acknowledge_warnings = False
    import_record.allow_partial = False
    record_event(
        session,
        "data_import.validation_stale",
        tenant_id=import_record.tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        outcome="failure",
        details={"previous_validation_hash": old_hash, "current_validation_hash": new_hash},
    )
    session.flush()
    raise CommitAndRaise(
        ApiError(
            409,
            "import_validation_stale",
            (
                "The source, mapping, or referenced company data changed after validation. "
                "Review the refreshed report and confirm it again."
            ),
        )
    )


def confirm_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    request: ImportConfirmRequest,
    request_id: UUID,
) -> ImportSummary:
    import_record = _get_import(session, user, tenant_id, import_id, write=True, lock=True)
    _ensure_not_expired(session, user, import_record, request_id)
    if import_record.status not in {"validated", "ready"} or import_record.validation_hash is None:
        raise ApiError(
            409,
            "import_validation_required",
            "Validate the current mapping before confirming this import.",
        )
    previous_hash = import_record.validation_hash
    result = validate_staged_import(session, import_record)
    if result.validation_hash != previous_hash:
        _stale_validation(
            session, user, import_record, request_id, previous_hash, result.validation_hash
        )
    if request.validation_hash != result.validation_hash:
        raise ApiError(
            409,
            "validation_hash_mismatch",
            "The confirmation hash does not match the current validation report.",
        )
    if result.invalid_rows and not request.allow_partial:
        raise ApiError(
            409,
            "invalid_rows_require_partial",
            (
                "The import contains invalid rows; explicitly allow a partial apply or "
                "correct the source first."
            ),
        )
    if result.warning_rows and not request.acknowledge_warnings:
        raise ApiError(
            409,
            "warnings_require_acknowledgement",
            "Acknowledge the current warnings before confirming this import.",
        )
    if result.eligible_rows == 0 and result.duplicate_rows == 0:
        raise ApiError(
            409,
            "no_eligible_rows",
            "There are no valid rows or traceable duplicates to apply; all rows are invalid.",
        )
    import_record.status = "confirmed"
    import_record.confirmed_by_user_id = user.id
    import_record.confirmed_validation_hash = result.validation_hash
    import_record.confirmed_at = datetime.now(UTC)
    import_record.allow_partial = request.allow_partial
    import_record.acknowledge_warnings = request.acknowledge_warnings
    record_event(
        session,
        "data_import.confirmed",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={
            "validation_hash": result.validation_hash,
            "allow_partial": request.allow_partial,
            "acknowledge_warnings": request.acknowledge_warnings,
            "eligible_rows": result.eligible_rows,
            "invalid_rows": result.invalid_rows,
            "duplicate_rows": result.duplicate_rows,
        },
    )
    session.flush()
    return _summary(import_record)


def _create_canonical_record(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    plan: PlannedRow,
    request_id: UUID,
) -> CanonicalRecord:
    candidate = plan.candidate
    if candidate is None:
        raise ApiError(
            409, "import_plan_invalid", "A row marked eligible no longer has a validated payload."
        )
    body = candidate.body
    if candidate.canonical_type == "employee" and isinstance(body, EmployeeCreate):
        return create_employee(session, user, tenant_id, body, request_id).employee
    if candidate.canonical_type == "skill" and isinstance(body, SkillCreate):
        return create_skill(session, user, tenant_id, body, request_id)
    if candidate.canonical_type == "employee_skill" and isinstance(body, EmployeeSkillCreate):
        employee = next((item for item in candidate.references if isinstance(item, Employee)), None)
        if employee is None:
            raise ApiError(
                409, "import_plan_invalid", "The employee reference is unavailable; validate again."
            )
        return create_employee_skill(
            session, user, tenant_id, employee.id, body, request_id
        ).employee_skill
    if candidate.canonical_type == "weekly_capacity" and isinstance(body, WeeklyCapacityCreate):
        employee = next((item for item in candidate.references if isinstance(item, Employee)), None)
        if employee is None:
            raise ApiError(
                409, "import_plan_invalid", "The employee reference is unavailable; validate again."
            )
        week_start = cast(date, candidate.lookup_key[1])
        return record_weekly_capacity(
            session, user, tenant_id, employee.id, week_start, body, request_id
        )
    if candidate.canonical_type == "project" and isinstance(body, ProjectCreate):
        return create_project(session, user, tenant_id, body, request_id)
    if candidate.canonical_type == "resource_assignment" and isinstance(
        body, ResourceAssignmentCreate
    ):
        project = next((item for item in candidate.references if isinstance(item, Project)), None)
        if project is None:
            raise ApiError(
                409, "import_plan_invalid", "The project reference is unavailable; validate again."
            )
        return create_resource_assignment(session, user, tenant_id, project.id, body, request_id)
    raise ApiError(
        409, "import_plan_invalid", "The validated row type is inconsistent; validate again."
    )


def _target_for_duplicate(
    plan: PlannedRow, targets: dict[tuple[str, ...], CanonicalRecord]
) -> CanonicalRecord | None:
    if plan.existing_entity is not None:
        return cast(CanonicalRecord, plan.existing_entity)
    if plan.candidate is not None:
        return targets.get(plan.candidate.identity)
    return None


def _lock_apply_references(
    session: Session, import_record: DataImport, plans: list[PlannedRow]
) -> bool:
    if import_record.dataset_type not in {"weekly_capacity", "resource_assignments"}:
        return False
    project_ids: set[UUID] = set()
    employee_ids: set[UUID] = set()
    for plan in plans:
        if plan.candidate is None:
            continue
        for reference in plan.candidate.references:
            if isinstance(reference, Project):
                project_ids.add(reference.id)
            elif isinstance(reference, Employee):
                employee_ids.add(reference.id)

    if project_ids:
        session.scalars(
            select(Project)
            .where(
                Project.tenant_id == import_record.tenant_id,
                Project.company_id == import_record.company_id,
                Project.id.in_(project_ids),
            )
            .order_by(Project.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    if employee_ids:
        session.scalars(
            select(Employee)
            .where(
                Employee.tenant_id == import_record.tenant_id,
                Employee.company_id == import_record.company_id,
                Employee.id.in_(employee_ids),
            )
            .order_by(Employee.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    return bool(project_ids or employee_ids)


def apply_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    request_id: UUID,
) -> ImportApplyResult:
    import_record = _get_import(session, user, tenant_id, import_id, write=True, lock=True)
    _ensure_not_expired(session, user, import_record, request_id)
    if import_record.status in {"applied", "applied_with_warnings"}:
        links_created = (
            session.scalar(
                select(func.count())
                .select_from(DataImportRecordLink)
                .where(
                    DataImportRecordLink.tenant_id == tenant_id,
                    DataImportRecordLink.company_id == import_record.company_id,
                    DataImportRecordLink.import_id == import_record.id,
                )
            )
            or 0
        )
        return ImportApplyResult(
            import_record=_summary(import_record), links_created=links_created, replayed=True
        )
    if import_record.status != "confirmed" or import_record.confirmed_validation_hash is None:
        raise ApiError(
            409,
            "import_confirmation_required",
            "Explicitly confirm the current validation report before applying.",
        )
    confirmed_hash = import_record.confirmed_validation_hash
    stored_hash = import_record.validation_hash
    if stored_hash is None or stored_hash != confirmed_hash:
        raise ApiError(
            409,
            "import_validation_stale",
            "The confirmed validation snapshot is stale; validate and confirm again.",
        )
    result = validate_staged_import(session, import_record)
    if result.validation_hash != confirmed_hash:
        _stale_validation(
            session, user, import_record, request_id, confirmed_hash, result.validation_hash
        )
    if _lock_apply_references(session, import_record, result.rows):
        result = validate_staged_import(session, import_record)
        if result.validation_hash != confirmed_hash:
            _stale_validation(
                session, user, import_record, request_id, confirmed_hash, result.validation_hash
            )
    if result.invalid_rows and not import_record.allow_partial:
        raise ApiError(
            409,
            "invalid_rows_require_partial",
            "The confirmed report does not authorize a partial apply.",
        )
    if result.warning_rows and not import_record.acknowledge_warnings:
        raise ApiError(
            409,
            "warnings_require_acknowledgement",
            "The confirmed report does not acknowledge warnings.",
        )

    targets: dict[tuple[str, ...], CanonicalRecord] = {}
    created = 0
    skipped = 0
    links_created = 0
    for plan in result.rows:
        if plan.status == "invalid":
            plan.source_row.row_status = "skipped"
            skipped += 1
            continue
        if plan.status == "duplicate":
            entity = _target_for_duplicate(plan, targets)
            if entity is None:
                raise ApiError(
                    409,
                    "duplicate_target_unresolved",
                    "A duplicate row could not be traced to its canonical record; "
                    "no rows were applied.",
                )
            canonical_id = entity.id
        else:
            entity = _create_canonical_record(session, user, tenant_id, plan, request_id)
            canonical_id = entity.id
            plan.source_row.row_status = "applied"
            created += 1
        if plan.candidate is None:
            raise ApiError(
                409, "import_plan_invalid", "The row plan is incomplete; no rows were applied."
            )
        targets.setdefault(plan.candidate.identity, entity)
        plan.source_row.canonical_type = plan.candidate.canonical_type
        plan.source_row.canonical_id = canonical_id
        if plan.status == "duplicate":
            plan.source_row.row_status = "skipped"
            skipped += 1
        session.add(
            DataImportRecordLink(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=import_record.company_id,
                import_id=import_record.id,
                source_row_id=plan.source_row.id,
                canonical_type=plan.candidate.canonical_type,
                canonical_id=canonical_id,
                canonical_fingerprint=_entity_fingerprint(entity),
            )
        )
        links_created += 1

    now = datetime.now(UTC)
    import_record.applied_by_user_id = user.id
    import_record.applied_at = now
    import_record.created_rows = created
    import_record.updated_rows = 0
    import_record.skipped_rows = skipped
    import_record.status = (
        "applied_with_warnings"
        if result.warning_rows or result.invalid_rows or result.duplicate_rows
        else "applied"
    )
    session.execute(
        update(DataImportRow)
        .where(
            DataImportRow.tenant_id == tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
        )
        .values(raw_values=null())
    )
    record_event(
        session,
        "data_import.applied",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={
            "validation_hash": result.validation_hash,
            "created_rows": created,
            "updated_rows": 0,
            "skipped_rows": skipped,
            "link_count": links_created,
            "invalid_rows": result.invalid_rows,
            "duplicate_rows": result.duplicate_rows,
        },
    )
    session.flush()
    return ImportApplyResult(import_record=_summary(import_record), links_created=links_created)


def preview_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    *,
    worksheet_name: str | None,
    limit: int,
    offset: int,
) -> ImportPreviewPage:
    import_record = _get_import(session, user, tenant_id, import_id, write=False)
    if import_record.status == "expired" or (
        import_record.status in STAGED_IMPORT_STATUSES
        and import_record.staging_expires_at <= datetime.now(UTC)
    ):
        raise ApiError(
            410,
            "import_staging_expired",
            "The staged row values have expired and are no longer available.",
        )
    if worksheet_name is None:
        worksheet_name = import_record.selected_worksheet
        if worksheet_name is None:
            worksheets = import_record.inspection.get("worksheets", [])
            if (
                not isinstance(worksheets, list)
                or not worksheets
                or not isinstance(worksheets[0], dict)
            ):
                raise ApiError(
                    409, "import_snapshot_invalid", "The staged worksheet metadata is unavailable."
                )
            worksheet_name = str(worksheets[0].get("name", ""))
    headers, _ = _worksheet(import_record, worksheet_name)
    filters = (
        DataImportRow.tenant_id == tenant_id,
        DataImportRow.company_id == import_record.company_id,
        DataImportRow.import_id == import_record.id,
        DataImportRow.worksheet_name == worksheet_name,
    )
    total = session.scalar(select(func.count()).select_from(DataImportRow).where(*filters)) or 0
    source_rows = session.scalars(
        select(DataImportRow)
        .where(*filters)
        .order_by(DataImportRow.row_number, DataImportRow.id)
        .limit(limit)
        .offset(offset)
    )
    previews: list[ImportPreviewRow] = []
    for row in source_rows:
        raw_values = row.raw_values
        source_values = None
        if raw_values is not None and len(raw_values) == len(headers):
            source_values = {
                header: cast(str | None, raw_values[index]) for index, header in enumerate(headers)
            }
        mapped_values: dict[str, object] | None = None
        if (
            source_values is not None
            and import_record.dataset_type is not None
            and import_record.selected_worksheet == worksheet_name
            and import_record.field_mapping is not None
        ):
            mapped_values = {
                field_name: source_values.get(source_header)
                for field_name, source_header in import_record.field_mapping.items()
            }
        previews.append(
            ImportPreviewRow(
                row_number=row.row_number,
                worksheet_name=row.worksheet_name,
                row_hash=row.row_hash,
                row_status=row.row_status,
                source_values=source_values,
                mapped_values=mapped_values,
                canonical_type=row.canonical_type,
                canonical_id=row.canonical_id,
                issues=[ImportRowIssue.model_validate(issue) for issue in row.issues],
            )
        )
    return ImportPreviewPage(
        items=previews,
        total=total,
        limit=limit,
        offset=offset,
        counts=ImportCounts(
            total_rows=import_record.total_rows,
            valid_rows=import_record.valid_rows,
            invalid_rows=import_record.invalid_rows,
            warning_rows=import_record.warning_rows,
            duplicate_rows=import_record.duplicate_rows,
            eligible_rows=import_record.eligible_rows,
            skipped_rows=import_record.skipped_rows,
            created_rows=import_record.created_rows,
            updated_rows=import_record.updated_rows,
        ),
    )


def cancel_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    request_id: UUID,
) -> ImportSummary:
    import_record = _get_import(session, user, tenant_id, import_id, write=True, lock=True)
    _ensure_not_expired(session, user, import_record, request_id)
    if import_record.status in TERMINAL_IMPORT_STATUSES:
        raise ApiError(409, "import_terminal", "A terminal import cannot be cancelled or replayed.")
    import_record.status = "cancelled"
    import_record.confirmed_by_user_id = None
    import_record.confirmed_validation_hash = None
    import_record.confirmed_at = None
    session.execute(
        update(DataImportRow)
        .where(
            DataImportRow.tenant_id == tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
        )
        .values(raw_values=null())
    )
    record_event(
        session,
        "data_import.cancelled",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="data_import",
        target_id=import_record.id,
        request_id=request_id,
        details={"file_hash": import_record.file_hash},
    )
    session.flush()
    return _summary(import_record)
