from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from iso4217 import Currency
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.authorization import require_tenant_access
from businessflow.company_context.models import (
    BusinessUnit,
    Department,
    Employee,
    EmployeeSkill,
    EmployeeWeeklyCapacity,
    Project,
    ResourceAssignment,
    Skill,
)
from businessflow.config import Settings
from businessflow.data_imports.models import DataImport, DataImportRecordLink, DataImportRow
from businessflow.data_imports.parsers import ImportFileError, parse_file, read_limited_upload
from businessflow.data_imports.schemas import ImportConfirmRequest, ImportMappingRequest
from businessflow.data_imports.service import (
    apply_import,
    confirm_import,
    set_import_mapping,
    stage_import,
    validate_import,
)
from businessflow.errors import ApiError
from businessflow.metrics.models import MetricRun
from businessflow.metrics.service import create_metric_run
from businessflow.metrics.snapshot import content_hash
from businessflow.models import Company, Tenant, UserAccount
from businessflow.position_reviews.calculation import (
    POSITION_REVIEW_CALCULATION_VERSION,
    build_position_review_payload,
    position_review_hashes,
)
from businessflow.position_reviews.demo import (
    DEMO_SEED_VERSION,
    DEMO_SYNTHETIC_LABEL,
    FINANCIAL_HEADERS,
    OPERATIONAL_DATASETS,
    TEMPLATE_DATASETS,
    current_template_csv,
    current_template_hashes,
    demo_manifest_hash,
    next_monday,
    prior_pack_csvs,
)
from businessflow.position_reviews.models import (
    PositionDemoSeed,
    PositionFinancialImport,
    PositionReviewBrief,
    PositionReviewDisposition,
    PositionReviewOperationalImport,
)
from businessflow.position_reviews.schemas import (
    FinancialImportConfirmRequest,
    FinancialImportListResponse,
    FinancialImportResponse,
    FinancialImportStatus,
    FinancialImportSummary,
    OperationalImportSelection,
    PositionDemoSeedResponse,
    PositionDemoWorkspaceResponse,
    PositionReviewCreateRequest,
    PositionReviewDispositionRequest,
    PositionReviewDispositionResponse,
    PositionReviewListResponse,
    PositionReviewOperationalImportResponse,
    PositionReviewResponse,
    PositionReviewSummary,
    ReviewDisposition,
)
from businessflow.services.company_context_access import require_company_context_access

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._~:-]{16,128}$")
FINANCIAL_SCHEMA_VERSION = "businessflow.company-finance-export.v1"
FINANCE_REQUIRED_HEADERS = frozenset(FINANCIAL_HEADERS[:-1])
FINANCE_ALLOWED_HEADERS = frozenset(FINANCIAL_HEADERS)
FINANCIAL_IMPORT_RETENTION_NOTE = (
    "Original file bytes are not retained. Parsed source-native cell values, worksheet/row "
    "locators, row hashes, file hash, and validation report are retained in this synthetic "
    "demo workspace."
)


def _safe_file_name(filename: str | None) -> str:
    if not filename:
        raise ApiError(422, "invalid_file_name", "A CSV file name is required.")
    leaf = filename.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[\x00-\x1f\x7f]", "_", leaf).strip(" .")
    if not safe or len(safe) > 255 or not safe.casefold().endswith(".csv"):
        raise ApiError(422, "invalid_file_name", "The finance export must be a CSV file.")
    return safe


def _idempotency_hash(idempotency_key: str) -> str:
    if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        raise ApiError(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must be 16 to 128 safe ASCII characters.",
        )
    return sha256(idempotency_key.encode("ascii")).hexdigest()


