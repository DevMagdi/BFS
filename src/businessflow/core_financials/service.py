from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import BinaryIO, Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.calculation_core.decimal import parse_output_decimal_text, validate_currency_code
from businessflow.config import Settings
from businessflow.core_financials.adapters import (
    NormalizedSourceRow,
    PriorCanonicalRecord,
    normalize_source_rows,
    validate_source_headers,
)
from businessflow.core_financials.calculations import (
    CanonicalFinancialFact,
    FinancialRunLine,
    SourceImportState,
    calculate_financial_metrics,
)
from businessflow.core_financials.catalog import (
    CORE_FINANCIAL_ENGINE_VERSION,
    CORE_FINANCIAL_MAX_ROWS,
    ILLUSTRATIVE_POLICY_DISCLAIMER,
    LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    illustrative_policy_template,
)
from businessflow.core_financials.contracts import (
    BANK_FIELDS,
    FINANCIAL_MEASURES,
    LEDGER_FIELDS,
    CoreFinancialAnalysisStatement,
    CoreFinancialImportMetadata,
    CoreFinancialImportPage,
    CoreFinancialImportResponse,
    CoreFinancialMappingCreateRequest,
    CoreFinancialMappingPage,
    CoreFinancialMappingResponse,
    CoreFinancialMetricResult,
    CoreFinancialOverviewMeasure,
    CoreFinancialOverviewResponse,
    CoreFinancialOverviewSource,
    CoreFinancialPolicyCreateRequest,
    CoreFinancialPolicyPage,
    CoreFinancialPolicyResponse,
    CoreFinancialRecordPage,
    CoreFinancialRecordResponse,
    CoreFinancialRunCreateRequest,
    CoreFinancialRunEvidencePage,
    CoreFinancialRunEvidenceResponse,
    CoreFinancialRunPage,
    CoreFinancialRunResponse,
    CoreFinancialRunVerificationResponse,
    CoreFinancialTemplateResponse,
    FinancialCategory,
    FinancialMeasure,
    SourceType,
)
from businessflow.core_financials.models import (
    CoreFinancialImport,
    CoreFinancialMapping,
    CoreFinancialPolicy,
    CoreFinancialRecord,
    CoreFinancialRun,
    CoreFinancialRunEvidence,
)
from businessflow.core_financials.overview import build_overview_checks
from businessflow.data_imports.parsers import (
    ImportFileError,
    parse_file,
    read_limited_upload,
)
from businessflow.errors import ApiError
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._~:-]{16,128}$")
MAX_PAGE_SIZE = 100
MAX_PAGE_OFFSET = 100_000
SNAPSHOT_SCHEMA_VERSION = 1


def ensure_core_financials_enabled(settings: Settings | None) -> None:
    """Fail closed unless the explicitly default-disabled non-production slice is enabled."""
    if (
        getattr(settings, "core_financials_enabled", None) is not True
        or getattr(settings, "app_env", None) == "production"
    ):
        raise ApiError(
            503,
            "core_financials_disabled",
            "Core Financials is disabled by deployment configuration.",
        )


def _not_found(resource: str) -> ApiError:
    return ApiError(
        404,
        f"core_financial_{resource}_not_found",
        f"{resource.replace('_', ' ').title()} was not found.",
    )


def _integrity_error() -> ApiError:
    return ApiError(
        409,
        "core_financial_evidence_unverifiable",
        "The referenced financial evidence failed an integrity check.",
    )


def _canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Core Financials timestamps must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_file_name(filename: str | None) -> str:
    if not filename:
        raise ApiError(422, "invalid_file_name", "A CSV file name is required.")
    leaf = filename.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[\x00-\x1f\x7f]", "_", leaf).strip(" .")
    if not safe or len(safe) > 255:
        raise ApiError(422, "invalid_file_name", "The file name is invalid or too long.")
    if PurePosixPath(safe).suffix.casefold() != ".csv":
        raise ApiError(415, "unsupported_file", "Core Financials accepts UTF-8 CSV files only.")
    return safe


def _lock_for_key(
    session: Session,
    *,
    namespace: str,
    tenant_id: UUID,
    company_id: UUID,
    key: str,
) -> None:
    material = b"\0".join(
        (namespace.encode("utf-8"), tenant_id.bytes, company_id.bytes, key.encode("utf-8"))
    )
    lock_key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _policy_hash(definition: CoreFinancialPolicyCreateRequest) -> str:
    # Excluding null values preserves hashes for immutable v2 policies, which predate the
    # explicitly versioned Cash Flow rule object.
    return content_hash(definition.model_dump(mode="json", exclude_none=True))


def _mapping_hash(request: CoreFinancialMappingCreateRequest) -> str:
    ignored = sorted(
        (item.model_dump(mode="json") for item in request.ignored_columns),
        key=lambda item: item["header"],
    )
    return content_hash(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "source_key": request.source_key,
            "source_type": request.source_type,
            "field_mapping": request.field_mapping,
            "ignored_columns": ignored,
        }
    )


def _verify_policy(row: CoreFinancialPolicy) -> CoreFinancialPolicyCreateRequest:
    try:
        definition = CoreFinancialPolicyCreateRequest.model_validate(row.definition)
    except ValidationError:
        raise _integrity_error() from None
    if (
        row.authority != "illustrative_not_authoritative"
        or definition.policy_key != row.policy_key
        or _policy_hash(definition) != row.definition_hash
        or row.version < 1
    ):
        raise _integrity_error()
    return definition


def _verify_mapping(row: CoreFinancialMapping) -> CoreFinancialMappingCreateRequest:
    try:
        request = CoreFinancialMappingCreateRequest.model_validate(
            {
                "source_key": row.source_key,
                "source_type": row.source_type,
                "field_mapping": row.field_mapping,
                "ignored_columns": row.ignored_columns,
            }
        )
    except ValidationError:
        raise _integrity_error() from None
    if row.version < 1 or _mapping_hash(request) != row.mapping_hash:
        raise _integrity_error()
    return request


def _policy_response(row: CoreFinancialPolicy) -> CoreFinancialPolicyResponse:
    definition = _verify_policy(row)
    return CoreFinancialPolicyResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        created_by_user_id=row.created_by_user_id,
        policy_key=row.policy_key,
        version=row.version,
        authority="illustrative_not_authoritative",
        accounting_policy_status="not_established",
        definition_hash=row.definition_hash,
        definition=definition,
        created_at=row.created_at,
    )


def _mapping_response(row: CoreFinancialMapping) -> CoreFinancialMappingResponse:
    request = _verify_mapping(row)
    return CoreFinancialMappingResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        created_by_user_id=row.created_by_user_id,
        source_key=row.source_key,
        source_type=cast(SourceType, row.source_type),
        version=row.version,
        field_mapping=request.field_mapping,
        ignored_columns=request.ignored_columns,
        mapping_hash=row.mapping_hash,
        created_at=row.created_at,
    )


def get_core_financial_template(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    settings: Settings,
) -> CoreFinancialTemplateResponse:
    ensure_core_financials_enabled(settings)
    require_company_context_access(session, user, tenant_id, "finance:read")
    return CoreFinancialTemplateResponse(
        illustrative_policy=illustrative_policy_template(),
        policy_disclaimer=ILLUSTRATIVE_POLICY_DISCLAIMER,
        general_ledger_headers=LEDGER_FIELDS,
        bank_activity_headers=BANK_FIELDS,
    )


