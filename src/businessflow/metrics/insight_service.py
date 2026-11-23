from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_data.service import company_data_evidence_references
from businessflow.data_imports.models import DataImport, DataImportRecordLink, DataImportRow
from businessflow.errors import ApiError
from businessflow.metrics.calculation import calculate_metrics
from businessflow.metrics.catalog import CALCULATION_VERSION, METRIC_CATALOG_VERSION
from businessflow.metrics.insights import (
    COMPARISON_DEFINITION_VERSION,
    COMPARISON_SPEC_BY_ID,
    INSIGHT_CATALOG_VERSION,
    INSIGHT_EVIDENCE_SCHEMA_VERSION,
    INSIGHT_SNAPSHOT_SCHEMA_VERSION,
    METRIC_INSIGHT_ENGINE_VERSION,
    build_comparison_result,
    metric_insight_catalog_payload,
)
from businessflow.metrics.models import MetricInsightRun, MetricRun
from businessflow.metrics.snapshot import content_hash, deserialize_input_snapshot
from businessflow.models import UserAccount
from businessflow.services.company_context_access import (
    CompanyContextAccess,
    require_company_context_access,
)

MAX_METRIC_INSIGHT_LINEAGE_REFERENCES = 50_000
_QUERY_CHUNK_SIZE = 1_000


def _api_integrity_error(code: str = "metric_run_integrity_error") -> ApiError:
    return ApiError(
        409,
        code,
        "The selected metric run or its retained evidence failed an integrity check.",
    )


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _api_integrity_error()
    return cast(Mapping[str, object], value)


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _api_integrity_error()
    return value


def _required_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _api_integrity_error()
    return value