def _row_hash(worksheet_name: str, row_number: int, values: list[str | None]) -> str:
    payload = json.dumps(
        {"worksheet": worksheet_name, "row_number": row_number, "values": values},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _finance_issue(code: str, message: str, field: str | None = None) -> dict[str, object]:
    return {"code": code, "field": field, "message": message, "severity": "error"}


def _valid_currency(value: str | None) -> bool:
    if value is None or len(value) != 3 or not value.isascii() or not value.isupper():
        return False
    try:
        currency = Currency[value]
    except KeyError:
        return False
    return currency.name != "XXX"


def _valid_timestamp(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _validate_financial_rows(
    file_name: str, content: bytes, max_rows: int
) -> tuple[str, list[dict[str, object]], dict[str, object], str]:
    try:
        parsed = parse_file(file_name, content, max_rows)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    if parsed.file_format != "csv" or len(parsed.worksheets) != 1:
        raise ApiError(
            415,
            "unsupported_financial_export",
            "M10 accepts one UTF-8 CSV finance worksheet so exact source value text is preserved.",
        )
    sheet = parsed.worksheets[0]
    header_set = set(sheet.headers)
    if not FINANCE_REQUIRED_HEADERS.issubset(header_set) or not header_set.issubset(
        FINANCE_ALLOWED_HEADERS
    ):
        raise ApiError(
            422,
            "financial_export_schema_invalid",
            (
                "Use the M10 finance CSV template; every column must match its "
                "source-native field name."
            ),
        )
    if not parsed.total_rows:
        raise ApiError(422, "empty_financial_export", "The finance CSV contains no data rows.")

    observations: list[dict[str, object]] = []
    label_keys: set[str] = set()
    issue_counts: Counter[str] = Counter()
    for row in sheet.rows:
        raw = {header: row.values[index] for index, header in enumerate(sheet.headers)}
        source_report = raw.get("source_report")
        exported_at = raw.get("exported_at")
        source_label = raw.get("source_label")
        source_value_text = raw.get("source_value_text")
        currency_code = raw.get("currency_code")
        period_start = raw.get("period_start")
        period_end = raw.get("period_end")
        source_record_id = raw.get("source_record_id")
        issues: list[dict[str, object]] = []

        for field_name, value, limit in (
            ("source_report", source_report, 128),
            ("source_label", source_label, 128),
            ("source_value_text", source_value_text, 256),
        ):
            if value is None or not value.strip():
                issues.append(
                    _finance_issue(
                        "required_value_missing", "A required source value is missing.", field_name
                    )
                )
            elif len(value) > limit:
                issues.append(
                    _finance_issue(
                        "value_too_long", "A source value exceeds the supported length.", field_name
                    )
                )
        if source_record_id is not None and len(source_record_id) > 128:
            issues.append(
                _finance_issue(
                    "value_too_long",
                    "The source record identifier is too long.",
                    "source_record_id",
                )
            )
        if not _valid_timestamp(exported_at):
            issues.append(
                _finance_issue(
                    "invalid_export_timestamp",
                    (
                        "Use an ISO-8601 export timestamp that includes a timezone, such as "
                        "2026-11-21T12:00:00Z."
                    ),
                    "exported_at",
                )
            )
        if not _valid_currency(currency_code):
            issues.append(
                _finance_issue(
                    "invalid_currency_code",
                    "Use a supported uppercase ISO 4217 currency code.",
                    "currency_code",
                )
            )
        start_date = _valid_iso_date(period_start)
        end_date = _valid_iso_date(period_end)
        if start_date is None:
            issues.append(
                _finance_issue(
                    "invalid_period_date", "Use an ISO date such as 2026-11-21.", "period_start"
                )
            )
        if end_date is None:
            issues.append(
                _finance_issue(
                    "invalid_period_date", "Use an ISO date such as 2026-11-21.", "period_end"
                )
            )
        if start_date is not None and end_date is not None and end_date < start_date:
            issues.append(
                _finance_issue(
                    "invalid_period_order",
                    "The source period end must not precede its start.",
                    "period_end",
                )
            )
        if source_label is not None and source_label.strip():
            label_keys.add(source_label.casefold())
        issue_counts.update(str(issue["code"]) for issue in issues)
        observations.append(
            {
                "row_number": row.row_number,
                "row_hash": _row_hash(sheet.name, row.row_number, row.values),
                "worksheet_name": sheet.name,
                "source_report": source_report,
                "exported_at": exported_at,
                "source_label": source_label,
                "source_value_text": source_value_text,
                "currency_code": currency_code,
                "period_start": period_start,
                "period_end": period_end,
                "source_record_id": source_record_id,
                "issues": issues,
            }
        )

    if len(label_keys) > 3:
        for observation in observations:
            issue = _finance_issue(
                "too_many_source_labels",
                "Select no more than three source labels in one finance export.",
                "source_label",
            )
            cast(list[dict[str, object]], observation["issues"]).append(issue)
            issue_counts.update(["too_many_source_labels"])

    invalid_rows = sum(bool(row["issues"]) for row in observations)
    currencies = sorted(
        {
            str(row["currency_code"])
            for row in observations
            if not row["issues"] and isinstance(row["currency_code"], str)
        }
    )
    validation_report: dict[str, object] = {
        "schema_version": FINANCIAL_SCHEMA_VERSION,
        "worksheet_name": sheet.name,
        "total_rows": len(observations),
        "valid_rows": len(observations) - invalid_rows,
        "invalid_rows": invalid_rows,
        "source_label_count": len(label_keys),
        "currency_codes_present": currencies,
        "issue_counts": [
            {"code": code, "count": count} for code, count in sorted(issue_counts.items())
        ],
        "financial_calculations_performed": False,
        "currency_conversion_performed": False,
    }
    file_hash = sha256(content).hexdigest()
    validation_hash = content_hash(
        {
            "schema_version": FINANCIAL_SCHEMA_VERSION,
            "file_hash": file_hash,
            "worksheet_name": sheet.name,
            "observations": observations,
        }
    )
    return sheet.name, observations, validation_report, validation_hash


def _demo_seed_for_company(
    session: Session, tenant_id: UUID, company_id: UUID
) -> PositionDemoSeed | None:
    return session.scalar(
        select(PositionDemoSeed).where(
            PositionDemoSeed.tenant_id == tenant_id,
            PositionDemoSeed.company_id == company_id,
        )
    )


def _require_demo_seed(session: Session, tenant_id: UUID, company_id: UUID) -> PositionDemoSeed:
    seed = _demo_seed_for_company(session, tenant_id, company_id)
    if seed is None:
        raise ApiError(
            409,
            "synthetic_demo_workspace_required",
            "Create the explicitly labeled synthetic demo workspace before using M10.",
        )
    return seed


def _metric_run_payload(run: MetricRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "company_id": str(run.company_id),
        "start_week_date": run.start_week_date.isoformat(),
        "end_week_date": run.end_week_date.isoformat(),
        "week_count": run.week_count,
        "metric_catalog_version": run.metric_catalog_version,
        "calculation_version": run.calculation_version,
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
        "input_snapshot": run.input_snapshot,
        "result_snapshot": run.result_snapshot,
    }


def _seed_payload(seed: PositionDemoSeed) -> dict[str, object]:
    return {
        "id": str(seed.id),
        "seed_version": seed.seed_version,
        "manifest_hash": seed.manifest_hash,
        "current_template_hashes": seed.current_template_hashes,
        "synthetic_label": seed.synthetic_label,
        "baseline_metric_run_id": str(seed.baseline_metric_run_id),
        "baseline_start_week_date": seed.baseline_start_week_date.isoformat(),
        "baseline_end_week_date": seed.baseline_end_week_date.isoformat(),
        "current_start_week_date": seed.current_start_week_date.isoformat(),
    }


def _demo_seed_response(
    session: Session, seed: PositionDemoSeed, *, reused: bool
) -> PositionDemoSeedResponse:
    baseline = session.scalar(
        select(MetricRun).where(
            MetricRun.id == seed.baseline_metric_run_id,
            MetricRun.tenant_id == seed.tenant_id,
            MetricRun.company_id == seed.company_id,
        )
    )
    if baseline is None:
        raise ApiError(
            409, "demo_baseline_unavailable", "The saved synthetic baseline is unavailable."
        )
    return PositionDemoSeedResponse(
        id=seed.id,
        tenant_id=seed.tenant_id,
        company_id=seed.company_id,
        created_by_user_id=seed.created_by_user_id,
        baseline_metric_run_id=baseline.id,
        seed_version=seed.seed_version,
        manifest_hash=seed.manifest_hash,
        current_template_hashes=seed.current_template_hashes,
        synthetic_label=seed.synthetic_label,
        baseline_start_week_date=seed.baseline_start_week_date,
        baseline_end_week_date=seed.baseline_end_week_date,
        current_start_week_date=seed.current_start_week_date,
        baseline_metric_catalog_version=baseline.metric_catalog_version,
        baseline_calculation_version=baseline.calculation_version,
        baseline_input_hash=baseline.input_hash,
        baseline_result_hash=baseline.result_hash,
        created_at=seed.created_at,
        reused=reused,
    )


def get_demo_workspace(
    session: Session, user: UserAccount, tenant_id: UUID
) -> PositionDemoWorkspaceResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    seed = _demo_seed_for_company(session, tenant_id, access.company.id)
    return PositionDemoWorkspaceResponse(
        synthetic_label=DEMO_SYNTHETIC_LABEL,
        demo_seed=_demo_seed_response(session, seed, reused=True) if seed is not None else None,
        next_step="upload_current_operational_pack"
        if seed is not None
        else "seed_synthetic_workspace",
    )


def create_demo_workspace(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    request_id: UUID,
    settings: Settings,
) -> PositionDemoSeedResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    company = session.scalar(
        select(Company)
        .where(
            Company.id == access.company.id,
            Company.tenant_id == tenant_id,
            Company.status == "active",
        )
        .with_for_update()
    )
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id, Tenant.status == "active"))
    if company is None or tenant is None:
        raise ApiError(404, "company_not_found", "Company was not found.")

    existing_seed = _demo_seed_for_company(session, tenant_id, company.id)
    if existing_seed is not None:
        return _demo_seed_response(session, existing_seed, reused=True)

    if "demo" not in tenant.name.casefold() or "synthetic" not in company.legal_name.casefold():
        raise ApiError(
            409,
            "demo_company_must_be_labeled",
            (
                "Name the tenant with 'Demo' and the Company with 'Synthetic' before "
                "seeding demo records."
            ),
        )

    scoped_models = (
        (BusinessUnit, BusinessUnit.tenant_id == tenant_id, BusinessUnit.company_id == company.id),
        (Department, Department.tenant_id == tenant_id, Department.company_id == company.id),
        (Employee, Employee.tenant_id == tenant_id, Employee.company_id == company.id),
        (
            EmployeeSkill,
            EmployeeSkill.tenant_id == tenant_id,
            EmployeeSkill.company_id == company.id,
        ),
        (
            EmployeeWeeklyCapacity,
            EmployeeWeeklyCapacity.tenant_id == tenant_id,
            EmployeeWeeklyCapacity.company_id == company.id,
        ),
        (Project, Project.tenant_id == tenant_id, Project.company_id == company.id),
        (
            ResourceAssignment,
            ResourceAssignment.tenant_id == tenant_id,
            ResourceAssignment.company_id == company.id,
        ),
        (Skill, Skill.tenant_id == tenant_id, None),
        (DataImport, DataImport.tenant_id == tenant_id, DataImport.company_id == company.id),
        (MetricRun, MetricRun.tenant_id == tenant_id, MetricRun.company_id == company.id),
    )
    for model, tenant_filter, company_filter in scoped_models:
        filters = [tenant_filter]
        if company_filter is not None:
            filters.append(company_filter)
        if session.scalar(select(model.id).where(*filters).limit(1)) is not None:
            raise ApiError(
                409,
                "demo_company_not_empty",
                (
                    "Synthetic demo seeding is allowed only in a company with no workforce, "
                    "project, import, or metric data."
                ),
            )

    current_start = next_monday()
    baseline_start = current_start - timedelta(days=28)
    baseline_end = current_start - timedelta(days=1)
    prior_files = prior_pack_csvs(current_start)
    dataset_names = {
        "employees": "synthetic_demo_employees.csv",
        "projects": "synthetic_demo_projects.csv",
        "weekly_capacity": "synthetic_demo_weekly_capacity.csv",
        "resource_assignments": "synthetic_demo_resource_assignments.csv",
    }
    for dataset_type in OPERATIONAL_DATASETS:
        content = prior_files[dataset_type]
        staged = stage_import(
            session,
            user,
            tenant_id,
            BytesIO(content),
            dataset_names[dataset_type],
            f"m10-demo-v1-{dataset_type}",
            request_id,
            settings,
        )
        mapping = ImportMappingRequest(
            dataset_type=dataset_type,
            worksheet_name="CSV",
            field_mapping={header: header for header in _csv_headers(content)},
            ignored_columns=[],
        )
        set_import_mapping(session, user, tenant_id, staged.id, mapping, request_id)
        report = validate_import(session, user, tenant_id, staged.id, request_id)
        validation_hash = report.import_record.validation_hash
        if validation_hash is None or report.import_record.counts.invalid_rows:
            raise ApiError(
                409,
                "synthetic_seed_validation_failed",
                "The versioned synthetic seed pack failed its operational validation.",
            )
        confirm_import(
            session,
            user,
            tenant_id,
            staged.id,
            ImportConfirmRequest(
                validation_hash=validation_hash,
                allow_partial=False,
                acknowledge_warnings=False,
            ),
            request_id,
        )
        applied = apply_import(session, user, tenant_id, staged.id, request_id)
        if applied.import_record.status not in {"applied", "applied_with_warnings"}:
            raise ApiError(
                409,
                "synthetic_seed_apply_failed",
                "The synthetic seed pack could not be applied completely.",
            )

    baseline = create_metric_run(
        session,
        user,
        tenant_id,
        baseline_start,
        4,
        request_id,
    )
    if baseline.reliability_status != "complete":
        raise ApiError(
            409,
            "synthetic_seed_baseline_incomplete",
            "The synthetic baseline did not produce complete confirmed capacity coverage.",
        )
    seed = PositionDemoSeed(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=company.id,
        created_by_user_id=user.id,
        baseline_metric_run_id=baseline.id,
        seed_version=DEMO_SEED_VERSION,
        manifest_hash=demo_manifest_hash(current_start),
        current_template_hashes=current_template_hashes(current_start),
        synthetic_label=DEMO_SYNTHETIC_LABEL,
        baseline_start_week_date=baseline_start,
        baseline_end_week_date=baseline_end,
        current_start_week_date=current_start,
    )
    session.add(seed)
    session.flush()
    record_event(
        session,
        "position_review.demo_seeded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="position_demo_seed",
        target_id=seed.id,
        request_id=request_id,
        details={
            "seed_version": seed.seed_version,
            "manifest_hash": seed.manifest_hash,
            "baseline_metric_run_id": baseline.id,
            "baseline_input_hash": baseline.input_hash,
            "baseline_result_hash": baseline.result_hash,
            "baseline_start_week_date": baseline_start.isoformat(),
            "current_start_week_date": current_start.isoformat(),
        },
    )
    return _demo_seed_response(session, seed, reused=False)