def create_core_financial_policy(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    request: CoreFinancialPolicyCreateRequest,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> CoreFinancialPolicyResponse:
    ensure_core_financials_enabled(settings)
    now = datetime.fromisoformat(_canonical_datetime(now).replace("Z", "+00:00"))
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    definition_payload = request.model_dump(mode="json")
    definition_hash = _policy_hash(request)
    _lock_for_key(
        session,
        namespace="core-financial-policy-version",
        tenant_id=tenant_id,
        company_id=access.company.id,
        key=request.policy_key,
    )
    latest = session.scalar(
        select(CoreFinancialPolicy)
        .where(
            CoreFinancialPolicy.tenant_id == tenant_id,
            CoreFinancialPolicy.company_id == access.company.id,
            CoreFinancialPolicy.policy_key == request.policy_key,
        )
        .order_by(CoreFinancialPolicy.version.desc())
        .limit(1)
    )
    if latest is not None:
        _verify_policy(latest)
        if latest.definition_hash == definition_hash:
            return _policy_response(latest)
    version = latest.version + 1 if latest is not None else 1
    row = CoreFinancialPolicy(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        policy_key=request.policy_key,
        version=version,
        authority="illustrative_not_authoritative",
        definition_hash=definition_hash,
        definition=definition_payload,
        created_at=now,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "core_financials.policy_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="core_financial_policy",
        target_id=row.id,
        request_id=request_id,
        details={
            "policy_key": row.policy_key,
            "version": row.version,
            "definition_hash": row.definition_hash,
            "authority": row.authority,
        },
    )
    return _policy_response(row)


def list_core_financial_policies(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> CoreFinancialPolicyPage:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    filters = (
        CoreFinancialPolicy.tenant_id == tenant_id,
        CoreFinancialPolicy.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(CoreFinancialPolicy).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(CoreFinancialPolicy)
            .where(*filters)
            .order_by(CoreFinancialPolicy.policy_key, CoreFinancialPolicy.version.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CoreFinancialPolicyPage(
        items=[_policy_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def create_core_financial_mapping(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    request: CoreFinancialMappingCreateRequest,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> CoreFinancialMappingResponse:
    ensure_core_financials_enabled(settings)
    now = datetime.fromisoformat(_canonical_datetime(now).replace("Z", "+00:00"))
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    mapping_hash = _mapping_hash(request)
    _lock_for_key(
        session,
        namespace="core-financial-mapping-version",
        tenant_id=tenant_id,
        company_id=access.company.id,
        key=f"{request.source_key}:{request.source_type}",
    )
    latest = session.scalar(
        select(CoreFinancialMapping)
        .where(
            CoreFinancialMapping.tenant_id == tenant_id,
            CoreFinancialMapping.company_id == access.company.id,
            CoreFinancialMapping.source_key == request.source_key,
            CoreFinancialMapping.source_type == request.source_type,
        )
        .order_by(CoreFinancialMapping.version.desc())
        .limit(1)
    )
    if latest is not None:
        _verify_mapping(latest)
        if latest.mapping_hash == mapping_hash:
            return _mapping_response(latest)
    version = latest.version + 1 if latest is not None else 1
    row = CoreFinancialMapping(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        source_key=request.source_key,
        source_type=request.source_type,
        version=version,
        mapping_hash=mapping_hash,
        field_mapping=request.field_mapping,
        ignored_columns=[item.model_dump(mode="json") for item in request.ignored_columns],
        created_at=now,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "core_financials.mapping_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="core_financial_mapping",
        target_id=row.id,
        request_id=request_id,
        details={
            "source_key": row.source_key,
            "source_type": row.source_type,
            "version": row.version,
            "mapping_hash": row.mapping_hash,
        },
    )
    return _mapping_response(row)


def list_core_financial_mappings(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    source_key: str | None,
    source_type: SourceType | None,
    settings: Settings,
) -> CoreFinancialMappingPage:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    filters = [
        CoreFinancialMapping.tenant_id == tenant_id,
        CoreFinancialMapping.company_id == access.company.id,
    ]
    if source_key is not None:
        filters.append(CoreFinancialMapping.source_key == source_key)
    if source_type is not None:
        filters.append(CoreFinancialMapping.source_type == source_type)
    total = (
        session.scalar(select(func.count()).select_from(CoreFinancialMapping).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(CoreFinancialMapping)
            .where(*filters)
            .order_by(
                CoreFinancialMapping.source_key,
                CoreFinancialMapping.source_type,
                CoreFinancialMapping.version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return CoreFinancialMappingPage(
        items=[_mapping_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _build_import_input_snapshot(
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_key: str,
    source_type: SourceType,
    period_start: date,
    period_end: date,
    period_completeness: str,
    file_name: str,
    file_hash: str,
    file_size_bytes: int,
    row_count: int,
    blank_rows_ignored: int,
    mapping: CoreFinancialMapping,
    policy: CoreFinancialPolicy,
    source_row_hashes: list[dict[str, object]],
    engine_version: str = CORE_FINANCIAL_ENGINE_VERSION,
) -> dict[str, object]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "engine_version": engine_version,
        "scope": {"tenant_id": str(tenant_id), "company_id": str(company_id)},
        "source": {
            "source_key": source_key,
            "source_type": source_type,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "period_completeness": period_completeness,
            "completeness_authority": "caller_asserted_not_verified",
        },
        "file": {
            "file_name": file_name,
            "format": "csv",
            "file_hash": file_hash,
            "file_size_bytes": file_size_bytes,
            "row_count": row_count,
            "blank_rows_ignored": blank_rows_ignored,
        },
        "mapping": {
            "id": str(mapping.id),
            "source_key": mapping.source_key,
            "source_type": mapping.source_type,
            "version": mapping.version,
            "mapping_hash": mapping.mapping_hash,
            "field_mapping": mapping.field_mapping,
            "ignored_columns": mapping.ignored_columns,
        },
        "policy": {
            "id": str(policy.id),
            "policy_key": policy.policy_key,
            "version": policy.version,
            "definition_hash": policy.definition_hash,
            "definition": policy.definition,
            "authority": policy.authority,
        },
        "source_row_hashes": source_row_hashes,
    }


def _import_input_snapshot(
    row: CoreFinancialImport,
    mapping: CoreFinancialMapping,
    policy: CoreFinancialPolicy,
    records: list[CoreFinancialRecord],
) -> dict[str, object]:
    stored_file = row.input_snapshot.get("file") if isinstance(row.input_snapshot, dict) else None
    blank_rows = stored_file.get("blank_rows_ignored") if isinstance(stored_file, dict) else None
    engine_version = row.input_snapshot.get("engine_version")
    if (
        isinstance(blank_rows, bool)
        or not isinstance(blank_rows, int)
        or blank_rows < 0
        or engine_version
        not in (LEGACY_CORE_FINANCIAL_ENGINE_VERSION, CORE_FINANCIAL_ENGINE_VERSION)
    ):
        raise _integrity_error()
    return _build_import_input_snapshot(
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        source_key=row.source_key,
        source_type=cast(SourceType, row.source_type),
        period_start=row.period_start,
        period_end=row.period_end,
        period_completeness=row.period_completeness,
        file_name=row.file_name,
        file_hash=row.file_hash,
        file_size_bytes=row.file_size_bytes,
        row_count=row.total_rows,
        blank_rows_ignored=blank_rows,
        mapping=mapping,
        policy=policy,
        source_row_hashes=[
            {"row_number": record.source_row_number, "row_hash": record.source_row_hash}
            for record in records
        ],
        engine_version=engine_version,
    )


def _record_snapshot(record: CoreFinancialRecord) -> dict[str, object]:
    return {
        "row_number": record.source_row_number,
        "source_record_id": record.source_record_id,
        "transaction_date": record.transaction_date.isoformat()
        if record.transaction_date
        else None,
        "currency_code": record.currency_code,
        "account_code": record.account_code,
        "category": record.category,
        "event_type": record.event_type,
        "signed_amount": record.signed_amount,
        "reverses_record_id": record.reverses_record_id,
        "canonical_record_key": record.canonical_record_key,
        "source_row_hash": record.source_row_hash,
        "row_status": record.row_status,
        "issues": list(record.issues),
    }


def _normalized_row_snapshot(
    row: NormalizedSourceRow, canonical_record_key: str | None
) -> dict[str, object]:
    return {
        "row_number": row.row_number,
        "source_record_id": row.source_record_id,
        "transaction_date": row.transaction_date.isoformat() if row.transaction_date else None,
        "currency_code": row.currency_code,
        "account_code": row.account_code,
        "category": row.category,
        "event_type": row.event_type,
        "signed_amount": row.signed_amount,
        "reverses_record_id": row.reverses_record_id,
        "canonical_record_key": canonical_record_key,
        "source_row_hash": row.source_row_hash,
        "row_status": row.row_status,
        "issues": list(row.issues),
    }


def _counts_for_rows(rows: list[dict[str, object]]) -> dict[str, int]:
    return {
        "total_rows": len(rows),
        "accepted_rows": sum(item["row_status"] == "accepted" for item in rows),
        "rejected_rows": sum(item["row_status"] == "rejected" for item in rows),
        "duplicate_rows": sum(item["row_status"] == "duplicate" for item in rows),
    }


def _import_result_snapshot(
    rows: list[dict[str, object]],
    *,
    quality_status: str,
    engine_version: str = CORE_FINANCIAL_ENGINE_VERSION,
) -> dict[str, object]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "engine_version": engine_version,
        "counts": _counts_for_rows(rows),
        "quality_status": quality_status,
        "rows": rows,
    }


def _canonical_record_key(
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_key: str,
    source_type: SourceType,
    source_record_id: str,
) -> str:
    return content_hash(
        {
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "source_key": source_key,
            "source_type": source_type,
            "source_record_id": source_record_id,
        }
    )


def _recomputed_source_row_hash(
    record: CoreFinancialRecord,
    source_import: CoreFinancialImport,
) -> str:
    values = record.mapped_source_values
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or (value is not None and not isinstance(value, str))
        for key, value in values.items()
    ):
        raise _integrity_error()
    return content_hash(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "source_key": source_import.source_key,
            "source_type": source_import.source_type,
            "file_hash": source_import.file_hash,
            "mapping_hash": source_import.mapping_hash,
            "policy_hash": source_import.policy_hash,
            "source_period_start": source_import.period_start.isoformat(),
            "source_period_end": source_import.period_end.isoformat(),
            "source_row_number": record.source_row_number,
            "mapped_source_values": values,
        }
    )


def _verify_import_integrity(
    session: Session,
    source_import: CoreFinancialImport,
) -> tuple[CoreFinancialMapping, CoreFinancialPolicy, list[CoreFinancialRecord]]:
    if (
        not isinstance(source_import.input_snapshot, dict)
        or not isinstance(source_import.result_snapshot, dict)
        or content_hash(source_import.input_snapshot) != source_import.input_hash
        or content_hash(source_import.result_snapshot) != source_import.result_hash
        or source_import.input_snapshot.get("engine_version")
        not in (LEGACY_CORE_FINANCIAL_ENGINE_VERSION, CORE_FINANCIAL_ENGINE_VERSION)
        or source_import.result_snapshot.get("engine_version")
        != source_import.input_snapshot.get("engine_version")
    ):
        raise _integrity_error()
    mapping = session.scalar(
        select(CoreFinancialMapping).where(
            CoreFinancialMapping.id == source_import.mapping_id,
            CoreFinancialMapping.tenant_id == source_import.tenant_id,
            CoreFinancialMapping.company_id == source_import.company_id,
        )
    )
    policy = session.scalar(
        select(CoreFinancialPolicy).where(
            CoreFinancialPolicy.id == source_import.policy_id,
            CoreFinancialPolicy.tenant_id == source_import.tenant_id,
            CoreFinancialPolicy.company_id == source_import.company_id,
        )
    )
    if mapping is None or policy is None:
        raise _integrity_error()
    mapping_request = _verify_mapping(mapping)
    policy_definition = _verify_policy(policy)
    if (
        mapping.source_key != source_import.source_key
        or mapping.source_type != source_import.source_type
        or mapping.version != source_import.mapping_version
        or mapping.mapping_hash != source_import.mapping_hash
        or policy.version != source_import.policy_version
        or policy.definition_hash != source_import.policy_hash
        or source_import.file_format != "csv"
        or source_import.file_size_bytes <= 0
        or source_import.period_end < source_import.period_start
        or (
            source_import.source_type == "general_ledger"
            and mapping_request.source_type != "general_ledger"
        )
        or (
            source_import.source_type == "bank_activity"
            and mapping_request.source_type != "bank_activity"
        )
        or policy_definition.policy_key != policy.policy_key
    ):
        raise _integrity_error()
    records = list(
        session.scalars(
            select(CoreFinancialRecord)
            .where(
                CoreFinancialRecord.tenant_id == source_import.tenant_id,
                CoreFinancialRecord.company_id == source_import.company_id,
                CoreFinancialRecord.import_id == source_import.id,
            )
            .order_by(CoreFinancialRecord.source_row_number)
        )
    )
    for record in records:
        if _recomputed_source_row_hash(record, source_import) != record.source_row_hash:
            raise _integrity_error()
        expected_key = (
            _canonical_record_key(
                tenant_id=source_import.tenant_id,
                company_id=source_import.company_id,
                source_key=source_import.source_key,
                source_type=cast(SourceType, source_import.source_type),
                source_record_id=record.source_record_id,
            )
            if record.row_status == "accepted" and record.source_record_id is not None
            else None
        )
        if record.canonical_record_key != expected_key:
            raise _integrity_error()
        if record.row_status == "accepted":
            if (
                record.transaction_date is None
                or record.currency_code is None
                or record.event_type is None
                or record.signed_amount is None
                or record.source_record_id is None
                or record.account_code is None
                or not source_import.period_start
                <= record.transaction_date
                <= source_import.period_end
                or (source_import.source_type == "general_ledger" and record.category is None)
                or (source_import.source_type == "bank_activity" and record.category is not None)
            ):
                raise _integrity_error()
            if len(record.signed_amount) > 65:
                raise _integrity_error()
            try:
                parse_output_decimal_text(record.signed_amount)
                validate_currency_code(record.currency_code)
            except ValueError:
                raise _integrity_error() from None
    if len(records) != source_import.total_rows:
        raise _integrity_error()
    summaries = [_record_snapshot(record) for record in records]
    counts = _counts_for_rows(summaries)
    quality_status = (
        "passed" if counts["rejected_rows"] == 0 and counts["duplicate_rows"] == 0 else "incomplete"
    )
    engine_version = cast(str, source_import.input_snapshot["engine_version"])
    expected_result = _import_result_snapshot(
        summaries, quality_status=quality_status, engine_version=engine_version
    )
    expected_input = _import_input_snapshot(source_import, mapping, policy, records)
    if (
        counts["total_rows"] != source_import.total_rows
        or counts["accepted_rows"] != source_import.accepted_rows
        or counts["rejected_rows"] != source_import.rejected_rows
        or counts["duplicate_rows"] != source_import.duplicate_rows
        or quality_status != source_import.quality_status
        or expected_input != source_import.input_snapshot
        or expected_result != source_import.result_snapshot
    ):
        raise _integrity_error()
    return mapping, policy, records


def _import_response(
    row: CoreFinancialImport, *, replayed: bool = False
) -> CoreFinancialImportResponse:
    return CoreFinancialImportResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        requested_by_user_id=row.requested_by_user_id,
        source_key=row.source_key,
        source_type=cast(SourceType, row.source_type),
        file_name=row.file_name,
        file_hash=row.file_hash,
        file_size_bytes=row.file_size_bytes,
        mapping_id=row.mapping_id,
        mapping_version=row.mapping_version,
        mapping_hash=row.mapping_hash,
        policy_id=row.policy_id,
        policy_version=row.policy_version,
        policy_hash=row.policy_hash,
        period_start=row.period_start,
        period_end=row.period_end,
        period_completeness=cast(
            Literal["complete", "partial", "unknown"], row.period_completeness
        ),
        completeness_authority="caller_asserted_not_verified",
        total_rows=row.total_rows,
        accepted_rows=row.accepted_rows,
        rejected_rows=row.rejected_rows,
        duplicate_rows=row.duplicate_rows,
        quality_status=cast(Literal["passed", "incomplete"], row.quality_status),
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        created_at=row.created_at,
        replayed=replayed,
    )


def _prior_source_records(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_key: str,
    source_type: SourceType,
    policy_id: UUID,
) -> tuple[set[str], dict[str, PriorCanonicalRecord], set[str]]:
    query = (
        select(CoreFinancialRecord, CoreFinancialImport)
        .join(CoreFinancialImport, CoreFinancialRecord.import_id == CoreFinancialImport.id)
        .where(
            CoreFinancialRecord.tenant_id == tenant_id,
            CoreFinancialRecord.company_id == company_id,
            CoreFinancialImport.tenant_id == tenant_id,
            CoreFinancialImport.company_id == company_id,
            CoreFinancialImport.source_key == source_key,
            CoreFinancialImport.source_type == source_type,
            CoreFinancialRecord.row_status == "accepted",
            CoreFinancialRecord.source_record_id.is_not(None),
        )
    )
    prior_rows = list(session.execute(query))
    existing_ids = {
        record.source_record_id
        for record, _source_import in prior_rows
        if record.source_record_id is not None
    }
    policy_records: dict[str, PriorCanonicalRecord] = {}
    already_reversed: set[str] = set()
    for record, source_import in prior_rows:
        if record.event_type == "reversal" and record.reverses_record_id is not None:
            already_reversed.add(record.reverses_record_id)
        if source_import.policy_id != policy_id or record.source_record_id is None:
            continue
        if (
            record.currency_code is None
            or record.signed_amount is None
            or record.event_type is None
        ):
            continue
        policy_records[record.source_record_id] = PriorCanonicalRecord(
            source_record_id=record.source_record_id,
            category=record.category,
            currency_code=record.currency_code,
            signed_amount=record.signed_amount,
            account_code=record.account_code,
            event_type=record.event_type,
            reverses_record_id=record.reverses_record_id,
            policy_id=str(source_import.policy_id),
        )
    return existing_ids, policy_records, already_reversed


def _get_scoped_policy(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    policy_id: UUID,
) -> CoreFinancialPolicy:
    row = session.scalar(
        select(CoreFinancialPolicy).where(
            CoreFinancialPolicy.id == policy_id,
            CoreFinancialPolicy.tenant_id == tenant_id,
            CoreFinancialPolicy.company_id == company_id,
        )
    )
    if row is None:
        raise _not_found("policy")
    _verify_policy(row)
    return row


def get_core_financial_policy(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    policy_id: UUID,
    *,
    settings: Settings,
) -> CoreFinancialPolicyResponse:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    row = _get_scoped_policy(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        policy_id=policy_id,
    )
    return _policy_response(row)


def _get_scoped_mapping(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    mapping_id: UUID,
) -> CoreFinancialMapping:
    row = session.scalar(
        select(CoreFinancialMapping).where(
            CoreFinancialMapping.id == mapping_id,
            CoreFinancialMapping.tenant_id == tenant_id,
            CoreFinancialMapping.company_id == company_id,
        )
    )
    if row is None:
        raise _not_found("mapping")
    _verify_mapping(row)
    return row


def create_core_financial_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    upload: BinaryIO,
    filename: str | None,
    idempotency_key: str,
    metadata: CoreFinancialImportMetadata,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> CoreFinancialImportResponse:
    ensure_core_financials_enabled(settings)
    now = datetime.fromisoformat(_canonical_datetime(now).replace("Z", "+00:00"))
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
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
    try:
        parsed = parse_file(
            safe_name, content, min(settings.import_max_rows, CORE_FINANCIAL_MAX_ROWS)
        )
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    if parsed.file_format != "csv" or len(parsed.worksheets) != 1:
        raise ApiError(415, "unsupported_file", "Core Financials accepts one UTF-8 CSV sheet.")
    sheet = parsed.worksheets[0]
    if not sheet.rows:
        raise ApiError(422, "empty_dataset", "The CSV file contains no non-empty data rows.")
    mapping = _get_scoped_mapping(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        mapping_id=metadata.mapping_id,
    )
    policy_row = _get_scoped_policy(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        policy_id=metadata.policy_id,
    )
    policy = _verify_policy(policy_row)
    if (
        mapping.source_key != metadata.source_key
        or mapping.source_type != metadata.source_type
        or policy_row.tenant_id != tenant_id
    ):
        raise ApiError(
            409,
            "core_financial_import_reference_mismatch",
            "The selected mapping or policy does not match this source.",
        )
    mapping_request = _verify_mapping(mapping)
    ignored_columns = tuple(
        item.model_dump(mode="json") for item in mapping_request.ignored_columns
    )
    try:
        validate_source_headers(
            sheet,
            field_mapping=mapping_request.field_mapping,
            ignored_columns=ignored_columns,
            source_type=metadata.source_type,
        )
    except ValueError as exc:
        raise ApiError(422, "source_mapping_mismatch", str(exc)) from None

    metadata_payload = metadata.model_dump(mode="json")
    request_fingerprint = content_hash(
        {
            "file_name": safe_name,
            "file_hash": file_hash,
            "file_size_bytes": len(content),
            "metadata": metadata_payload,
            "mapping_hash": mapping.mapping_hash,
            "policy_hash": policy_row.definition_hash,
        }
    )
    idempotency_key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
    _lock_for_key(
        session,
        namespace="core-financial-import-idempotency",
        tenant_id=tenant_id,
        company_id=access.company.id,
        key=idempotency_key_hash,
    )
    existing = session.scalar(
        select(CoreFinancialImport).where(
            CoreFinancialImport.tenant_id == tenant_id,
            CoreFinancialImport.company_id == access.company.id,
            CoreFinancialImport.idempotency_key_hash == idempotency_key_hash,
        )
    )
    if existing is not None:
        if (
            existing.requested_by_user_id != user.id
            or existing.request_fingerprint != request_fingerprint
        ):
            raise ApiError(
                409,
                "idempotency_key_conflict",
                "This Idempotency-Key is already associated with different financial evidence.",
            )
        _verify_import_integrity(session, existing)
        return _import_response(existing, replayed=True)

    _lock_for_key(
        session,
        namespace="core-financial-source-identity",
        tenant_id=tenant_id,
        company_id=access.company.id,
        key=f"{metadata.source_key}:{metadata.source_type}",
    )
    same_evidence = session.scalar(
        select(CoreFinancialImport)
        .where(
            CoreFinancialImport.tenant_id == tenant_id,
            CoreFinancialImport.company_id == access.company.id,
            CoreFinancialImport.source_key == metadata.source_key,
            CoreFinancialImport.source_type == metadata.source_type,
            CoreFinancialImport.file_hash == file_hash,
            CoreFinancialImport.file_size_bytes == len(content),
            CoreFinancialImport.mapping_id == mapping.id,
            CoreFinancialImport.mapping_hash == mapping.mapping_hash,
            CoreFinancialImport.policy_id == policy_row.id,
            CoreFinancialImport.policy_hash == policy_row.definition_hash,
            CoreFinancialImport.period_start == metadata.period_start,
            CoreFinancialImport.period_end == metadata.period_end,
            CoreFinancialImport.period_completeness == metadata.period_completeness,
        )
        .order_by(CoreFinancialImport.created_at.asc(), CoreFinancialImport.id.asc())
        .limit(1)
    )
    if same_evidence is not None:
        _verify_import_integrity(session, same_evidence)
        return _import_response(same_evidence, replayed=True)

    existing_ids, prior_records, prior_reversed_ids = _prior_source_records(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_key=metadata.source_key,
        source_type=metadata.source_type,
        policy_id=policy_row.id,
    )
    import_id = uuid4()
    try:
        normalized_rows = normalize_source_rows(
            sheet,
            source_type=metadata.source_type,
            source_key=metadata.source_key,
            file_hash=file_hash,
            mapping_hash=mapping.mapping_hash,
            policy_hash=policy_row.definition_hash,
            field_mapping=mapping_request.field_mapping,
            policy=policy,
            policy_id=str(policy_row.id),
            source_period_start=metadata.period_start,
            source_period_end=metadata.period_end,
            existing_record_ids=existing_ids,
            prior_records=prior_records,
            prior_reversed_ids=prior_reversed_ids,
        )
    except ValueError as exc:
        raise ApiError(422, "financial_source_normalization_failed", str(exc)) from None

    summary_rows = [
        _normalized_row_snapshot(
            row,
            _canonical_record_key(
                tenant_id=tenant_id,
                company_id=access.company.id,
                source_key=metadata.source_key,
                source_type=metadata.source_type,
                source_record_id=row.source_record_id,
            )
            if row.row_status == "accepted" and row.source_record_id is not None
            else None,
        )
        for row in normalized_rows
    ]
    counts = _counts_for_rows(summary_rows)
    quality_status: Literal["passed", "incomplete"] = (
        "passed" if counts["rejected_rows"] == 0 and counts["duplicate_rows"] == 0 else "incomplete"
    )
    input_snapshot = _build_import_input_snapshot(
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_key=metadata.source_key,
        source_type=metadata.source_type,
        period_start=metadata.period_start,
        period_end=metadata.period_end,
        period_completeness=metadata.period_completeness,
        file_name=safe_name,
        file_hash=file_hash,
        file_size_bytes=len(content),
        row_count=len(normalized_rows),
        blank_rows_ignored=sheet.blank_rows_ignored,
        mapping=mapping,
        policy=policy_row,
        source_row_hashes=[
            {"row_number": item["row_number"], "row_hash": item["source_row_hash"]}
            for item in summary_rows
        ],
    )
    result_snapshot = _import_result_snapshot(summary_rows, quality_status=quality_status)
    input_hash = content_hash(input_snapshot)
    result_hash = content_hash(result_snapshot)
    source_import = CoreFinancialImport(
        id=import_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        source_key=metadata.source_key,
        source_type=metadata.source_type,
        file_name=safe_name,
        file_format="csv",
        file_hash=file_hash,
        file_size_bytes=len(content),
        mapping_id=mapping.id,
        mapping_version=mapping.version,
        mapping_hash=mapping.mapping_hash,
        policy_id=policy_row.id,
        policy_version=policy_row.version,
        policy_hash=policy_row.definition_hash,
        idempotency_key_hash=idempotency_key_hash,
        request_fingerprint=request_fingerprint,
        period_start=metadata.period_start,
        period_end=metadata.period_end,
        period_completeness=metadata.period_completeness,
        quality_status=quality_status,
        total_rows=counts["total_rows"],
        accepted_rows=counts["accepted_rows"],
        rejected_rows=counts["rejected_rows"],
        duplicate_rows=counts["duplicate_rows"],
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=now,
    )
    session.add(source_import)
    session.flush()
    record_rows: list[CoreFinancialRecord] = []
    for normalized in normalized_rows:
        canonical_key = (
            _canonical_record_key(
                tenant_id=tenant_id,
                company_id=access.company.id,
                source_key=metadata.source_key,
                source_type=metadata.source_type,
                source_record_id=normalized.source_record_id,
            )
            if normalized.row_status == "accepted" and normalized.source_record_id is not None
            else None
        )
        record_rows.append(
            CoreFinancialRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=access.company.id,
                import_id=import_id,
                source_row_number=normalized.row_number,
                source_record_id=normalized.source_record_id,
                transaction_date=normalized.transaction_date,
                currency_code=normalized.currency_code,
                account_code=normalized.account_code,
                category=normalized.category,
                event_type=normalized.event_type,
                signed_amount=normalized.signed_amount,
                reverses_record_id=normalized.reverses_record_id,
                canonical_record_key=canonical_key,
                source_row_hash=normalized.source_row_hash,
                mapped_source_values=normalized.mapped_source_values,
                row_status=normalized.row_status,
                issues=list(normalized.issues),
                created_at=now,
            )
        )
    session.add_all(record_rows)
    session.flush()
    record_event(
        session,
        "core_financials.import_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="core_financial_import",
        target_id=import_id,
        request_id=request_id,
        details={
            "source_key": source_import.source_key,
            "source_type": source_import.source_type,
            "file_hash": source_import.file_hash,
            "mapping_version": source_import.mapping_version,
            "policy_version": source_import.policy_version,
            "total_rows": source_import.total_rows,
            "accepted_rows": source_import.accepted_rows,
            "rejected_rows": source_import.rejected_rows,
            "duplicate_rows": source_import.duplicate_rows,
            "quality_status": source_import.quality_status,
        },
    )
    return _import_response(source_import)


def list_core_financial_imports(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> CoreFinancialImportPage:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    filters = (
        CoreFinancialImport.tenant_id == tenant_id,
        CoreFinancialImport.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(CoreFinancialImport).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(CoreFinancialImport)
            .where(*filters)
            .order_by(CoreFinancialImport.created_at.desc(), CoreFinancialImport.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CoreFinancialImportPage(
        items=[_import_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


def _get_scoped_import(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    import_id: UUID,
) -> CoreFinancialImport:
    row = session.scalar(
        select(CoreFinancialImport).where(
            CoreFinancialImport.id == import_id,
            CoreFinancialImport.tenant_id == tenant_id,
            CoreFinancialImport.company_id == company_id,
        )
    )
    if row is None:
        raise _not_found("import")
    return row


def get_core_financial_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    *,
    settings: Settings,
) -> CoreFinancialImportResponse:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    row = _get_scoped_import(
        session, tenant_id=tenant_id, company_id=access.company.id, import_id=import_id
    )
    _verify_import_integrity(session, row)
    return _import_response(row)


def list_core_financial_records(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    *,
    limit: int,
    offset: int,
    row_status: Literal["accepted", "rejected", "duplicate"] | None,
    settings: Settings,
) -> CoreFinancialRecordPage:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    source_import = _get_scoped_import(
        session, tenant_id=tenant_id, company_id=access.company.id, import_id=import_id
    )
    _verify_import_integrity(session, source_import)
    filters = [
        CoreFinancialRecord.tenant_id == tenant_id,
        CoreFinancialRecord.company_id == access.company.id,
        CoreFinancialRecord.import_id == import_id,
    ]
    if row_status is not None:
        filters.append(CoreFinancialRecord.row_status == row_status)
    total = (
        session.scalar(select(func.count()).select_from(CoreFinancialRecord).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(CoreFinancialRecord)
            .where(*filters)
            .order_by(CoreFinancialRecord.source_row_number)
            .limit(limit)
            .offset(offset)
        )
    )
    return CoreFinancialRecordPage(
        items=[
            CoreFinancialRecordResponse(
                id=row.id,
                source_row_number=row.source_row_number,
                source_record_id=row.source_record_id,
                transaction_date=row.transaction_date,
                currency_code=row.currency_code,
                account_code=row.account_code,
                category=row.category,
                event_type=row.event_type,
                signed_amount=row.signed_amount,
                row_status=cast(Literal["accepted", "rejected", "duplicate"], row.row_status),
                row_hash=row.source_row_hash,
                issues=tuple(row.issues),
                reverses_record_id=row.reverses_record_id,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def _source_import_state(row: CoreFinancialImport) -> SourceImportState:
    return SourceImportState(
        id=row.id,
        source_key=row.source_key,
        source_type=cast(SourceType, row.source_type),
        file_hash=row.file_hash,
        mapping_hash=row.mapping_hash,
        mapping_version=row.mapping_version,
        policy_hash=row.policy_hash,
        period_start=row.period_start,
        period_end=row.period_end,
        period_completeness=row.period_completeness,
        quality_status=row.quality_status,
        accepted_rows=row.accepted_rows,
        rejected_rows=row.rejected_rows,
        duplicate_rows=row.duplicate_rows,
    )


def _fact_from_record(
    record: CoreFinancialRecord,
    source_import: CoreFinancialImport,
) -> CanonicalFinancialFact:
    if (
        record.row_status != "accepted"
        or record.source_record_id is None
        or record.transaction_date is None
        or record.currency_code is None
        or record.event_type is None
        or record.signed_amount is None
    ):
        raise _integrity_error()
    return CanonicalFinancialFact(
        id=record.id,
        import_id=record.import_id,
        source_record_id=record.source_record_id,
        transaction_date=record.transaction_date,
        currency_code=record.currency_code,
        account_code=record.account_code,
        category=cast(FinancialCategory | None, record.category),
        event_type=record.event_type,
        signed_amount=record.signed_amount,
        source_row_number=record.source_row_number,
        source_row_hash=record.source_row_hash,
        source_key=source_import.source_key,
        source_type=cast(SourceType, source_import.source_type),
        source_file_hash=source_import.file_hash,
    )


def _fact_payload(fact: CanonicalFinancialFact) -> dict[str, object]:
    return {
        "id": str(fact.id),
        "import_id": str(fact.import_id),
        "source_record_id": fact.source_record_id,
        "transaction_date": fact.transaction_date.isoformat(),
        "currency_code": fact.currency_code,
        "account_code": fact.account_code,
        "category": fact.category,
        "event_type": fact.event_type,
        "signed_amount": fact.signed_amount,
        "source_row_number": fact.source_row_number,
        "source_row_hash": fact.source_row_hash,
        "source_key": fact.source_key,
        "source_type": fact.source_type,
        "source_file_hash": fact.source_file_hash,
    }


def _run_request_payload(request: CoreFinancialRunCreateRequest) -> dict[str, object]:
    return request.model_dump(mode="json", exclude_none=True)


def _run_import_payload(
    row: CoreFinancialImport, records: list[CoreFinancialRecord]
) -> dict[str, object]:
    return {
        "id": str(row.id),
        "source_key": row.source_key,
        "source_type": row.source_type,
        "file_hash": row.file_hash,
        "mapping_id": str(row.mapping_id),
        "mapping_version": row.mapping_version,
        "mapping_hash": row.mapping_hash,
        "policy_id": str(row.policy_id),
        "policy_version": row.policy_version,
        "policy_hash": row.policy_hash,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "period_completeness": row.period_completeness,
        "quality_status": row.quality_status,
        "total_rows": row.total_rows,
        "accepted_rows": row.accepted_rows,
        "rejected_rows": row.rejected_rows,
        "duplicate_rows": row.duplicate_rows,
        "input_hash": row.input_hash,
        "result_hash": row.result_hash,
        "source_rows": [
            {
                "id": str(record.id),
                "row_number": record.source_row_number,
                "row_hash": record.source_row_hash,
                "row_status": record.row_status,
            }
            for record in records
        ],
    }


def _run_input_snapshot(
    *,
    tenant_id: UUID,
    company_id: UUID,
    policy_row: CoreFinancialPolicy,
    policy: CoreFinancialPolicyCreateRequest,
    request: CoreFinancialRunCreateRequest,
    imports: tuple[tuple[CoreFinancialImport, list[CoreFinancialRecord]] | None, ...],
    facts: tuple[CanonicalFinancialFact, ...],
    engine_version: str = CORE_FINANCIAL_ENGINE_VERSION,
) -> dict[str, object]:
    source_imports = [
        _run_import_payload(source_import, records)
        for item in imports
        if item is not None
        for source_import, records in [item]
    ]
    source_imports.sort(key=lambda item: (str(item["source_type"]), str(item["id"])))
    ordered_facts = sorted(
        facts,
        key=lambda item: (
            item.source_type,
            item.source_key,
            item.source_row_number,
            str(item.id),
        ),
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "engine_version": engine_version,
        "scope": {"tenant_id": str(tenant_id), "company_id": str(company_id)},
        "policy": {
            "id": str(policy_row.id),
            "policy_key": policy_row.policy_key,
            "version": policy_row.version,
            "definition_hash": policy_row.definition_hash,
            "definition": policy.model_dump(mode="json", exclude_none=True),
            "authority": policy_row.authority,
        },
        "request": _run_request_payload(request),
        "imports": source_imports,
        "canonical_facts": [_fact_payload(item) for item in ordered_facts],
    }


def _run_result_snapshot(
    *,
    state: Literal["calculated", "incomplete"],
    metrics: tuple[CoreFinancialMetricResult, ...],
    analysis: tuple[CoreFinancialAnalysisStatement, ...],
    engine_version: str = CORE_FINANCIAL_ENGINE_VERSION,
) -> dict[str, object]:
    metric_payloads = [item.model_dump(mode="json") for item in metrics]
    if engine_version == LEGACY_CORE_FINANCIAL_ENGINE_VERSION:
        for item in metric_payloads:
            item.pop("excluded_transfer_count", None)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "engine_version": engine_version,
        "state": state,
        "metrics": metric_payloads,
        "analysis": [item.model_dump(mode="json") for item in analysis],
    }


def _line_payload(line: FinancialRunLine) -> dict[str, object]:
    return {
        "metric": line.metric,
        "record_id": str(line.fact.id),
        "formula_coefficient": line.coefficient,
        "contribution_amount": line.contribution_amount,
    }


def _evidence_payload_from_rows(rows: list[CoreFinancialRunEvidence]) -> list[dict[str, object]]:
    return [
        {
            "metric": row.metric,
            "record_id": str(row.record_id),
            "formula_coefficient": row.formula_coefficient,
            "contribution_amount": row.contribution_amount,
        }
        for row in sorted(rows, key=lambda item: (item.metric, str(item.record_id)))
    ]


def _trace_hash(
    *,
    run_id: UUID,
    input_hash: str,
    result_hash: str,
    evidence_hash: str,
    created_at: datetime,
    engine_version: str = CORE_FINANCIAL_ENGINE_VERSION,
) -> str:
    return content_hash(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "engine_version": engine_version,
            "run_id": str(run_id),
            "input_hash": input_hash,
            "result_hash": result_hash,
            "evidence_hash": evidence_hash,
            "created_at": _canonical_datetime(created_at),
        }
    )


def _verify_run_record(session: Session, row: CoreFinancialRun) -> list[CoreFinancialRunEvidence]:
    if (
        not isinstance(row.input_snapshot, dict)
        or not isinstance(row.result_snapshot, dict)
        or content_hash(row.input_snapshot) != row.input_hash
        or content_hash(row.result_snapshot) != row.result_hash
        or row.state != row.result_snapshot.get("state")
        or row.input_snapshot.get("engine_version")
        not in (LEGACY_CORE_FINANCIAL_ENGINE_VERSION, CORE_FINANCIAL_ENGINE_VERSION)
        or row.result_snapshot.get("engine_version") != row.input_snapshot.get("engine_version")
    ):
        raise _integrity_error()
    engine_version = cast(str, row.input_snapshot["engine_version"])
    evidence_rows = list(
        session.scalars(
            select(CoreFinancialRunEvidence)
            .where(
                CoreFinancialRunEvidence.tenant_id == row.tenant_id,
                CoreFinancialRunEvidence.company_id == row.company_id,
                CoreFinancialRunEvidence.run_id == row.id,
            )
            .order_by(CoreFinancialRunEvidence.metric, CoreFinancialRunEvidence.record_id)
        )
    )
    evidence_hash = content_hash(_evidence_payload_from_rows(evidence_rows))
    expected_trace = _trace_hash(
        run_id=row.id,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        evidence_hash=evidence_hash,
        created_at=row.created_at,
        engine_version=engine_version,
    )
    if expected_trace != row.trace_hash:
        raise _integrity_error()
    return evidence_rows


def _run_response(session: Session, row: CoreFinancialRun) -> CoreFinancialRunResponse:
    _verify_run_record(session, row)
    snapshot = row.result_snapshot
    try:
        metrics_payload = snapshot.get("metrics")
        analysis_payload = snapshot.get("analysis")
        if not isinstance(metrics_payload, list) or not isinstance(analysis_payload, list):
            raise _integrity_error()
        metrics = tuple(CoreFinancialMetricResult.model_validate(item) for item in metrics_payload)
        analysis = tuple(
            CoreFinancialAnalysisStatement.model_validate(item) for item in analysis_payload
        )
        response = CoreFinancialRunResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            requested_by_user_id=row.requested_by_user_id,
            policy_id=row.policy_id,
            policy_version=row.policy_version,
            policy_hash=row.policy_hash,
            authority="illustrative_not_authoritative",
            reporting_framework="management_reporting",
            accounting_policy_status="not_established",
            period_start=row.period_start,
            period_end=row.period_end,
            period_rule="inclusive_transaction_date",
            currency_code=row.currency_code,
            fx_policy="one_currency_no_fx",
            ledger_import_id=row.ledger_import_id,
            bank_import_id=row.bank_import_id,
            state=cast(Literal["calculated", "incomplete"], row.state),
            engine_version=cast(str, row.input_snapshot["engine_version"]),
            metrics=metrics,
            analysis=analysis,
            input_hash=row.input_hash,
            result_hash=row.result_hash,
            trace_hash=row.trace_hash,
            created_at=row.created_at,
        )
    except (ValidationError, TypeError):
        raise _integrity_error() from None
    return response


def _load_run_calculation_inputs(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    request: CoreFinancialRunCreateRequest,
) -> tuple[
    CoreFinancialPolicy,
    CoreFinancialPolicyCreateRequest,
    tuple[tuple[CoreFinancialImport, list[CoreFinancialRecord]] | None, ...],
    SourceImportState | None,
    SourceImportState | None,
    tuple[CanonicalFinancialFact, ...],
]:
    policy_row = _get_scoped_policy(
        session,
        tenant_id=tenant_id,
        company_id=company_id,
        policy_id=request.policy_id,
    )
    policy = _verify_policy(policy_row)
    input_imports: list[tuple[CoreFinancialImport, list[CoreFinancialRecord]] | None] = []
    ledger_state: SourceImportState | None = None
    bank_state: SourceImportState | None = None
    facts: list[CanonicalFinancialFact] = []
    selected_sources: tuple[tuple[SourceType, UUID | None], ...] = (
        ("general_ledger", request.ledger_import_id),
        ("bank_activity", request.bank_import_id),
    )
    for expected_type, import_id in selected_sources:
        if import_id is None:
            input_imports.append(None)
            continue
        source_import = _get_scoped_import(
            session,
            tenant_id=tenant_id,
            company_id=company_id,
            import_id=import_id,
        )
        if (
            source_import.source_type != expected_type
            or source_import.policy_id != policy_row.id
            or source_import.policy_version != policy_row.version
            or source_import.policy_hash != policy_row.definition_hash
        ):
            raise ApiError(
                409,
                "core_financial_run_reference_mismatch",
                "The selected import does not match the requested source and policy version.",
            )
        _mapping, _source_policy, records = _verify_import_integrity(session, source_import)
        facts.extend(
            _fact_from_record(record, source_import)
            for record in records
            if record.row_status == "accepted"
        )
        input_imports.append((source_import, records))
        if expected_type == "general_ledger":
            ledger_state = _source_import_state(source_import)
        else:
            bank_state = _source_import_state(source_import)
    return (
        policy_row,
        policy,
        tuple(input_imports),
        ledger_state,
        bank_state,
        tuple(facts),
    )


def create_core_financial_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    request: CoreFinancialRunCreateRequest,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> CoreFinancialRunResponse:
    ensure_core_financials_enabled(settings)
    now = datetime.fromisoformat(_canonical_datetime(now).replace("Z", "+00:00"))
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    (
        policy_row,
        policy,
        imports,
        ledger_import,
        bank_import,
        facts,
    ) = _load_run_calculation_inputs(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        request=request,
    )
    input_snapshot = _run_input_snapshot(
        tenant_id=tenant_id,
        company_id=access.company.id,
        policy_row=policy_row,
        policy=policy,
        request=request,
        imports=imports,
        facts=facts,
    )
    input_hash = content_hash(input_snapshot)
    metrics, analysis, lines, run_state = calculate_financial_metrics(
        policy=policy,
        policy_version=policy_row.version,
        request_period_start=request.period_start,
        request_period_end=request.period_end,
        currency_code=request.currency_code,
        ledger_import=ledger_import,
        bank_import=bank_import,
        facts=facts,
        controls=request.controls,
        requested_metrics=(request.measure,),
    )
    result_snapshot = _run_result_snapshot(state=run_state, metrics=metrics, analysis=analysis)
    result_hash = content_hash(result_snapshot)
    line_payload = sorted(
        (_line_payload(item) for item in lines),
        key=lambda item: (str(item["metric"]), str(item["record_id"])),
    )
    evidence_hash = content_hash(line_payload)
    run_id = uuid4()
    trace_hash = _trace_hash(
        run_id=run_id,
        input_hash=input_hash,
        result_hash=result_hash,
        evidence_hash=evidence_hash,
        created_at=now,
    )
    row = CoreFinancialRun(
        id=run_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        policy_id=policy_row.id,
        policy_version=policy_row.version,
        policy_hash=policy_row.definition_hash,
        ledger_import_id=request.ledger_import_id,
        bank_import_id=request.bank_import_id,
        period_start=request.period_start,
        period_end=request.period_end,
        currency_code=request.currency_code,
        state=run_state,
        input_hash=input_hash,
        result_hash=result_hash,
        trace_hash=trace_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=now,
    )
    session.add(row)
    session.flush()
    for line in lines:
        session.add(
            CoreFinancialRunEvidence(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=access.company.id,
                run_id=run_id,
                metric=line.metric,
                record_id=line.fact.id,
                formula_coefficient=line.coefficient,
                contribution_amount=line.contribution_amount,
                created_at=now,
            )
        )
    session.flush()
    record_event(
        session,
        "core_financials.run_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="core_financial_run",
        target_id=run_id,
        request_id=request_id,
        details={
            "policy_id": policy_row.id,
            "policy_version": policy_row.version,
            "measure": request.measure,
            "ledger_import_id": request.ledger_import_id,
            "bank_import_id": request.bank_import_id,
            "period_start": request.period_start.isoformat(),
            "period_end": request.period_end.isoformat(),
            "currency_code": request.currency_code,
            "state": run_state,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "trace_hash": trace_hash,
        },
    )
    return _run_response(session, row)


def _get_scoped_run(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    run_id: UUID,
) -> CoreFinancialRun:
    row = session.scalar(
        select(CoreFinancialRun).where(
            CoreFinancialRun.id == run_id,
            CoreFinancialRun.tenant_id == tenant_id,
            CoreFinancialRun.company_id == company_id,
        )
    )
    if row is None:
        raise _not_found("run")
    return row


def get_core_financial_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    run_id: UUID,
    *,
    settings: Settings,
) -> CoreFinancialRunResponse:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    row = _get_scoped_run(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        run_id=run_id,
    )
    return _run_response(session, row)


def list_core_financial_runs(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> CoreFinancialRunPage:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    filters = (
        CoreFinancialRun.tenant_id == tenant_id,
        CoreFinancialRun.company_id == access.company.id,
    )
    total = session.scalar(select(func.count()).select_from(CoreFinancialRun).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(CoreFinancialRun)
            .where(*filters)
            .order_by(CoreFinancialRun.created_at.desc(), CoreFinancialRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CoreFinancialRunPage(
        items=[_run_response(session, row) for row in rows], total=total, limit=limit, offset=offset
    )


def _overview_label(measure: FinancialMeasure) -> str:
    return {
        "revenue": "Revenue",
        "expenses": "Expenses",
        "gross_profit": "Gross Profit",
        "operating_profit": "Operating Profit",
        "net_profit": "Net Profit",
        "cash_flow": "Cash Flow — net observed bank movement",
    }[measure]


def _missing_overview_measure(measure: FinancialMeasure) -> CoreFinancialOverviewMeasure:
    return CoreFinancialOverviewMeasure(
        measure=measure,
        label=_overview_label(measure),
        status="missing",
        metric_status=None,
        reported_value=None,
        exact_value=None,
        currency_code=None,
        basis=None,
        period_start=None,
        period_end=None,
        period_rule=None,
        fx_policy=None,
        run_id=None,
        run_url=None,
        evidence_url=None,
        evidence_status="not_available",
        evidence_count=0,
        run_state=None,
        engine_version=None,
        policy_id=None,
        policy_version=None,
        policy_hash=None,
        policy_url=None,
        policy_authority=None,
        reporting_framework=None,
        accounting_policy_status=None,
        source_imports=(),
        reconciliation=None,
        reason_codes=("no_persisted_run",),
        limitations=(
            "No persisted run exists for this measure in the selected Company workspace.",
        ),
        assumptions=(),
    )


def get_core_financial_overview(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    settings: Settings,
) -> CoreFinancialOverviewResponse:
    """Return the latest immutable run per measure and policy-scoped comparison checks."""
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    filters = (
        CoreFinancialRun.tenant_id == tenant_id,
        CoreFinancialRun.company_id == access.company.id,
    )
    requested_measure = CoreFinancialRun.input_snapshot["request"]["measure"].as_string()
    latest_rows: dict[FinancialMeasure, CoreFinancialRun | None] = {}
    for measure in FINANCIAL_MEASURES:
        latest_rows[measure] = session.scalar(
            select(CoreFinancialRun)
            .where(*filters, requested_measure == measure)
            .order_by(CoreFinancialRun.created_at.desc(), CoreFinancialRun.id.desc())
            .limit(1)
        )

    source_cache: dict[UUID, CoreFinancialOverviewSource] = {}
    measures: list[CoreFinancialOverviewMeasure] = []
    for measure in FINANCIAL_MEASURES:
        row = latest_rows[measure]
        if row is None:
            measures.append(_missing_overview_measure(measure))
            continue

        run_response = _run_response(session, row)
        request_payload = row.input_snapshot.get("request")
        if not isinstance(request_payload, dict):
            raise _integrity_error()
        try:
            saved_request = CoreFinancialRunCreateRequest.model_validate(request_payload)
        except ValidationError:
            raise _integrity_error() from None
        if (
            saved_request.measure != measure
            or saved_request.policy_id != row.policy_id
            or saved_request.ledger_import_id != row.ledger_import_id
            or saved_request.bank_import_id != row.bank_import_id
            or saved_request.period_start != row.period_start
            or saved_request.period_end != row.period_end
            or saved_request.currency_code != row.currency_code
            or len(run_response.metrics) != 1
            or run_response.metrics[0].metric != measure
        ):
            raise _integrity_error()
        metric = run_response.metrics[0]
        if (metric.status == "calculated") != (run_response.state == "calculated"):
            raise _integrity_error()
        if metric.status == "calculated":
            if metric.exact_value is None or metric.reported_value is None:
                raise _integrity_error()
        elif metric.exact_value is not None or metric.reported_value is not None:
            raise _integrity_error()

        policy_row = _get_scoped_policy(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            policy_id=row.policy_id,
        )
        policy_definition = _verify_policy(policy_row)
        if (
            policy_row.version != row.policy_version
            or policy_row.definition_hash != row.policy_hash
        ):
            raise _integrity_error()

        if measure == "cash_flow":
            source_import_id = row.bank_import_id
            expected_source_type: SourceType = "bank_activity"
            if row.ledger_import_id is not None:
                raise _integrity_error()
        else:
            source_import_id = row.ledger_import_id
            expected_source_type = "general_ledger"
            if row.bank_import_id is not None:
                raise _integrity_error()
        if source_import_id is None:
            raise _integrity_error()
        if metric.source_import_ids and metric.source_import_ids != (source_import_id,):
            raise _integrity_error()

        source = source_cache.get(source_import_id)
        if source is None:
            import_row = _get_scoped_import(
                session,
                tenant_id=tenant_id,
                company_id=access.company.id,
                import_id=source_import_id,
            )
            _verify_import_integrity(session, import_row)
            if (
                import_row.source_type != expected_source_type
                or import_row.policy_id != row.policy_id
                or import_row.policy_version != row.policy_version
                or import_row.policy_hash != row.policy_hash
            ):
                raise _integrity_error()
            source = CoreFinancialOverviewSource(
                id=import_row.id,
                source_key=import_row.source_key,
                source_type=import_row.source_type,
                file_name=import_row.file_name,
                file_hash=import_row.file_hash,
                input_hash=import_row.input_hash,
                result_hash=import_row.result_hash,
                mapping_version=import_row.mapping_version,
                mapping_hash=import_row.mapping_hash,
                policy_id=import_row.policy_id,
                policy_version=import_row.policy_version,
                policy_hash=import_row.policy_hash,
                period_start=import_row.period_start,
                period_end=import_row.period_end,
                period_completeness=cast(
                    Literal["complete", "partial", "unknown"], import_row.period_completeness
                ),
                completeness_authority="caller_asserted_not_verified",
                quality_status=cast(Literal["passed", "incomplete"], import_row.quality_status),
                total_rows=import_row.total_rows,
                accepted_rows=import_row.accepted_rows,
                rejected_rows=import_row.rejected_rows,
                duplicate_rows=import_row.duplicate_rows,
                import_url=(f"/api/v1/tenants/{tenant_id}/core-financials/imports/{import_row.id}"),
            )
            source_cache[source_import_id] = source

        evidence_rows = _verify_run_record(session, row)
        evidence_count = sum(item.metric == measure for item in evidence_rows)
        policy_formulas = {
            policy_measure: formula.terms
            for policy_measure, formula in policy_definition.metric_formulas.items()
        }
        formula_terms = (
            policy_definition.metric_formulas[measure].terms if measure != "cash_flow" else ()
        )
        limitations = tuple(
            statement.text
            for statement in run_response.analysis
            if statement.kind == "limitation" and statement.metric in (None, measure)
        )
        assumptions = (
            "The saved policy is caller-supplied illustrative management reporting; accounting "
            "approval and GAAP/IFRS treatment are not established.",
            "Source identity and period completeness are caller assertions and are not "
            "independently authenticated or verified against a source system.",
            f"The run reports {run_response.currency_code} only; no FX conversion or consolidation "
            "is performed.",
            "The reporting window is inclusive by transaction date; no fiscal calendar is "
            "inferred.",
        )
        status: Literal["available", "incomplete", "withheld", "missing"] = (
            "available"
            if metric.status == "calculated"
            else "incomplete"
            if metric.status == "incomplete"
            else "withheld"
        )
        measures.append(
            CoreFinancialOverviewMeasure(
                measure=measure,
                label=_overview_label(measure),
                status=status,
                metric_status=metric.status,
                reported_value=metric.reported_value,
                exact_value=metric.exact_value,
                currency_code=metric.currency_code,
                basis=metric.basis,
                period_start=run_response.period_start,
                period_end=run_response.period_end,
                period_rule=run_response.period_rule,
                fx_policy=run_response.fx_policy,
                run_id=run_response.id,
                run_url=(f"/api/v1/tenants/{tenant_id}/core-financials/runs/{run_response.id}"),
                evidence_url=(
                    f"/api/v1/tenants/{tenant_id}/core-financials/runs/{run_response.id}"
                    f"/evidence?metric={measure}"
                ),
                evidence_status="available" if evidence_count else "empty",
                evidence_count=evidence_count,
                run_state=run_response.state,
                engine_version=run_response.engine_version,
                policy_id=run_response.policy_id,
                policy_version=run_response.policy_version,
                policy_hash=run_response.policy_hash,
                policy_url=(
                    f"/api/v1/tenants/{tenant_id}/core-financials/policies/{run_response.policy_id}"
                ),
                policy_authority=run_response.authority,
                reporting_framework=run_response.reporting_framework,
                accounting_policy_status=run_response.accounting_policy_status,
                source_imports=(source,),
                reconciliation=metric.reconciliation,
                required_categories=metric.required_categories,
                category_totals=metric.category_totals,
                formula_terms=formula_terms,
                policy_formulas=policy_formulas,
                category_coverage=policy_definition.category_coverage,
                cash_flow_policy=policy_definition.cash_flow_policy,
                reason_codes=metric.reason_codes,
                limitations=limitations,
                assumptions=assumptions,
            )
        )

    measure_tuple = tuple(measures)
    return CoreFinancialOverviewResponse(
        tenant_id=tenant_id,
        company_id=access.company.id,
        selection_rule="latest_saved_run_per_measure",
        measures=measure_tuple,
        checks=build_overview_checks(measure_tuple),
        limitations=(
            "The overview selects the latest saved run for each measure independently; their "
            "reporting periods, currencies, sources, policies, or calculation versions may differ.",
            "Cross-measure checks abstain when saved formulas or run contexts do not support a "
            "comparison. A consistent result is not an accounting conclusion or audit opinion.",
            "No missing measure is imputed, no expense category is assumed to be cost of sales, "
            "and no cross-period or cross-currency arithmetic is performed.",
        ),
    )


def verify_core_financial_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    run_id: UUID,
    *,
    settings: Settings,
    request_id: UUID | None = None,
) -> CoreFinancialRunVerificationResponse:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    row = _get_scoped_run(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        run_id=run_id,
    )
    persisted_evidence_rows = _verify_run_record(session, row)
    persisted_evidence = _evidence_payload_from_rows(persisted_evidence_rows)
    reasons: list[str] = []
    request_data = row.input_snapshot.get("request")
    if not isinstance(request_data, dict):
        raise _integrity_error()
    try:
        saved_request = CoreFinancialRunCreateRequest.model_validate(request_data)
    except ValidationError:
        raise _integrity_error() from None
    if (
        saved_request.policy_id != row.policy_id
        or saved_request.ledger_import_id != row.ledger_import_id
        or saved_request.bank_import_id != row.bank_import_id
        or saved_request.period_start != row.period_start
        or saved_request.period_end != row.period_end
        or saved_request.currency_code != row.currency_code
    ):
        raise _integrity_error()
    engine_version = cast(str, row.input_snapshot["engine_version"])
    (
        policy_row,
        policy,
        imports,
        ledger_import,
        bank_import,
        facts,
    ) = _load_run_calculation_inputs(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        request=saved_request,
    )
    replayed_input = _run_input_snapshot(
        tenant_id=tenant_id,
        company_id=access.company.id,
        policy_row=policy_row,
        policy=policy,
        request=saved_request,
        imports=imports,
        facts=facts,
        engine_version=engine_version,
    )
    replayed_input_hash = content_hash(replayed_input)
    if replayed_input_hash != row.input_hash or replayed_input != row.input_snapshot:
        reasons.append("persisted_input_hash_mismatch")
    metrics, analysis, lines, replayed_state = calculate_financial_metrics(
        policy=policy,
        policy_version=policy_row.version,
        request_period_start=saved_request.period_start,
        request_period_end=saved_request.period_end,
        currency_code=saved_request.currency_code,
        ledger_import=ledger_import,
        bank_import=bank_import,
        facts=facts,
        controls=saved_request.controls,
        requested_metrics=(saved_request.measure,),
        legacy_v2_replay=engine_version == LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    )
    replayed_result = _run_result_snapshot(
        state=replayed_state,
        metrics=metrics,
        analysis=analysis,
        engine_version=engine_version,
    )
    replayed_result_hash = content_hash(replayed_result)
    replayed_evidence = sorted(
        (_line_payload(item) for item in lines),
        key=lambda item: (str(item["metric"]), str(item["record_id"])),
    )
    if replayed_result_hash != row.result_hash or replayed_result != row.result_snapshot:
        reasons.append("persisted_result_hash_mismatch")
    if replayed_evidence != persisted_evidence:
        reasons.append("persisted_evidence_mismatch")
    replayed_trace_hash = _trace_hash(
        run_id=row.id,
        input_hash=replayed_input_hash,
        result_hash=replayed_result_hash,
        evidence_hash=content_hash(replayed_evidence),
        created_at=row.created_at,
        engine_version=engine_version,
    )
    if replayed_trace_hash != row.trace_hash:
        reasons.append("persisted_trace_hash_mismatch")
    if replayed_state != row.state:
        reasons.append("persisted_state_mismatch")
    return CoreFinancialRunVerificationResponse(
        run_id=row.id,
        passed=not reasons,
        reason_codes=tuple(reasons),
        recorded_input_hash=row.input_hash,
        replayed_input_hash=replayed_input_hash,
        recorded_result_hash=row.result_hash,
        replayed_result_hash=replayed_result_hash,
        engine_version=engine_version,
    )


def list_core_financial_run_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    run_id: UUID,
    *,
    limit: int,
    offset: int,
    metric: FinancialMeasure | None,
    settings: Settings,
) -> CoreFinancialRunEvidencePage:
    ensure_core_financials_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    run = _get_scoped_run(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        run_id=run_id,
    )
    _verify_run_record(session, run)
    filters = [
        CoreFinancialRunEvidence.tenant_id == tenant_id,
        CoreFinancialRunEvidence.company_id == access.company.id,
        CoreFinancialRunEvidence.run_id == run_id,
    ]
    if metric is not None:
        filters.append(CoreFinancialRunEvidence.metric == metric)
    total = (
        session.scalar(select(func.count()).select_from(CoreFinancialRunEvidence).where(*filters))
        or 0
    )
    evidence_rows = list(
        session.execute(
            select(CoreFinancialRunEvidence, CoreFinancialRecord, CoreFinancialImport)
            .join(
                CoreFinancialRecord,
                (CoreFinancialRecord.id == CoreFinancialRunEvidence.record_id)
                & (CoreFinancialRecord.tenant_id == CoreFinancialRunEvidence.tenant_id)
                & (CoreFinancialRecord.company_id == CoreFinancialRunEvidence.company_id),
            )
            .join(
                CoreFinancialImport,
                (CoreFinancialImport.id == CoreFinancialRecord.import_id)
                & (CoreFinancialImport.tenant_id == CoreFinancialRecord.tenant_id)
                & (CoreFinancialImport.company_id == CoreFinancialRecord.company_id),
            )
            .where(*filters)
            .order_by(
                CoreFinancialRunEvidence.metric,
                CoreFinancialRecord.transaction_date,
                CoreFinancialRecord.source_row_number,
            )
            .limit(limit)
            .offset(offset)
        )
    )
    items: list[CoreFinancialRunEvidenceResponse] = []
    for evidence, record, source_import in evidence_rows:
        if (
            record.source_record_id is None
            or record.transaction_date is None
            or record.currency_code is None
            or record.event_type is None
            or record.signed_amount is None
        ):
            raise _integrity_error()
        items.append(
            CoreFinancialRunEvidenceResponse(
                id=evidence.id,
                metric=cast(FinancialMeasure, evidence.metric),
                record_id=record.id,
                source_import_id=source_import.id,
                source_key=source_import.source_key,
                source_type=cast(SourceType, source_import.source_type),
                source_file_hash=source_import.file_hash,
                source_row_number=record.source_row_number,
                source_row_hash=record.source_row_hash,
                source_record_id=record.source_record_id,
                transaction_date=record.transaction_date,
                currency_code=record.currency_code,
                account_code=record.account_code,
                category=cast(FinancialCategory | None, record.category),
                event_type=record.event_type,
                source_signed_amount=record.signed_amount,
                formula_coefficient=cast(Literal[-1, 1], evidence.formula_coefficient),
                contribution_amount=evidence.contribution_amount,
            )
        )
    return CoreFinancialRunEvidencePage(items=items, total=total, limit=limit, offset=offset)