def _iso(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat(timespec="microseconds")


def _validate_metric_run(run: MetricRun, tenant_id: UUID, company_id: UUID) -> None:
    if (
        run.metric_catalog_version != METRIC_CATALOG_VERSION
        or run.calculation_version != CALCULATION_VERSION
    ):
        raise ApiError(
            409,
            "metric_run_version_unsupported",
            "The selected M5 metric run uses an unsupported catalog or calculation version.",
        )
    if (
        content_hash(run.input_snapshot) != run.input_hash
        or content_hash(run.result_snapshot) != run.result_hash
        or run.result_snapshot.get("metric_catalog_version") != run.metric_catalog_version
        or run.result_snapshot.get("calculation_version") != run.calculation_version
    ):
        raise _api_integrity_error()

    try:
        input_scope = _mapping(run.input_snapshot.get("scope"), name="scope")
        input_period = _mapping(run.input_snapshot.get("period"), name="period")
        result_period = _mapping(run.result_snapshot.get("period"), name="period")
        if (
            input_scope.get("tenant_id") != str(tenant_id)
            or input_scope.get("company_id") != str(company_id)
            or not isinstance(input_scope.get("company_timezone"), str)
            or input_scope.get("company_timezone") != result_period.get("company_timezone")
            or input_period.get("start_week_date") != run.start_week_date.isoformat()
            or input_period.get("week_count") != run.week_count
            or result_period.get("start_week_date") != run.start_week_date.isoformat()
            or result_period.get("end_week_date") != run.end_week_date.isoformat()
            or result_period.get("week_count") != run.week_count
        ):
            raise _api_integrity_error()
        replayed = calculate_metrics(deserialize_input_snapshot(run.input_snapshot))
    except ApiError:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise _api_integrity_error() from None
    if replayed != run.result_snapshot or content_hash(replayed) != run.result_hash:
        raise _api_integrity_error()


def _period_payload(run: MetricRun) -> dict[str, object]:
    return {
        "metric_run_id": str(run.id),
        "start_week_date": run.start_week_date.isoformat(),
        "end_week_date": run.end_week_date.isoformat(),
        "week_count": run.week_count,
    }


def _period_completion_payload(
    baseline_run: MetricRun,
    comparison_run: MetricRun,
    *,
    timezone_name: str,
    checked_at: datetime,
) -> dict[str, object]:
    timezone = ZoneInfo(timezone_name)
    local_date = checked_at.astimezone(timezone).date()
    checks: dict[str, dict[str, object]] = {}
    for role, run in (("baseline", baseline_run), ("comparison", comparison_run)):
        try:
            local_close = datetime.combine(
                run.end_week_date + timedelta(days=1), time.min, tzinfo=timezone
            )
        except OverflowError:
            local_close = None
        created_at = run.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        period_ended = run.end_week_date < local_date
        run_created_after_close = local_close is not None and created_at.astimezone(
            UTC
        ) >= local_close.astimezone(UTC)
        checks[role] = {
            "period_end_date": run.end_week_date.isoformat(),
            "period_close_at_local": (
                local_close.isoformat(timespec="seconds") if local_close is not None else None
            ),
            "metric_run_created_at": _iso(created_at),
            "period_ended_before_local_date": period_ended,
            "metric_run_created_after_close": run_created_after_close,
            "status": "completed" if period_ended and run_created_after_close else "incomplete",
        }
    return {
        "status": (
            "completed"
            if all(item["status"] == "completed" for item in checks.values())
            else "incomplete"
        ),
        "checked_at": _iso(checked_at),
        "timezone": timezone_name,
        "as_of_local_date": local_date.isoformat(),
        "completion_rule": (
            "period_end_date_before_company_local_date_and_m5_run_created_at_at_or_after_"
            "local_midnight_after_period_end"
        ),
        "periods": checks,
    }


def _metric_run_timezone(run: MetricRun) -> str:
    scope = _mapping(run.input_snapshot.get("scope"), name="scope")
    return _required_string(scope.get("company_timezone"), name="company_timezone")


def _metric_input_facts(
    run: MetricRun,
    metric_id: str,
    *,
    role: str,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    spec = COMPARISON_SPEC_BY_ID[metric_id]
    payload = run.input_snapshot
    employees = [
        _mapping(item, name="employee") for item in _required_list(payload.get("employees"))
    ]
    active_employee_ids = {
        value
        for employee in employees
        if employee.get("employment_status") == "active"
        if isinstance((value := employee.get("id")), str)
    }
    start_date = run.start_week_date
    period_end = run.end_week_date
    grouped: dict[str, list[tuple[str, Mapping[str, object]]]] = {
        "employees": [
            ("employee", employee)
            for employee in employees
            if employee.get("employment_status") == "active"
        ]
    }

    if "weekly_capacity" in spec.required_input_groups:
        capacities = [
            _mapping(item, name="weekly capacity")
            for item in _required_list(payload.get("weekly_capacity"))
        ]
        candidates: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for capacity in capacities:
            employee_id = capacity.get("employee_id")
            week_start = capacity.get("week_start_date")
            version = capacity.get("version")
            if (
                not isinstance(employee_id, str)
                or employee_id not in active_employee_ids
                or not isinstance(week_start, str)
                or not isinstance(version, int)
                or isinstance(version, bool)
            ):
                continue
            try:
                week = date.fromisoformat(week_start)
            except ValueError:
                raise _api_integrity_error() from None
            if start_date <= week <= period_end:
                candidates.setdefault((week_start, employee_id), []).append(capacity)
        latest: list[Mapping[str, object]] = []
        for records in candidates.values():
            highest_version = max(cast(int, record["version"]) for record in records)
            latest.extend(record for record in records if record.get("version") == highest_version)
        grouped["weekly_capacity"] = [
            ("weekly_capacity", item)
            for item in sorted(
                latest,
                key=lambda record: (
                    str(record.get("week_start_date")),
                    str(record.get("employee_id")),
                    str(record.get("id")),
                ),
            )
        ]

    if "resource_assignments" in spec.required_input_groups:
        assignments = [
            _mapping(item, name="resource assignment")
            for item in _required_list(payload.get("resource_assignments"))
        ]
        relevant_assignments: list[Mapping[str, object]] = []
        for assignment in assignments:
            if assignment.get("status") != "committed":
                continue
            try:
                assignment_start = date.fromisoformat(
                    _required_string(assignment.get("start_date"), name="start_date")
                )
                assignment_end = date.fromisoformat(
                    _required_string(assignment.get("end_date"), name="end_date")
                )
            except ValueError:
                raise _api_integrity_error() from None
            if assignment_start <= period_end and assignment_end >= start_date:
                relevant_assignments.append(assignment)
        relevant_employee_ids = {
            value
            for assignment in relevant_assignments
            if isinstance((value := assignment.get("employee_id")), str)
        }
        grouped["employees"] = [
            ("employee", employee)
            for employee in employees
            if employee.get("employment_status") == "active"
            or employee.get("id") in relevant_employee_ids
        ]
        grouped["resource_assignments"] = [
            ("resource_assignment", item)
            for item in sorted(
                relevant_assignments,
                key=lambda record: str(record.get("id")),
            )
        ]

    result: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    group_dataset = {
        "employees": "employees",
        "weekly_capacity": "weekly_capacity",
        "resource_assignments": "resource_assignments",
    }
    for group in spec.required_input_groups:
        facts = grouped.get(group, [])
        if not facts:
            missing.append(
                {
                    "target": f"{role}:{group}",
                    "reason": "required_input_group_missing",
                }
            )
            continue
        expected_dataset = group_dataset[group]
        for fact_type, fact in facts:
            fact_id = _required_string(fact.get("id"), name="fact id")
            raw_lineage = fact.get("lineage")
            if not isinstance(raw_lineage, list) or not raw_lineage:
                missing.append(
                    {
                        "target": f"{role}:{group}:{fact_id}",
                        "reason": "source_lineage_missing_for_input_fact",
                    }
                )
                continue
            for raw_reference in raw_lineage:
                reference = _mapping(raw_reference, name="source lineage")
                import_id = _required_string(reference.get("import_id"), name="import_id")
                source_row_id = _required_string(
                    reference.get("source_row_id"), name="source_row_id"
                )
                dataset_type = reference.get("dataset_type")
                if dataset_type != expected_dataset:
                    missing.append(
                        {
                            "target": f"{role}:{import_id}:{source_row_id}",
                            "reason": "source_dataset_type_incompatible",
                        }
                    )
                result.append(
                    {
                        "run_role": role,
                        "input_group": group,
                        "fact_type": fact_type,
                        "fact_id": fact_id,
                        "import_id": import_id,
                        "import_status": reference.get("import_status"),
                        "dataset_type": dataset_type,
                        "mapping_version": reference.get("mapping_version"),
                        "file_hash": reference.get("file_hash"),
                        "source_row_id": source_row_id,
                        "worksheet_name": reference.get("worksheet_name"),
                        "row_number": reference.get("row_number"),
                        "row_hash": reference.get("row_hash"),
                        "canonical_fingerprint_at_import": reference.get(
                            "canonical_fingerprint_at_import"
                        ),
                    }
                )
    return result, missing


def _id_chunks(ids: set[UUID]) -> list[list[UUID]]:
    ordered = sorted(ids, key=str)
    return [
        ordered[start : start + _QUERY_CHUNK_SIZE]
        for start in range(0, len(ordered), _QUERY_CHUNK_SIZE)
    ]


def _lineage_evidence(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    evaluated_at: datetime,
    baseline_run: MetricRun,
    comparison_run: MetricRun,
    metric_id: str,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    baseline_lineage, baseline_missing = _metric_input_facts(
        baseline_run, metric_id, role="baseline"
    )
    comparison_lineage, comparison_missing = _metric_input_facts(
        comparison_run, metric_id, role="comparison"
    )
    lineage = baseline_lineage + comparison_lineage
    missing = baseline_missing + comparison_missing
    if len(lineage) > MAX_METRIC_INSIGHT_LINEAGE_REFERENCES:
        raise ApiError(
            422,
            "metric_insight_input_limit_exceeded",
            "The selected metric inputs exceed the supported evidence-lineage limit.",
        )

    deduped_lineage = {content_hash(item): item for item in lineage}
    lineage = [deduped_lineage[key] for key in sorted(deduped_lineage)]
    source_import_ids: set[UUID] = set()
    for item in lineage:
        raw_import_id = item.get("import_id")
        try:
            parsed_import_id = UUID(cast(str, raw_import_id))
        except (ValueError, TypeError):
            missing.append(
                {
                    "target": str(raw_import_id),
                    "reason": "source_import_id_invalid",
                }
            )
            continue
        source_import_ids.add(parsed_import_id)

    imports: list[DataImport] = []
    for import_id_chunk in _id_chunks(source_import_ids):
        imports.extend(
            session.scalars(
                select(DataImport).where(
                    DataImport.tenant_id == tenant_id,
                    DataImport.company_id == company_id,
                    DataImport.id.in_(import_id_chunk),
                )
            )
        )
    import_by_id = {record.id: record for record in imports}
    row_ids: set[UUID] = set()
    for item in lineage:
        try:
            row_ids.add(UUID(cast(str, item.get("source_row_id"))))
        except (ValueError, TypeError):
            continue
    rows: list[DataImportRow] = []
    for row_id_chunk in _id_chunks(row_ids):
        rows.extend(
            session.scalars(
                select(DataImportRow).where(
                    DataImportRow.tenant_id == tenant_id,
                    DataImportRow.company_id == company_id,
                    DataImportRow.id.in_(row_id_chunk),
                )
            )
        )
    row_by_id = {row.id: row for row in rows}
    links: list[DataImportRecordLink] = []
    for row_id_chunk in _id_chunks(row_ids):
        links.extend(
            session.scalars(
                select(DataImportRecordLink).where(
                    DataImportRecordLink.tenant_id == tenant_id,
                    DataImportRecordLink.company_id == company_id,
                    DataImportRecordLink.source_row_id.in_(row_id_chunk),
                )
            )
        )
    link_by_row = {link.source_row_id: link for link in links}

    refs_by_import: dict[UUID, list[dict[str, object]]] = {}
    lineage_with_status: list[dict[str, object]] = []
    for item in lineage:
        import_id_raw = cast(str, item["import_id"])
        source_row_id_raw = cast(str, item["source_row_id"])
        target = f"{item['run_role']}:{import_id_raw}:{source_row_id_raw}"
        try:
            import_id = UUID(import_id_raw)
            source_row_id = UUID(source_row_id_raw)
        except (ValueError, TypeError):
            reason = "source_lineage_uuid_invalid"
            missing.append({"target": target, "reason": reason})
            lineage_with_status.append(
                {
                    **item,
                    "verification_status": "conflicting",
                    "verification_reasons": [reason],
                }
            )
            continue
        import_record = import_by_id.get(import_id)
        row = row_by_id.get(source_row_id)
        link = link_by_row.get(source_row_id)
        reasons: list[str] = []
        if import_record is None or row is None or link is None:
            reasons.append("m4_lineage_record_missing")
        else:
            expected_values = (
                (item.get("import_status"), import_record.status),
                (item.get("dataset_type"), import_record.dataset_type),
                (item.get("mapping_version"), import_record.mapping_version),
                (item.get("file_hash"), import_record.file_hash),
                (item.get("row_hash"), row.row_hash),
                (item.get("worksheet_name"), row.worksheet_name),
                (item.get("row_number"), row.row_number),
                (item.get("canonical_fingerprint_at_import"), link.canonical_fingerprint),
            )
            if any(expected != actual for expected, actual in expected_values):
                reasons.append("m4_lineage_binding_mismatch")
            if row.import_id != import_record.id or link.import_id != import_record.id:
                reasons.append("m4_lineage_import_mismatch")
        if reasons:
            missing.extend({"target": target, "reason": reason} for reason in reasons)
        else:
            refs_by_import.setdefault(import_id, []).append(item)
        lineage_with_status.append(
            {
                **item,
                "verification_status": "verified" if not reasons else "conflicting",
                "verification_reasons": reasons,
            }
        )

    verified_import_records = {
        import_id: import_by_id[import_id]
        for import_id in refs_by_import
        if import_id in import_by_id
    }
    m12_evidence = company_data_evidence_references(
        session,
        tenant_id,
        company_id,
        verified_import_records,
        evaluated_at=evaluated_at,
    )

    source_import_evidence: list[dict[str, object]] = []
    expected_contracts = {
        dataset_type: (contract_key, version)
        for dataset_type, contract_key, version in COMPARISON_SPEC_BY_ID[
            metric_id
        ].required_contracts
    }
    for import_id in sorted(source_import_ids, key=str):
        import_record = import_by_id.get(import_id)
        import_lineage = refs_by_import.get(import_id, [])
        datasets = sorted(
            {str(item.get("dataset_type")) for item in import_lineage if item.get("dataset_type")}
        )
        roles = sorted({str(item.get("run_role")) for item in import_lineage})
        reasons = [
            item["reason"]
            for item in missing
            if item["target"].endswith(f":{import_id}")
            or item["target"].startswith(f"{import_id}:")
            or f":{import_id}:" in item["target"]
        ]
        m12 = m12_evidence.get(import_id)
        if import_record is None:
            reasons.append("m4_import_missing")
            status = "missing"
        elif not import_lineage:
            status = "conflicting" if reasons else "missing"
        elif m12 is None:
            reasons.append("company_data_ingestion_missing")
            status = "missing"
        else:
            for dataset_type in datasets:
                expected_contract = expected_contracts.get(dataset_type)
                if (
                    expected_contract is None
                    or (m12.get("contract_key"), m12.get("contract_version")) != expected_contract
                ):
                    reasons.append("source_contract_incompatible")
            if m12.get("ingestion_status") != "succeeded":
                reasons.append("source_ingestion_not_succeeded")
            if m12.get("quality_status") != "passed":
                reasons.append("source_quality_not_passed")
            freshness_status = m12.get("freshness_status")
            if freshness_status != "fresh":
                reasons.append(f"source_freshness_{freshness_status}")
            conflict_reasons = {
                "m4_lineage_binding_mismatch",
                "m4_lineage_import_mismatch",
                "m4_lineage_record_missing",
                "source_dataset_type_incompatible",
                "source_contract_incompatible",
            }
            if any(reason in conflict_reasons for reason in reasons):
                status = "conflicting"
            elif m12.get("quality_status") == "warning":
                status = "quality_warning"
            elif m12.get("quality_status") == "failed":
                status = "quality_failed"
            elif m12.get("ingestion_status") != "succeeded":
                status = "ingestion_not_succeeded"
            elif freshness_status != "fresh":
                status = cast(str, freshness_status)
            elif reasons:
                status = "conflicting"
            else:
                status = "verified"
        if status != "verified":
            missing.extend(
                {
                    "target": f"{','.join(roles)}:{import_id}",
                    "reason": reason,
                }
                for reason in dict.fromkeys(reasons)
            )
        evidence_item: dict[str, object] = {
            "import_id": str(import_id),
            "run_roles": roles,
            "dataset_types": datasets,
            "lineage_reference_count": len(import_lineage),
            "m4_status": import_record.status if import_record is not None else None,
            "m4_file_hash": import_record.file_hash if import_record is not None else None,
            "m4_validation_hash": (
                import_record.validation_hash if import_record is not None else None
            ),
            "m4_mapping_version": (
                import_record.mapping_version if import_record is not None else None
            ),
            "verification_status": status,
            "verification_reasons": list(dict.fromkeys(reasons)),
        }
        if m12 is not None:
            evidence_item["company_data"] = {
                key: value for key, value in m12.items() if key != "current_freshness_status"
            }
        source_import_evidence.append(evidence_item)

    missing = _dedupe_reasons(missing)
    spec = COMPARISON_SPEC_BY_ID[metric_id]
    evidence = {
        "schema_version": INSIGHT_EVIDENCE_SCHEMA_VERSION,
        "evidence_evaluated_at": _iso(evaluated_at),
        "status": "verified" if not missing else "incomplete_or_untrusted",
        "required_input_groups": list(spec.required_input_groups),
        "required_contracts": [
            {
                "dataset_type": dataset_type,
                "contract_key": contract_key,
                "version": version,
            }
            for dataset_type, contract_key, version in spec.required_contracts
        ],
        "metric_runs": [
            {
                "role": "baseline",
                "id": str(baseline_run.id),
                "input_hash": baseline_run.input_hash,
                "result_hash": baseline_run.result_hash,
                "metric_catalog_version": baseline_run.metric_catalog_version,
                "calculation_version": baseline_run.calculation_version,
            },
            {
                "role": "comparison",
                "id": str(comparison_run.id),
                "input_hash": comparison_run.input_hash,
                "result_hash": comparison_run.result_hash,
                "metric_catalog_version": comparison_run.metric_catalog_version,
                "calculation_version": comparison_run.calculation_version,
            },
        ],
        "source_imports": source_import_evidence,
        "source_lineage": lineage_with_status,
        "source_lineage_hash": content_hash(lineage_with_status),
        "missing_or_conflicting_input_evidence": missing,
        "lineage_fingerprint_scope": (
            "M5 source-row references are matched to retained M4 row hashes and link fingerprints; "
            "canonical_fingerprint_at_import is not a check of current mutable entity values."
        ),
        "freshness_scope": (
            "M12 freshness is evaluated against the user-reported source_as_of_at and the "
            "company-configured maximum age at evidence_evaluated_at; it is not authenticated."
        ),
    }
    abstentions = [{"target": item["target"], "reason": item["reason"]} for item in missing]
    return evidence, abstentions


def _dedupe_reasons(reasons: list[dict[str, str]]) -> list[dict[str, str]]:
    unique = {(item["target"], item["reason"]): item for item in reasons}
    return [unique[key] for key in sorted(unique)]


def read_metric_insight_catalog(
    session: Session, user: UserAccount, tenant_id: UUID
) -> dict[str, object]:
    require_company_context_access(session, user, tenant_id, "company_context:read")
    return metric_insight_catalog_payload()


def _find_metric_runs(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    baseline_metric_run_id: UUID,
    comparison_metric_run_id: UUID,
) -> tuple[CompanyContextAccess, MetricRun, MetricRun]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    runs = session.scalars(
        select(MetricRun).where(
            MetricRun.tenant_id == tenant_id,
            MetricRun.company_id == access.company.id,
            MetricRun.id.in_([baseline_metric_run_id, comparison_metric_run_id]),
        )
    )
    run_by_id = {run.id: run for run in runs}
    baseline_run = run_by_id.get(baseline_metric_run_id)
    comparison_run = run_by_id.get(comparison_metric_run_id)
    if baseline_run is None or comparison_run is None:
        raise ApiError(404, "metric_run_not_found", "A selected M5 metric run was not found.")
    return access, baseline_run, comparison_run


def create_metric_insight_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    baseline_metric_run_id: UUID,
    comparison_metric_run_id: UUID,
    metric_id: str,
    request_id: UUID,
) -> MetricInsightRun:
    if metric_id not in COMPARISON_SPEC_BY_ID:
        raise ApiError(
            422,
            "metric_comparison_unavailable",
            "The selected M5 metric has no supported M13 comparison definition.",
        )
    if baseline_metric_run_id == comparison_metric_run_id:
        raise ApiError(422, "invalid_metric_comparison", "Select two distinct M5 metric runs.")

    access, baseline_run, comparison_run = _find_metric_runs(
        session,
        user,
        tenant_id,
        baseline_metric_run_id,
        comparison_metric_run_id,
    )
    for run in (baseline_run, comparison_run):
        _validate_metric_run(run, tenant_id, access.company.id)

    baseline_timezone = _metric_run_timezone(baseline_run)
    comparison_timezone = _metric_run_timezone(comparison_run)
    if baseline_timezone != comparison_timezone or baseline_timezone != access.company.timezone:
        raise ApiError(
            422,
            "metric_comparison_scope_incompatible",
            (
                "Selected M5 runs must share the Company's current tenant, company, and "
                "timezone scope."
            ),
        )
    if (
        baseline_run.week_count != comparison_run.week_count
        or baseline_run.end_week_date + timedelta(days=1) != comparison_run.start_week_date
    ):
        raise ApiError(
            422,
            "invalid_metric_comparison_period",
            "M13 comparisons require equal-length, immediately adjacent M5 periods.",
        )

    evidence_evaluated_at = datetime.now(UTC)
    try:
        period_completion = _period_completion_payload(
            baseline_run,
            comparison_run,
            timezone_name=baseline_timezone,
            checked_at=evidence_evaluated_at,
        )
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(
            409,
            "metric_comparison_scope_incompatible",
            (
                "The Company timezone is invalid; the selected M5 period boundaries cannot be "
                "verified."
            ),
        ) from None
    if period_completion["status"] != "completed":
        raise ApiError(
            422,
            "metric_comparison_period_incomplete",
            (
                "Both selected M5 periods must be fully elapsed, and each M5 run must have been "
                "created at or after local midnight following its period end."
            ),
        )

    data_evidence, evidence_abstentions = _lineage_evidence(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        evaluated_at=evidence_evaluated_at,
        baseline_run=baseline_run,
        comparison_run=comparison_run,
        metric_id=metric_id,
    )
    baseline_period = _period_payload(baseline_run)
    comparison_period = _period_payload(comparison_run)
    result_payload = build_comparison_result(
        metric_id=metric_id,
        baseline_result=baseline_run.result_snapshot,
        comparison_result=comparison_run.result_snapshot,
        baseline_period=baseline_period,
        comparison_period=comparison_period,
        period_completion=period_completion,
        data_evidence=data_evidence,
        evidence_abstentions=evidence_abstentions,
    )
    input_payload: dict[str, object] = {
        "snapshot_schema_version": INSIGHT_SNAPSHOT_SCHEMA_VERSION,
        "scope": {
            "tenant_id": str(tenant_id),
            "company_id": str(access.company.id),
            "company_timezone": baseline_timezone,
        },
        "metric_id": metric_id,
        "insight_catalog_version": INSIGHT_CATALOG_VERSION,
        "engine_version": METRIC_INSIGHT_ENGINE_VERSION,
        "comparison_definition_version": COMPARISON_DEFINITION_VERSION,
        "source_metric_catalog_version": METRIC_CATALOG_VERSION,
        "source_calculation_version": CALCULATION_VERSION,
        "periods": {
            "baseline": baseline_period,
            "comparison": comparison_period,
        },
        "period_completion": period_completion,
        "evidence_evaluated_at": _iso(evidence_evaluated_at),
        "data_evidence": data_evidence,
        "evidence_abstentions": evidence_abstentions,
    }
    input_hash = content_hash(input_payload)
    result_hash = content_hash(result_payload)
    insight_run = MetricInsightRun(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        baseline_metric_run_id=baseline_run.id,
        comparison_metric_run_id=comparison_run.id,
        metric_id=metric_id,
        insight_catalog_version=INSIGHT_CATALOG_VERSION,
        engine_version=METRIC_INSIGHT_ENGINE_VERSION,
        comparison_definition_version=COMPARISON_DEFINITION_VERSION,
        availability_status=cast(str, result_payload["availability_status"]),
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_payload,
        result_snapshot=result_payload,
    )
    session.add(insight_run)
    session.flush()
    record_event(
        session,
        "metric_insight_run.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="metric_insight_run",
        target_id=insight_run.id,
        request_id=request_id,
        outcome="success",
        details={
            "baseline_metric_run_id": baseline_run.id,
            "comparison_metric_run_id": comparison_run.id,
            "metric_id": metric_id,
            "availability_status": insight_run.availability_status,
            "insight_catalog_version": INSIGHT_CATALOG_VERSION,
            "engine_version": METRIC_INSIGHT_ENGINE_VERSION,
            "comparison_definition_version": COMPARISON_DEFINITION_VERSION,
            "input_hash": input_hash,
            "result_hash": result_hash,
        },
    )
    return insight_run


def get_metric_insight_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    insight_run_id: UUID,
) -> MetricInsightRun:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    insight_run = session.scalar(
        select(MetricInsightRun).where(
            MetricInsightRun.id == insight_run_id,
            MetricInsightRun.tenant_id == tenant_id,
            MetricInsightRun.company_id == access.company.id,
        )
    )
    if insight_run is None:
        raise ApiError(404, "metric_insight_run_not_found", "The metric insight run was not found.")
    return insight_run


def replay_metric_insight_run(
    session: Session,
    insight_run: MetricInsightRun,
) -> dict[str, object]:
    if (
        insight_run.insight_catalog_version != INSIGHT_CATALOG_VERSION
        or insight_run.engine_version != METRIC_INSIGHT_ENGINE_VERSION
        or insight_run.comparison_definition_version != COMPARISON_DEFINITION_VERSION
    ):
        raise ApiError(
            409,
            "metric_insight_version_unsupported",
            "The saved M13 run uses an unsupported insight-engine version.",
        )
    if (
        content_hash(insight_run.input_snapshot) != insight_run.input_hash
        or content_hash(insight_run.result_snapshot) != insight_run.result_hash
    ):
        raise _api_integrity_error("metric_insight_integrity_error")

    try:
        scope = _mapping(insight_run.input_snapshot.get("scope"), name="scope")
        periods = _mapping(insight_run.input_snapshot.get("periods"), name="periods")
        baseline_period = _mapping(periods.get("baseline"), name="baseline period")
        comparison_period = _mapping(periods.get("comparison"), name="comparison period")
        period_completion = _mapping(
            insight_run.input_snapshot.get("period_completion"), name="period completion"
        )
        data_evidence = _mapping(
            insight_run.input_snapshot.get("data_evidence"), name="data evidence"
        )
        raw_abstentions = _required_list(insight_run.input_snapshot.get("evidence_abstentions"))
        if (
            insight_run.input_snapshot.get("snapshot_schema_version")
            != INSIGHT_SNAPSHOT_SCHEMA_VERSION
            or scope.get("tenant_id") != str(insight_run.tenant_id)
            or scope.get("company_id") != str(insight_run.company_id)
            or not isinstance(scope.get("company_timezone"), str)
            or insight_run.input_snapshot.get("metric_id") != insight_run.metric_id
            or insight_run.input_snapshot.get("insight_catalog_version")
            != insight_run.insight_catalog_version
            or insight_run.input_snapshot.get("engine_version") != insight_run.engine_version
            or insight_run.input_snapshot.get("comparison_definition_version")
            != insight_run.comparison_definition_version
            or insight_run.input_snapshot.get("source_metric_catalog_version")
            != METRIC_CATALOG_VERSION
            or insight_run.input_snapshot.get("source_calculation_version") != CALCULATION_VERSION
        ):
            raise _api_integrity_error("metric_insight_integrity_error")
        baseline_run = session.scalar(
            select(MetricRun).where(
                MetricRun.id == insight_run.baseline_metric_run_id,
                MetricRun.tenant_id == insight_run.tenant_id,
                MetricRun.company_id == insight_run.company_id,
            )
        )
        comparison_run = session.scalar(
            select(MetricRun).where(
                MetricRun.id == insight_run.comparison_metric_run_id,
                MetricRun.tenant_id == insight_run.tenant_id,
                MetricRun.company_id == insight_run.company_id,
            )
        )
        if baseline_run is None or comparison_run is None:
            raise _api_integrity_error("metric_insight_integrity_error")
        for metric_run in (baseline_run, comparison_run):
            _validate_metric_run(metric_run, insight_run.tenant_id, insight_run.company_id)
        baseline_timezone = _metric_run_timezone(baseline_run)
        comparison_timezone = _metric_run_timezone(comparison_run)
        if (
            baseline_timezone != comparison_timezone
            or scope.get("company_timezone") != baseline_timezone
            or period_completion.get("timezone") != baseline_timezone
            or baseline_run.week_count != comparison_run.week_count
            or baseline_run.end_week_date + timedelta(days=1) != comparison_run.start_week_date
            or dict(baseline_period) != _period_payload(baseline_run)
            or dict(comparison_period) != _period_payload(comparison_run)
            or insight_run.input_snapshot.get("evidence_evaluated_at")
            != data_evidence.get("evidence_evaluated_at")
            or insight_run.input_snapshot.get("evidence_evaluated_at")
            != period_completion.get("checked_at")
        ):
            raise _api_integrity_error("metric_insight_integrity_error")
        completion_checked_at = datetime.fromisoformat(
            _required_string(period_completion.get("checked_at"), name="checked_at")
        )
        if completion_checked_at.tzinfo is None:
            raise _api_integrity_error("metric_insight_integrity_error")
        expected_period_completion = _period_completion_payload(
            baseline_run,
            comparison_run,
            timezone_name=baseline_timezone,
            checked_at=completion_checked_at,
        )
        if (
            expected_period_completion != dict(period_completion)
            or period_completion.get("status") != "completed"
        ):
            raise _api_integrity_error("metric_insight_integrity_error")
        spec = COMPARISON_SPEC_BY_ID.get(insight_run.metric_id)
        if spec is None or (
            data_evidence.get("schema_version") != INSIGHT_EVIDENCE_SCHEMA_VERSION
            or data_evidence.get("required_input_groups") != list(spec.required_input_groups)
            or data_evidence.get("required_contracts")
            != [
                {
                    "dataset_type": dataset_type,
                    "contract_key": contract_key,
                    "version": version,
                }
                for dataset_type, contract_key, version in spec.required_contracts
            ]
        ):
            raise _api_integrity_error("metric_insight_integrity_error")
        expected_metric_runs = data_evidence.get("metric_runs")
        if not isinstance(expected_metric_runs, list) or len(expected_metric_runs) != 2:
            raise _api_integrity_error("metric_insight_integrity_error")
        expected_by_role = {
            item.get("role"): item for item in expected_metric_runs if isinstance(item, Mapping)
        }
        for role, metric_run in (
            ("baseline", baseline_run),
            ("comparison", comparison_run),
        ):
            expected = expected_by_role.get(role)
            if not isinstance(expected, Mapping) or (
                expected.get("id") != str(metric_run.id)
                or expected.get("input_hash") != metric_run.input_hash
                or expected.get("result_hash") != metric_run.result_hash
            ):
                raise _api_integrity_error("metric_insight_integrity_error")
        result = build_comparison_result(
            metric_id=insight_run.metric_id,
            baseline_result=baseline_run.result_snapshot,
            comparison_result=comparison_run.result_snapshot,
            baseline_period=baseline_period,
            comparison_period=comparison_period,
            period_completion=period_completion,
            data_evidence=dict(data_evidence),
            evidence_abstentions=[
                _string_mapping(item, name="evidence abstention") for item in raw_abstentions
            ],
        )
    except ApiError:
        raise
    except (KeyError, TypeError, ValueError):
        raise _api_integrity_error("metric_insight_integrity_error") from None

    replayed_hash = content_hash(result)
    if (
        result != insight_run.result_snapshot
        or replayed_hash != insight_run.result_hash
        or result.get("availability_status") != insight_run.availability_status
    ):
        raise ApiError(
            409,
            "metric_insight_replay_mismatch",
            "The M13 result could not be deterministically replayed from its retained inputs.",
        )
    return {
        "insight_run_id": insight_run.id,
        "input_hash": insight_run.input_hash,
        "saved_result_hash": insight_run.result_hash,
        "replayed_result_hash": replayed_hash,
        "matches_saved_result": True,
        "result_snapshot": result,
    }


def _string_mapping(value: object, *, name: str) -> dict[str, str]:
    mapping = _mapping(value, name=name)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in mapping.items()):
        raise _api_integrity_error("metric_insight_integrity_error")
    return cast(dict[str, str], dict(mapping))