def _csv_headers(content: bytes) -> list[str]:
    try:
        parsed = parse_file("synthetic_seed.csv", content, 10_000)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    if len(parsed.worksheets) != 1:
        raise ApiError(409, "synthetic_seed_invalid", "The synthetic seed file is invalid.")
    return parsed.worksheets[0].headers


def get_demo_template(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    dataset_type: str,
) -> tuple[bytes, str]:
    if dataset_type not in TEMPLATE_DATASETS:
        raise ApiError(404, "demo_template_not_found", "The synthetic template was not found.")
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    seed = _require_demo_seed(session, tenant_id, access.company.id)
    content = current_template_csv(dataset_type, seed.current_start_week_date)
    expected_hash = seed.current_template_hashes.get(dataset_type)
    if expected_hash is None or sha256(content).hexdigest() != expected_hash:
        raise ApiError(
            409,
            "synthetic_template_version_mismatch",
            "The built-in template no longer matches the immutable synthetic seed version.",
        )
    return content, f"synthetic_demo_{dataset_type}.csv"


def _finance_summary(import_record: PositionFinancialImport) -> FinancialImportSummary:
    report = import_record.validation_report
    invalid_rows = report.get("invalid_rows", 0)
    return FinancialImportSummary(
        id=import_record.id,
        tenant_id=import_record.tenant_id,
        company_id=import_record.company_id,
        uploaded_by_user_id=import_record.uploaded_by_user_id,
        confirmed_by_user_id=import_record.confirmed_by_user_id,
        file_name=import_record.file_name,
        file_format="csv",
        file_hash=import_record.file_hash,
        file_size_bytes=import_record.file_size_bytes,
        worksheet_name=import_record.worksheet_name,
        schema_version=import_record.schema_version,
        status=cast(FinancialImportStatus, import_record.status),
        row_count=import_record.row_count,
        invalid_row_count=cast(int, invalid_rows),
        validation_hash=import_record.validation_hash,
        created_at=import_record.created_at,
        confirmed_at=import_record.confirmed_at,
    )


def _finance_response(import_record: PositionFinancialImport) -> FinancialImportResponse:
    return FinancialImportResponse(
        financial_import=_finance_summary(import_record),
        validation_report=import_record.validation_report,
        observations=import_record.observations,
        retention_note=FINANCIAL_IMPORT_RETENTION_NOTE,
    )


def stage_financial_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    upload: BinaryIO,
    filename: str | None,
    idempotency_key: str,
    request_id: UUID,
    settings: Settings,
) -> FinancialImportResponse:
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    seed = _require_demo_seed(session, tenant_id, access.company.id)
    key_hash = _idempotency_hash(idempotency_key)
    safe_name = _safe_file_name(filename)
    try:
        content = read_limited_upload(upload, settings.import_max_file_bytes)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    file_hash = sha256(content).hexdigest()
    expected_demo_hash = seed.current_template_hashes.get("financial_export")
    if expected_demo_hash is None or file_hash != expected_demo_hash:
        raise ApiError(
            422,
            "synthetic_financial_template_required",
            (
                "This internal M10 slice accepts only the exact built-in synthetic finance "
                "template; do not upload customer or confidential data."
            ),
        )
    prior = session.scalar(
        select(PositionFinancialImport).where(
            PositionFinancialImport.tenant_id == tenant_id,
            PositionFinancialImport.company_id == access.company.id,
            PositionFinancialImport.idempotency_key_hash == key_hash,
        )
    )
    if prior is not None:
        if prior.uploaded_by_user_id != user.id or prior.file_hash != file_hash:
            raise ApiError(
                409,
                "idempotency_key_conflict",
                "This Idempotency-Key is already associated with a different finance upload.",
            )
        return _finance_response(prior)

    worksheet_name, observations, report, validation_hash = _validate_financial_rows(
        safe_name, content, settings.import_max_rows
    )
    import_record = PositionFinancialImport(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        uploaded_by_user_id=user.id,
        idempotency_key_hash=key_hash,
        file_name=safe_name,
        file_format="csv",
        file_hash=file_hash,
        file_size_bytes=len(content),
        worksheet_name=worksheet_name,
        schema_version=FINANCIAL_SCHEMA_VERSION,
        status="invalid" if report["invalid_rows"] else "validated",
        row_count=len(observations),
        validation_hash=validation_hash,
        validation_report=report,
        observations=observations,
    )
    try:
        with session.begin_nested():
            session.add(import_record)
            session.flush()
    except IntegrityError as exc:
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint_name != "uq_position_financial_imports_idempotency":
            raise
        prior = session.scalar(
            select(PositionFinancialImport).where(
                PositionFinancialImport.tenant_id == tenant_id,
                PositionFinancialImport.company_id == access.company.id,
                PositionFinancialImport.idempotency_key_hash == key_hash,
            )
        )
        if prior is None or prior.uploaded_by_user_id != user.id or prior.file_hash != file_hash:
            raise ApiError(
                409,
                "idempotency_key_conflict",
                "This Idempotency-Key is already associated with a different finance upload.",
            ) from None
        return _finance_response(prior)

    record_event(
        session,
        "position_review.financial_imported",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="position_financial_import",
        target_id=import_record.id,
        request_id=request_id,
        outcome="failure" if report["invalid_rows"] else "success",
        details={
            "file_hash": file_hash,
            "schema_version": FINANCIAL_SCHEMA_VERSION,
            "row_count": len(observations),
            "invalid_row_count": report["invalid_rows"],
        },
    )
    return _finance_response(import_record)


def _recomputed_financial_validation_hash(import_record: PositionFinancialImport) -> str:
    return content_hash(
        {
            "schema_version": import_record.schema_version,
            "file_hash": import_record.file_hash,
            "worksheet_name": import_record.worksheet_name,
            "observations": import_record.observations,
        }
    )


def confirm_financial_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
    body: FinancialImportConfirmRequest,
    request_id: UUID,
) -> FinancialImportResponse:
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    _require_demo_seed(session, tenant_id, access.company.id)
    import_record = session.scalar(
        select(PositionFinancialImport)
        .where(
            PositionFinancialImport.id == import_id,
            PositionFinancialImport.tenant_id == tenant_id,
            PositionFinancialImport.company_id == access.company.id,
        )
        .with_for_update()
    )
    if import_record is None:
        raise ApiError(404, "financial_import_not_found", "The financial import was not found.")
    if import_record.validation_hash != body.validation_hash:
        raise ApiError(
            409,
            "financial_import_validation_hash_mismatch",
            "The confirmation hash does not match the staged validation report.",
        )
    if import_record.status == "confirmed":
        return _finance_response(import_record)
    if import_record.status != "validated":
        raise ApiError(
            409,
            "financial_import_not_validated",
            "Correct the invalid source rows and upload a new finance CSV before confirming.",
        )
    if _recomputed_financial_validation_hash(import_record) != import_record.validation_hash:
        raise ApiError(
            409,
            "financial_import_snapshot_invalid",
            "The staged finance snapshot failed its integrity check.",
        )
    import_record.status = "confirmed"
    import_record.confirmed_by_user_id = user.id
    import_record.confirmed_at = datetime.now(UTC)
    record_event(
        session,
        "position_review.financial_import_confirmed",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="position_financial_import",
        target_id=import_record.id,
        request_id=request_id,
        details={
            "file_hash": import_record.file_hash,
            "validation_hash": import_record.validation_hash,
        },
    )
    session.flush()
    return _finance_response(import_record)


def get_financial_import(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    import_id: UUID,
) -> FinancialImportResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    _require_demo_seed(session, tenant_id, access.company.id)
    import_record = session.scalar(
        select(PositionFinancialImport).where(
            PositionFinancialImport.id == import_id,
            PositionFinancialImport.tenant_id == tenant_id,
            PositionFinancialImport.company_id == access.company.id,
        )
    )
    if import_record is None:
        raise ApiError(404, "financial_import_not_found", "The financial import was not found.")
    return _finance_response(import_record)


def list_financial_imports(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
) -> FinancialImportListResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    _require_demo_seed(session, tenant_id, access.company.id)
    filters = (
        PositionFinancialImport.tenant_id == tenant_id,
        PositionFinancialImport.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(PositionFinancialImport).where(*filters))
        or 0
    )
    records = session.scalars(
        select(PositionFinancialImport)
        .where(*filters)
        .order_by(PositionFinancialImport.created_at.desc(), PositionFinancialImport.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return FinancialImportListResponse(
        items=[_finance_summary(item) for item in records], total=total, limit=limit, offset=offset
    )


def _import_ids_from_metric_input(input_snapshot: dict[str, object]) -> set[UUID]:
    result: set[UUID] = set()
    for collection_name in (
        "employees",
        "weekly_capacity",
        "resource_assignments",
        "skills",
        "employee_skills",
    ):
        rows = input_snapshot.get(collection_name)
        if not isinstance(rows, list):
            continue
        for raw_record in cast(list[object], rows):
            if not isinstance(raw_record, dict) or not isinstance(raw_record.get("lineage"), list):
                continue
            for raw_reference in cast(list[object], raw_record["lineage"]):
                if not isinstance(raw_reference, dict):
                    continue
                raw_id = raw_reference.get("import_id")
                if isinstance(raw_id, str):
                    try:
                        result.add(UUID(raw_id))
                    except ValueError:
                        continue
    return result


def _source_import_catalog(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    import_ids: set[UUID],
) -> list[dict[str, object]]:
    if not import_ids:
        return []
    records = session.scalars(
        select(DataImport).where(
            DataImport.tenant_id == tenant_id,
            DataImport.company_id == company_id,
            DataImport.id.in_(sorted(import_ids, key=str)),
        )
    )
    by_id = {record.id: record for record in records}
    result: list[dict[str, object]] = []
    for import_id in sorted(import_ids, key=str):
        record = by_id.get(import_id)
        if record is None:
            continue
        result.append(
            {
                "import_id": str(record.id),
                "file_name": record.file_name,
                "dataset_type": record.dataset_type,
                "file_hash": record.file_hash,
                "validation_hash": record.validation_hash,
                "mapping_version": record.mapping_version,
                "status": record.status,
            }
        )
    return result


def _validate_operational_imports(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    seed: PositionDemoSeed,
    selection: OperationalImportSelection,
    current_run: MetricRun,
) -> list[dict[str, object]]:
    selected_ids = selection.by_dataset()
    if len(set(selected_ids.values())) != len(selected_ids):
        raise ApiError(
            422,
            "operational_import_reused",
            "Select a distinct, applied M4 import for each operational dataset.",
        )
    records = list(
        session.scalars(
            select(DataImport).where(
                DataImport.tenant_id == tenant_id,
                DataImport.company_id == company_id,
                DataImport.id.in_(sorted(set(selected_ids.values()), key=str)),
            )
        )
    )
    by_id = {record.id: record for record in records}
    if len(by_id) != len(selected_ids):
        raise ApiError(404, "operational_import_not_found", "An operational import was not found.")

    canonical_types = {
        "employees": "employee",
        "projects": "project",
        "weekly_capacity": "weekly_capacity",
        "resource_assignments": "resource_assignment",
    }
    expected_demo_hashes = seed.current_template_hashes
    result_rows: list[dict[str, object]] = []
    links_by_dataset: dict[str, list[DataImportRecordLink]] = {}
    for dataset_type, import_id in selected_ids.items():
        record = by_id[import_id]
        if record.dataset_type != dataset_type:
            raise ApiError(
                409,
                "operational_import_dataset_mismatch",
                "The selected M4 import does not match its operational dataset.",
            )
        if record.status not in {"applied", "applied_with_warnings"}:
            raise ApiError(
                409,
                "operational_import_not_applied",
                "Validate, explicitly confirm, and apply all four operational imports first.",
            )
        if record.file_hash != expected_demo_hashes[dataset_type]:
            raise ApiError(
                409,
                "synthetic_operational_template_required",
                (
                    "This internal M10 slice accepts only the exact built-in synthetic "
                    "operational template for each dataset."
                ),
            )
        expected_mapping = {
            header: header
            for header in _csv_headers(
                current_template_csv(dataset_type, seed.current_start_week_date)
            )
        }
        if (
            record.selected_worksheet != "CSV"
            or record.field_mapping != expected_mapping
            or record.ignored_columns
        ):
            raise ApiError(
                409,
                "synthetic_operational_mapping_required",
                (
                    "Each synthetic operational CSV must use its exact same-name field "
                    "mapping with no ignored columns."
                ),
            )
        if (
            record.validation_hash is None
            or record.mapping_version < 1
            or record.created_at <= seed.created_at
        ):
            raise ApiError(
                409,
                "operational_import_not_current_demo_pack",
                "Use four validated operational imports created after the synthetic demo seed.",
            )
        if record.invalid_rows:
            raise ApiError(
                409,
                "operational_import_contains_invalid_rows",
                "Correct every invalid operational row before creating a review.",
            )
        links = list(
            session.scalars(
                select(DataImportRecordLink).where(
                    DataImportRecordLink.tenant_id == tenant_id,
                    DataImportRecordLink.company_id == company_id,
                    DataImportRecordLink.import_id == import_id,
                    DataImportRecordLink.canonical_type == canonical_types[dataset_type],
                )
            )
        )
        if not links:
            raise ApiError(
                409,
                "operational_import_has_no_canonical_links",
                (
                    "Every operational import must retain at least one company-scoped "
                    "canonical source-row link."
                ),
            )
        if dataset_type in {"weekly_capacity", "resource_assignments"} and (
            record.duplicate_rows or record.warning_rows
        ):
            raise ApiError(
                409,
                "period_import_was_not_applied",
                (
                    "A capacity or assignment row was skipped or warned. Use a fresh period "
                    "and resolve all warnings; M4 never overwrites existing facts."
                ),
            )
        if dataset_type in {"employees", "projects"}:
            issue_rows = session.scalars(
                select(DataImportRow).where(
                    DataImportRow.tenant_id == tenant_id,
                    DataImportRow.company_id == company_id,
                    DataImportRow.import_id == import_id,
                )
            )
            for source_row in issue_rows:
                issue_codes = {
                    str(issue.get("code")) for issue in source_row.issues if isinstance(issue, dict)
                }
                if issue_codes - {"duplicate_existing", "duplicate_in_file"}:
                    raise ApiError(
                        409,
                        "roster_import_has_unresolved_warning",
                        (
                            "Employee and project roster imports may only contain explicit "
                            "duplicate warnings; no existing records are overwritten."
                        ),
                    )
        links_by_dataset[dataset_type] = links
        result_rows.append(
            {
                "dataset_type": dataset_type,
                "import_id": str(record.id),
                "file_name": record.file_name,
                "file_hash": record.file_hash,
                "validation_hash": record.validation_hash,
                "mapping_version": record.mapping_version,
                "status": record.status,
                "total_rows": record.total_rows,
                "invalid_rows": record.invalid_rows,
                "warning_rows": record.warning_rows,
                "duplicate_rows": record.duplicate_rows,
                "created_rows": record.created_rows,
                "skipped_rows": record.skipped_rows,
            }
        )

    metric_input = current_run.input_snapshot
    selected_employee_import_id = str(selected_ids["employees"])
    for employee in _dict_rows(metric_input.get("employees")):
        if employee.get("employment_status") == "active" and not any(
            reference.get("import_id") == selected_employee_import_id
            for reference in _dict_rows(employee.get("lineage"))
        ):
            raise ApiError(
                409,
                "employee_import_does_not_cover_active_cohort",
                (
                    "The selected employee import does not resolve every active employee "
                    "in the M5 input snapshot."
                ),
            )

    project_ids = {link.canonical_id for link in links_by_dataset["projects"]}
    for assignment in _dict_rows(metric_input.get("resource_assignments")):
        if assignment.get("status") not in {"committed", "proposed"}:
            continue
        try:
            assignment_start = date.fromisoformat(str(assignment.get("start_date")))
            assignment_end = date.fromisoformat(str(assignment.get("end_date")))
        except ValueError:
            continue
        if (
            assignment_start > current_run.end_week_date
            or assignment_end < current_run.start_week_date
        ):
            continue
        assignment_lineage = _dict_rows(assignment.get("lineage"))
        if not any(
            reference.get("import_id") == str(selected_ids["resource_assignments"])
            for reference in assignment_lineage
        ):
            raise ApiError(
                409,
                "assignment_import_does_not_cover_current_plan",
                (
                    "A current-period assignment in the M5 snapshot is not linked to the "
                    "selected resource-assignment import."
                ),
            )
        try:
            project_id = UUID(str(assignment.get("project_id")))
        except ValueError:
            raise ApiError(
                409,
                "assignment_project_reference_invalid",
                "A current-period assignment has an invalid project reference.",
            ) from None
        if project_id not in project_ids:
            raise ApiError(
                409,
                "project_import_does_not_cover_current_plan",
                (
                    "The selected project import does not resolve every current-period "
                    "assignment project."
                ),
            )

    selected_capacity_import_id = str(selected_ids["weekly_capacity"])
    for capacity in _dict_rows(metric_input.get("weekly_capacity")):
        week_start = str(capacity.get("week_start_date"))
        if not (
            current_run.start_week_date.isoformat()
            <= week_start
            <= current_run.end_week_date.isoformat()
        ):
            continue
        if not any(
            reference.get("import_id") == selected_capacity_import_id
            for reference in _dict_rows(capacity.get("lineage"))
        ):
            raise ApiError(
                409,
                "capacity_import_does_not_cover_current_period",
                (
                    "A current-period M5 capacity fact is not linked to the selected "
                    "weekly-capacity import."
                ),
            )
    return sorted(result_rows, key=lambda item: str(item.get("dataset_type")))


def _dict_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, object], item)
        for item in cast(list[object], value)
        if isinstance(item, dict)
    ]


def _collect_run_import_ids(run: MetricRun) -> set[UUID]:
    return _import_ids_from_metric_input(run.input_snapshot)


def _request_hash(body: PositionReviewCreateRequest) -> str:
    return content_hash(body.model_dump(mode="json"))


def _financial_input_snapshot(import_record: PositionFinancialImport) -> dict[str, object]:
    first = next(
        (
            row
            for row in import_record.observations
            if isinstance(row.get("issues"), list) and not row["issues"]
        ),
        {},
    )
    return {
        "id": str(import_record.id),
        "file_name": import_record.file_name,
        "file_hash": import_record.file_hash,
        "validation_hash": import_record.validation_hash,
        "schema_version": import_record.schema_version,
        "status": import_record.status,
        "source_report": first.get("source_report"),
        "exported_at": first.get("exported_at"),
        "observations": import_record.observations,
    }


def _brief_summary(session: Session, review: PositionReviewBrief) -> PositionReviewSummary:
    dispositions = (
        session.scalar(
            select(func.count())
            .select_from(PositionReviewDisposition)
            .where(
                PositionReviewDisposition.tenant_id == review.tenant_id,
                PositionReviewDisposition.company_id == review.company_id,
                PositionReviewDisposition.review_id == review.id,
            )
        )
        or 0
    )
    state = review.result_snapshot.get("state")
    return PositionReviewSummary(
        id=review.id,
        tenant_id=review.tenant_id,
        company_id=review.company_id,
        requested_by_user_id=review.requested_by_user_id,
        baseline_metric_run_id=review.baseline_metric_run_id,
        current_metric_run_id=review.current_metric_run_id,
        financial_import_id=review.financial_import_id,
        start_week_date=review.start_week_date,
        end_week_date=review.end_week_date,
        calculation_version=review.calculation_version,
        input_hash=review.input_hash,
        result_hash=review.result_hash,
        result_state=str(state or "unknown"),
        created_at=review.created_at,
        disposition_count=dispositions,
    )


def _verify_brief_integrity(
    session: Session,
    review: PositionReviewBrief,
) -> list[dict[str, object]]:
    baseline = session.scalar(
        select(MetricRun).where(
            MetricRun.id == review.baseline_metric_run_id,
            MetricRun.tenant_id == review.tenant_id,
            MetricRun.company_id == review.company_id,
        )
    )
    current = session.scalar(
        select(MetricRun).where(
            MetricRun.id == review.current_metric_run_id,
            MetricRun.tenant_id == review.tenant_id,
            MetricRun.company_id == review.company_id,
        )
    )
    if baseline is None or current is None:
        raise ApiError(
            409, "position_review_integrity_error", "A bound M5 metric run is unavailable."
        )
    for run in (baseline, current):
        if (
            content_hash(run.input_snapshot) != run.input_hash
            or content_hash(run.result_snapshot) != run.result_hash
        ):
            raise ApiError(
                409,
                "position_review_integrity_error",
                "A bound M5 metric snapshot failed its content-hash check.",
            )
    stored = review.input_snapshot
    demo_seed = stored.get("demo_seed")
    operational_imports = stored.get("operational_imports")
    financial_import = stored.get("financial_import")
    attestations = stored.get("attestations")
    source_catalog = stored.get("source_import_catalog")
    if not all(
        isinstance(value, (dict, list))
        for value in (demo_seed, operational_imports, attestations, source_catalog)
    ):
        raise ApiError(
            409, "position_review_integrity_error", "The saved M10 input snapshot is invalid."
        )
    input_snapshot, result_snapshot = build_position_review_payload(
        _metric_run_payload(baseline),
        _metric_run_payload(current),
        cast(dict[str, object], demo_seed),
        _dict_rows(operational_imports),
        cast(dict[str, object], financial_import) if isinstance(financial_import, dict) else None,
        cast(dict[str, object], attestations),
        _dict_rows(source_catalog),
    )
    input_hash, result_hash = position_review_hashes(input_snapshot, result_snapshot)
    if (
        input_hash != review.input_hash
        or result_hash != review.result_hash
        or input_snapshot != review.input_snapshot
        or result_snapshot != review.result_snapshot
    ):
        raise ApiError(
            409,
            "position_review_integrity_error",
            "The saved M10 result does not replay from its exact, versioned inputs.",
        )
    operational = session.scalars(
        select(PositionReviewOperationalImport)
        .where(
            PositionReviewOperationalImport.tenant_id == review.tenant_id,
            PositionReviewOperationalImport.company_id == review.company_id,
            PositionReviewOperationalImport.review_id == review.id,
        )
        .order_by(PositionReviewOperationalImport.dataset_type)
    )
    result: list[dict[str, object]] = []
    for link in operational:
        source = next(
            (
                item
                for item in _dict_rows(operational_imports)
                if item.get("dataset_type") == link.dataset_type
                and item.get("import_id") == str(link.import_id)
            ),
            {},
        )
        if (
            source.get("file_hash") != link.file_hash
            or source.get("validation_hash") != link.validation_hash
            or source.get("mapping_version") != link.mapping_version
        ):
            raise ApiError(
                409,
                "position_review_integrity_error",
                "An operational source link does not match the saved M10 input snapshot.",
            )
        result.append(
            {
                **source,
                "dataset_type": link.dataset_type,
                "import_id": link.import_id,
                "file_hash": link.file_hash,
                "validation_hash": link.validation_hash,
                "mapping_version": link.mapping_version,
                "status": str(source.get("status", "unknown")),
            }
        )
    return result


def _disposition_responses(
    session: Session, review: PositionReviewBrief
) -> list[PositionReviewDispositionResponse]:
    rows = session.scalars(
        select(PositionReviewDisposition)
        .where(
            PositionReviewDisposition.tenant_id == review.tenant_id,
            PositionReviewDisposition.company_id == review.company_id,
            PositionReviewDisposition.review_id == review.id,
        )
        .order_by(PositionReviewDisposition.created_at, PositionReviewDisposition.id)
    )
    result: list[PositionReviewDispositionResponse] = []
    for row in rows:
        actor = session.scalar(select(UserAccount).where(UserAccount.id == row.actor_user_id))
        result.append(
            PositionReviewDispositionResponse(
                id=row.id,
                tenant_id=row.tenant_id,
                company_id=row.company_id,
                review_id=row.review_id,
                actor_user_id=row.actor_user_id,
                actor_display_name=actor.display_name
                if actor is not None
                else str(row.actor_user_id),
                review_hash=row.review_hash,
                disposition=cast(ReviewDisposition, row.disposition),
                rationale=row.rationale,
                created_at=row.created_at,
            )
        )
    return result


def _position_review_response(
    session: Session, review: PositionReviewBrief
) -> PositionReviewResponse:
    operational_imports = _verify_brief_integrity(session, review)
    summary = _brief_summary(session, review)
    return PositionReviewResponse(
        **summary.model_dump(),
        demo_seed_id=review.demo_seed_id,
        metric_catalog_version=review.metric_catalog_version,
        metric_calculation_version=review.metric_calculation_version,
        input_snapshot=review.input_snapshot,
        result_snapshot=review.result_snapshot,
        operational_imports=[
            PositionReviewOperationalImportResponse.model_validate(item)
            for item in operational_imports
        ],
        dispositions=_disposition_responses(session, review),
    )


def create_position_review(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: PositionReviewCreateRequest,
    idempotency_key: str,
    request_id: UUID,
) -> PositionReviewResponse:
    access = require_company_context_access(
        session, user, tenant_id, "decision:write", request_id=request_id
    )
    require_tenant_access(session, user, tenant_id, "finance:read", request_id=request_id)
    # Serialize same-Company brief creation so concurrent Idempotency-Key retries replay.
    session.scalar(
        select(Company.id)
        .where(Company.id == access.company.id, Company.tenant_id == tenant_id)
        .with_for_update()
    )
    if not body.synthetic_data_acknowledged:
        raise ApiError(
            422,
            "synthetic_data_acknowledgement_required",
            "Confirm that every source in this M10 brief is synthetic demo data.",
        )
    seed = _require_demo_seed(session, tenant_id, access.company.id)
    if body.baseline_metric_run_id != seed.baseline_metric_run_id:
        raise ApiError(
            422,
            "baseline_metric_run_not_selected",
            "Select the immutable synthetic baseline M5 run shown in Create Review.",
        )
    if body.start_week_date != seed.current_start_week_date:
        raise ApiError(
            422,
            "current_period_mismatch",
            "Use the four-week current period generated for this synthetic demo workspace.",
        )

    key_hash = _idempotency_hash(idempotency_key)
    request_hash = _request_hash(body)
    selected_ids = body.operational_imports.by_dataset()
    if len(set(selected_ids.values())) != len(selected_ids):
        raise ApiError(
            422, "operational_import_reused", "Each operational dataset needs its own M4 import."
        )
    selected_records = list(
        session.scalars(
            select(DataImport).where(
                DataImport.tenant_id == tenant_id,
                DataImport.company_id == access.company.id,
                DataImport.id.in_(sorted(set(selected_ids.values()), key=str)),
            )
        )
    )
    selected_by_id = {record.id: record for record in selected_records}
    if len(selected_by_id) != len(selected_ids):
        raise ApiError(404, "operational_import_not_found", "An operational import was not found.")
    for dataset_type, import_id in selected_ids.items():
        record = selected_by_id[import_id]
        if record.dataset_type != dataset_type or record.status not in {
            "applied",
            "applied_with_warnings",
        }:
            raise ApiError(
                409,
                "operational_import_not_applied",
                "Validate, explicitly confirm, and apply four M4 imports for these datasets.",
            )
        if record.created_at <= seed.created_at:
            raise ApiError(
                409,
                "operational_import_not_current_demo_pack",
                "Create new current-period imports after the synthetic demo baseline was seeded.",
            )

    finance: PositionFinancialImport | None = None
    if body.financial_import_id is not None:
        finance = session.scalar(
            select(PositionFinancialImport).where(
                PositionFinancialImport.id == body.financial_import_id,
                PositionFinancialImport.tenant_id == tenant_id,
                PositionFinancialImport.company_id == access.company.id,
            )
        )
        if finance is None:
            raise ApiError(404, "financial_import_not_found", "The financial import was not found.")
        if finance.status != "confirmed" or finance.created_at <= seed.created_at:
            raise ApiError(
                409,
                "financial_import_not_confirmed",
                (
                    "Confirm a valid synthetic finance import created after the demo "
                    "workspace was seeded."
                ),
            )

    prior_review = session.scalar(
        select(PositionReviewBrief).where(
            PositionReviewBrief.tenant_id == tenant_id,
            PositionReviewBrief.company_id == access.company.id,
            PositionReviewBrief.idempotency_key_hash == key_hash,
        )
    )
    if prior_review is not None:
        if (
            prior_review.requested_by_user_id != user.id
            or prior_review.request_hash != request_hash
        ):
            raise ApiError(
                409,
                "idempotency_key_conflict",
                (
                    "This Idempotency-Key is already associated with a different "
                    "position review request."
                ),
            )
        return _position_review_response(session, prior_review)

    baseline = session.scalar(
        select(MetricRun).where(
            MetricRun.id == seed.baseline_metric_run_id,
            MetricRun.tenant_id == tenant_id,
            MetricRun.company_id == access.company.id,
        )
    )
    if baseline is None:
        raise ApiError(
            409, "demo_baseline_unavailable", "The saved synthetic baseline is unavailable."
        )
    current = create_metric_run(
        session,
        user,
        tenant_id,
        body.start_week_date,
        4,
        request_id,
    )
    operational_imports = _validate_operational_imports(
        session,
        tenant_id,
        access.company.id,
        seed,
        body.operational_imports,
        current,
    )
    source_import_ids = (
        _collect_run_import_ids(baseline)
        | _collect_run_import_ids(current)
        | set(selected_ids.values())
    )
    source_catalog = _source_import_catalog(
        session, tenant_id, access.company.id, source_import_ids
    )
    if source_import_ids - {UUID(str(item["import_id"])) for item in source_catalog}:
        raise ApiError(
            409,
            "position_review_source_unavailable",
            (
                "A source import referenced by an immutable M5 snapshot is unavailable "
                "in this Company."
            ),
        )
    financial_snapshot = _financial_input_snapshot(finance) if finance is not None else None
    attestation_snapshot: dict[str, object] = {
        **body.attestations.model_dump(mode="json"),
        "actor_user_id": str(user.id),
        "actor_display_name": user.display_name,
        "attested_at": datetime.now(UTC).isoformat(timespec="microseconds"),
    }
    demo_seed_snapshot = _seed_payload(seed)
    input_snapshot, result_snapshot = build_position_review_payload(
        _metric_run_payload(baseline),
        _metric_run_payload(current),
        demo_seed_snapshot,
        operational_imports,
        financial_snapshot,
        attestation_snapshot,
        source_catalog,
    )
    input_hash, result_hash = position_review_hashes(input_snapshot, result_snapshot)
    review = PositionReviewBrief(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        demo_seed_id=seed.id,
        baseline_metric_run_id=baseline.id,
        current_metric_run_id=current.id,
        financial_import_id=finance.id if finance is not None else None,
        start_week_date=current.start_week_date,
        end_week_date=current.end_week_date,
        calculation_version=POSITION_REVIEW_CALCULATION_VERSION,
        metric_catalog_version=current.metric_catalog_version,
        metric_calculation_version=current.calculation_version,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
    )
    session.add(review)
    session.flush()
    for selected in operational_imports:
        record = selected_by_id[UUID(str(selected["import_id"]))]
        session.add(
            PositionReviewOperationalImport(
                tenant_id=tenant_id,
                company_id=access.company.id,
                review_id=review.id,
                dataset_type=str(selected["dataset_type"]),
                import_id=record.id,
                file_hash=record.file_hash,
                validation_hash=cast(str, record.validation_hash),
                mapping_version=record.mapping_version,
            )
        )
    operational_result = cast(dict[str, object], result_snapshot["operational"])
    comparison_result = cast(dict[str, object], operational_result["comparison"])
    record_event(
        session,
        "position_review.brief_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="position_review_brief",
        target_id=review.id,
        request_id=request_id,
        details={
            "calculation_version": review.calculation_version,
            "baseline_metric_run_id": baseline.id,
            "current_metric_run_id": current.id,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "financial_import_id": finance.id if finance is not None else None,
            "operational_import_ids": [str(item) for item in selected_ids.values()],
            "operational_comparison_state": comparison_result.get("state"),
        },
    )
    session.flush()
    return _position_review_response(session, review)


def get_position_review(
    session: Session, user: UserAccount, tenant_id: UUID, review_id: UUID
) -> PositionReviewResponse:
    access = require_company_context_access(session, user, tenant_id, "decision:read")
    require_tenant_access(session, user, tenant_id, "finance:read")
    review = session.scalar(
        select(PositionReviewBrief).where(
            PositionReviewBrief.id == review_id,
            PositionReviewBrief.tenant_id == tenant_id,
            PositionReviewBrief.company_id == access.company.id,
        )
    )
    if review is None:
        raise ApiError(404, "position_review_not_found", "The position review was not found.")
    return _position_review_response(session, review)


def list_position_reviews(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
) -> PositionReviewListResponse:
    access = require_company_context_access(session, user, tenant_id, "decision:read")
    require_tenant_access(session, user, tenant_id, "finance:read")
    filters = (
        PositionReviewBrief.tenant_id == tenant_id,
        PositionReviewBrief.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(PositionReviewBrief).where(*filters)) or 0
    )
    rows = session.scalars(
        select(PositionReviewBrief)
        .where(*filters)
        .order_by(PositionReviewBrief.created_at.desc(), PositionReviewBrief.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return PositionReviewListResponse(
        items=[_brief_summary(session, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _disposition_response(
    session: Session, disposition: PositionReviewDisposition
) -> PositionReviewDispositionResponse:
    actor = session.scalar(select(UserAccount).where(UserAccount.id == disposition.actor_user_id))
    return PositionReviewDispositionResponse(
        id=disposition.id,
        tenant_id=disposition.tenant_id,
        company_id=disposition.company_id,
        review_id=disposition.review_id,
        actor_user_id=disposition.actor_user_id,
        actor_display_name=actor.display_name
        if actor is not None
        else str(disposition.actor_user_id),
        review_hash=disposition.review_hash,
        disposition=cast(ReviewDisposition, disposition.disposition),
        rationale=disposition.rationale,
        created_at=disposition.created_at,
    )


def record_position_disposition(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    review_id: UUID,
    body: PositionReviewDispositionRequest,
    request_id: UUID,
) -> PositionReviewDispositionResponse:
    access = require_company_context_access(
        session, user, tenant_id, "decision:write", request_id=request_id
    )
    require_tenant_access(session, user, tenant_id, "finance:read", request_id=request_id)
    review = session.scalar(
        select(PositionReviewBrief)
        .where(
            PositionReviewBrief.id == review_id,
            PositionReviewBrief.tenant_id == tenant_id,
            PositionReviewBrief.company_id == access.company.id,
        )
        .with_for_update()
    )
    if review is None:
        raise ApiError(404, "position_review_not_found", "The position review was not found.")
    _verify_brief_integrity(session, review)
    if body.review_hash != review.result_hash:
        raise ApiError(
            409,
            "position_review_version_mismatch",
            "The disposition must reference the exact current brief result hash.",
        )
    prior = session.scalar(
        select(PositionReviewDisposition).where(
            PositionReviewDisposition.tenant_id == tenant_id,
            PositionReviewDisposition.company_id == access.company.id,
            PositionReviewDisposition.review_id == review.id,
            PositionReviewDisposition.actor_user_id == user.id,
        )
    )
    if prior is not None:
        if (
            prior.review_hash == body.review_hash
            and prior.disposition == body.disposition
            and prior.rationale == body.rationale
        ):
            return _disposition_response(session, prior)
        raise ApiError(
            409,
            "position_review_disposition_immutable",
            "A reviewer may record one immutable disposition per brief version.",
        )
    disposition = PositionReviewDisposition(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        review_id=review.id,
        actor_user_id=user.id,
        review_hash=review.result_hash,
        disposition=body.disposition,
        rationale=body.rationale,
    )
    session.add(disposition)
    record_event(
        session,
        "position_review.disposition_recorded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="position_review_disposition",
        target_id=disposition.id,
        request_id=request_id,
        details={
            "review_id": review.id,
            "review_hash": review.result_hash,
            "disposition": body.disposition,
        },
    )
    session.flush()
    return _disposition_response(session, disposition)
