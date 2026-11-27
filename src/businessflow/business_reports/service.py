from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.business_intelligence.analysis_service import (
    get_metric_analysis,
    list_metric_evidence,
)
from businessflow.business_intelligence.models import BusinessIntelligenceAnalysisRun
from businessflow.business_reports.contracts import (
    ReportArtifactPurgeResponse,
    ReportExportCreateRequest,
    ReportExportDetail,
    ReportExportPage,
    ReportExportRevocationResponse,
    ReportExportRevokeRequest,
    ReportExportSummary,
    ReportSourceReference,
)
from businessflow.business_reports.models import (
    BusinessReportArtifactContent,
    BusinessReportArtifactRevocation,
    BusinessReportExport,
)
from businessflow.business_reports.rendering import MAX_REPORT_ARTIFACT_BYTES, render_report
from businessflow.config import Settings
from businessflow.direct_cash_forecasts.service import get_direct_cash_forecast_run
from businessflow.errors import ApiError
from businessflow.financial_calculations.service import (
    get_financial_calculation_run,
    replay_financial_calculation_for_report,
)
from businessflow.forecasting.service import (
    get_forecast_dataset,
    get_forecast_evaluation,
    get_forecast_run,
)
from businessflow.models import Company, UserAccount
from businessflow.services.company_context_access import require_company_context_access

REPORT_ENGINE_VERSION = "businessflow.reporting.v1"
REPORT_MODE = "nonproduction_prototype"
MAX_EVIDENCE_REFERENCES_PER_ANALYSIS = 1_000


def _not_found() -> ApiError:
    return ApiError(404, "report_export_not_found", "Report export was not found.")


def _production_exports_blocked() -> ApiError:
    return ApiError(
        503,
        "production_report_exports_blocked",
        "Production report exports remain blocked pending explicit governance "
        "and data authorization.",
    )


def _require_report_exports_enabled(settings: Settings) -> None:
    if settings.app_env == "production":
        raise _production_exports_blocked()
    if settings.report_exports_enabled is not True:
        raise ApiError(
            503,
            "business_reports_disabled",
            "Business report exports are disabled by deployment configuration.",
        )


def _now_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("report timestamps must include a timezone")
    return value.astimezone(UTC)


def _database_now(session: Session, fallback: datetime) -> datetime:
    value = session.scalar(select(func.clock_timestamp()))
    return _now_utc(value) if isinstance(value, datetime) else _now_utc(fallback)


def _manifest_hash(manifest: dict[str, object]) -> str:
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _verify_export_record(row: BusinessReportExport) -> None:
    manifest = row.manifest
    if (
        not isinstance(manifest, dict)
        or _manifest_hash(manifest) != row.manifest_hash
        or manifest.get("report_id") != str(row.id)
        or manifest.get("tenant_id") != str(row.tenant_id)
        or manifest.get("company_id") != str(row.company_id)
        or manifest.get("created_by_user_id") != str(row.created_by_user_id)
        or manifest.get("title") != row.title
        or manifest.get("format") != row.format
        or manifest.get("mode") != row.mode
        or manifest.get("source_count") != row.source_count
        or manifest.get("generated_at") != row.created_at.isoformat()
        or manifest.get("expires_at") != row.expires_at.isoformat()
        or row.mode != REPORT_MODE
        or row.format not in {"json", "csv", "xlsx"}
        or row.artifact_size_bytes < 1
        or row.artifact_size_bytes > MAX_REPORT_ARTIFACT_BYTES
        or len(row.artifact_sha256) != 64
        or not set(row.artifact_sha256) <= set("0123456789abcdef")
        or not timedelta(0) < row.expires_at - row.created_at <= timedelta(days=30)
    ):
        raise ApiError(
            409,
            "report_export_integrity_error",
            "Saved report export metadata failed an integrity check.",
        )


def _period(start: object = None, end: object = None, **extra: object) -> dict[str, object]:
    result: dict[str, object] = {}
    if isinstance(start, (date, datetime)):
        result["start"] = start.isoformat()
    elif isinstance(start, str):
        result["start"] = start
    if isinstance(end, (date, datetime)):
        result["end"] = end.isoformat()
    elif isinstance(end, str):
        result["end"] = end
    result.update({key: value for key, value in extra.items() if value is not None})
    return result


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _currency_codes(values: list[object]) -> list[str]:
    return sorted({value for value in values if isinstance(value, str) and len(value) == 3})


def _bi_source_freshness(input_snapshot: dict[str, object]) -> dict[str, object]:
    selection = _as_dict(input_snapshot.get("source_selection"))
    if "freshness_max_age_hours" not in selection:
        return {"status": "not_assessed"}
    freshness_limit = selection.get("freshness_max_age_hours")
    last_promoted_at = selection.get("last_promoted_at")
    reason_keys = {str(item) for item in _as_list(selection.get("reason_keys"))}
    if freshness_limit is None:
        status = "unknown_policy_unset"
    elif "source_data_stale" in reason_keys:
        status = "stale_at_analysis_time"
    elif isinstance(last_promoted_at, str):
        status = "within_policy_at_analysis_time"
    else:
        status = "unknown_missing_promoted_timestamp"
    return {
        "status": status,
        "freshness_max_age_hours": freshness_limit,
        "last_promoted_at": last_promoted_at,
        "analysis_source_status": selection.get("status"),
    }


def _build_bi_analysis_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    reference: ReportSourceReference,
    *,
    settings: Settings,
) -> dict[str, object]:
    analysis = get_metric_analysis(
        session,
        user,
        tenant_id,
        reference.source_id,
        settings=settings,
    )
    row = session.scalar(
        select(BusinessIntelligenceAnalysisRun).where(
            BusinessIntelligenceAnalysisRun.id == reference.source_id,
            BusinessIntelligenceAnalysisRun.tenant_id == tenant_id,
            BusinessIntelligenceAnalysisRun.company_id == analysis.company_id,
        )
    )
    if row is None:
        raise _not_found()

    first_page = list_metric_evidence(
        session,
        user,
        tenant_id,
        reference.source_id,
        limit=min(MAX_EVIDENCE_REFERENCES_PER_ANALYSIS, 100),
        offset=0,
        settings=settings,
    )
    evidence = [item.model_dump(mode="json", exclude_none=True) for item in first_page.items]
    next_offset = len(evidence)
    evidence_limit = min(first_page.total, MAX_EVIDENCE_REFERENCES_PER_ANALYSIS)
    while next_offset < evidence_limit:
        page = list_metric_evidence(
            session,
            user,
            tenant_id,
            reference.source_id,
            limit=min(100, evidence_limit - next_offset),
            offset=next_offset,
            settings=settings,
        )
        evidence.extend(item.model_dump(mode="json", exclude_none=True) for item in page.items)
        if not page.items:
            break
        next_offset += len(page.items)

    result = analysis.result_snapshot
    groups = [item for item in _as_list(result.get("groups")) if isinstance(item, dict)]
    definition = _as_dict(row.definition_snapshot)
    configuration = _as_dict(row.configuration_snapshot)
    source_profiles = _as_list(configuration.get("source_precedence"))
    data_points = [
        {
            "period_start": analysis.period_start.isoformat() if analysis.period_start else None,
            "period_end": analysis.period_end.isoformat() if analysis.period_end else None,
            "dimensions": _as_dict(group.get("dimensions")),
            "value": group.get("value"),
            "currency_code": group.get("currency_code"),
            "unit": definition.get("unit"),
            "status": result.get("status", analysis.status),
            "details": group,
        }
        for group in groups
    ]
    currencies = _currency_codes([group.get("currency_code") for group in groups])
    lineage = {
        "analysis_id": str(analysis.id),
        "metric_key": analysis.metric_key,
        "metric_version": analysis.metric_version,
        "system_metric_version": analysis.system_metric_version,
        "definition_hash": analysis.definition_hash,
        "configuration_version": analysis.configuration_version,
        "configuration_hash": analysis.configuration_hash,
        "calculation_version": analysis.calculation_version,
        "input_hash": analysis.input_hash,
        "result_hash": analysis.result_hash,
    }
    limitations = [
        item for item in _as_list(definition.get("limitations")) if isinstance(item, str)
    ]
    return {
        "kind": "business_intelligence_analysis",
        "id": str(analysis.id),
        "label": analysis.metric_key,
        "domain": analysis.domain,
        "status": analysis.status,
        "period_start": analysis.period_start.isoformat() if analysis.period_start else None,
        "period_end": analysis.period_end.isoformat() if analysis.period_end else None,
        "currency_codes": currencies,
        "unit": definition.get("unit"),
        "quality": {
            "analysis_status": analysis.status,
            "governance_blockers": analysis.governance_blockers,
            "retention_status": analysis.retention_status,
            "source_profile_context": source_profiles,
            "source_freshness": _bi_source_freshness(_as_dict(row.input_snapshot)),
            "result_status": result.get("status"),
            "result_warnings": _as_list(result.get("warnings")),
            "result_blockers": _as_list(result.get("blockers")),
        },
        "assumptions": limitations,
        "lineage": lineage,
        "evidence_count": first_page.total,
        "evidence_references": evidence,
        "evidence_truncated": first_page.total > len(evidence),
        "result": result,
        "data_points": data_points,
    }


def _build_forecast_run_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    reference: ReportSourceReference,
    *,
    settings: Settings,
) -> dict[str, object]:
    response = get_forecast_run(session, user, tenant_id, reference.source_id, settings=settings)
    run = response.run
    dataset_detail = get_forecast_dataset(
        session, user, tenant_id, run.dataset_version_id, settings=settings
    )
    dataset = dataset_detail.dataset
    result = response.result_snapshot
    points = [item for item in _as_list(result.get("forecast_points")) if isinstance(item, dict)]
    target = _as_dict(result.get("target"))
    currencies = _currency_codes([dataset.currency_code, target.get("currency_code")])
    lineage = {
        "forecast_run_id": str(run.id),
        "dataset_version_id": str(dataset.id),
        "dataset_hash": run.dataset_hash,
        "model_version_id": str(run.model_version_id),
        "model_hash": run.model_hash,
        "protocol_version_id": str(run.protocol_version_id),
        "protocol_hash": run.protocol_hash,
        "calibration_evaluation_id": (
            str(run.calibration_evaluation_id) if run.calibration_evaluation_id else None
        ),
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
        "run_hash": run.run_hash,
    }
    data_points = [
        {
            "period_start": point.get("period_start"),
            "period_end": point.get("period_start"),
            "dimensions": _as_dict(point.get("dimension_values")),
            "value": point.get("prediction"),
            "currency_code": target.get("currency_code"),
            "unit": target.get("unit", dataset.unit),
            "status": "forecasted" if point.get("prediction") is not None else "not_evaluable",
            "details": point,
        }
        for point in points
    ]
    reasons = _as_list(result.get("reason_codes"))
    return {
        "kind": "forecast_run",
        "id": str(run.id),
        "label": str(target.get("label") or dataset.target_label),
        "target_key": dataset.target_key,
        "status": run.run_status,
        "period_start": result.get("forecast_start_period"),
        "period_end": result.get("forecast_end_period"),
        "history_period": _period(dataset.start_period, dataset.end_period),
        "currency_codes": currencies,
        "unit": dataset.unit,
        "quality": {
            "forecast_status": run.run_status,
            "data_origin": dataset.data_origin,
            "dataset_eligibility": dataset_detail.eligibility.model_dump(mode="json"),
            "source_freshness_status": "not_assessed_by_m17",
            "reason_codes": reasons,
        },
        "assumptions": [
            "Forecast output is experimental and does not establish source authority or accuracy.",
            "No currency conversion or accounting interpretation is performed "
            "by the report renderer.",
        ],
        "lineage": lineage,
        "evidence_count": 1,
        "evidence_references": [
            {
                "evidence_kind": "forecast_dataset_version",
                "dataset_version_id": str(dataset.id),
                "dataset_hash": run.dataset_hash,
                "data_origin": dataset.data_origin,
            }
        ],
        "evidence_truncated": False,
        "result": result,
        "data_points": data_points,
    }


def _build_forecast_evaluation_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    reference: ReportSourceReference,
    *,
    settings: Settings,
) -> dict[str, object]:
    response = get_forecast_evaluation(
        session, user, tenant_id, reference.source_id, settings=settings
    )
    evaluation = response.evaluation
    dataset_detail = get_forecast_dataset(
        session, user, tenant_id, evaluation.dataset_version_id, settings=settings
    )
    dataset = dataset_detail.dataset
    result = response.result_snapshot
    model_results = [
        item for item in _as_list(result.get("model_results")) if isinstance(item, dict)
    ]
    points = [
        {
            "period_start": None,
            "period_end": None,
            "dimensions": {"model_version_id": item.get("model_version_id")},
            "value": None,
            "currency_code": dataset.currency_code,
            "unit": dataset.unit,
            "status": item.get("status", evaluation.evaluation_status),
            "details": item,
        }
        for item in model_results
    ]
    lineage = {
        "evaluation_id": str(evaluation.id),
        "dataset_version_id": str(dataset.id),
        "dataset_hash": evaluation.dataset_hash,
        "protocol_version_id": str(evaluation.protocol_version_id),
        "protocol_hash": evaluation.protocol_hash,
        "model_version_ids": [str(item) for item in evaluation.model_version_ids],
        "calibration_evaluation_id": (
            str(evaluation.calibration_evaluation_id)
            if evaluation.calibration_evaluation_id
            else None
        ),
        "input_hash": evaluation.input_hash,
        "result_hash": evaluation.result_hash,
        "evaluation_hash": evaluation.evaluation_hash,
    }
    evidence = [
        {
            "evidence_kind": "forecast_evaluation",
            "dataset_version_id": str(dataset.id),
            "dataset_hash": evaluation.dataset_hash,
            "protocol_hash": evaluation.protocol_hash,
            "model_version_ids": [str(item) for item in evaluation.model_version_ids],
        }
    ]
    return {
        "kind": "forecast_evaluation",
        "id": str(evaluation.id),
        "label": f"{evaluation.stage} model evaluation",
        "status": evaluation.evaluation_status,
        "period_start": dataset.start_period.isoformat(),
        "period_end": dataset.end_period.isoformat(),
        "currency_codes": _currency_codes([dataset.currency_code]),
        "unit": dataset.unit,
        "quality": {
            "stage": evaluation.stage,
            "evaluation_status": evaluation.evaluation_status,
            "data_origin": dataset.data_origin,
            "dataset_eligibility": dataset_detail.eligibility.model_dump(mode="json"),
            "source_freshness_status": "not_assessed_by_m17",
        },
        "assumptions": [
            "Backtest metrics describe only this versioned experimental evaluation protocol.",
            "No model ranking, business approval, or production activation is implied.",
        ],
        "lineage": lineage,
        "evidence_count": 1,
        "evidence_references": evidence,
        "evidence_truncated": False,
        "result": {
            "result_snapshot": result,
            "comparison": response.comparison,
        },
        "data_points": points,
    }


def _build_direct_cash_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    reference: ReportSourceReference,
    *,
    settings: Settings,
) -> dict[str, object]:
    if reference.intake_id is None:
        raise ApiError(422, "invalid_report_source", "The direct-cash report source is incomplete.")
    response = get_direct_cash_forecast_run(
        session,
        user,
        tenant_id,
        reference.intake_id,
        reference.source_id,
        settings=settings,
    )
    result = response.result_snapshot.model_dump(mode="json", exclude_none=True)
    input_snapshot = response.input_snapshot.model_dump(mode="json", exclude_none=True)
    weeks = [item for item in _as_list(result.get("weeks")) if isinstance(item, dict)]
    data_points = [
        {
            "period_start": week.get("period_start"),
            "period_end": week.get("period_end"),
            "dimensions": {"week_number": week.get("week_number")},
            "value": week.get("closing_cash_known_item_rollforward"),
            "currency_code": response.currency_code,
            "unit": "source currency amount",
            "status": week.get("closing_cash_status"),
            "details": week,
        }
        for week in weeks
    ]
    intake = _as_dict(input_snapshot.get("intake"))
    group = _as_dict(input_snapshot.get("group"))
    lineage = {
        "run_id": str(response.id),
        "intake_id": str(response.intake_id),
        "source_key": response.source_key,
        "source_file_hash": response.source_file_hash,
        "contract_key": response.contract_key,
        "contract_version": response.contract_version,
        "contract_hash": response.contract_hash,
        "mapping_version": response.mapping_version,
        "mapping_hash": response.mapping_hash,
        "reconciliation_group_hash": response.reconciliation_group_hash,
        "formula_id": response.formula_id,
        "formula_version": response.formula_version,
        "formula_hash": response.formula_hash,
        "engine_version": response.engine_version,
        "catalog_version": response.catalog_version,
        "input_hash": response.input_hash,
        "result_hash": response.result_hash,
        "trace_hash": response.trace_hash,
        "evidence_snapshot_hash": response.evidence_snapshot_hash,
    }
    evidence = [
        {
            "evidence_kind": "finance_source_group",
            "intake_id": str(response.intake_id),
            "source_key": response.source_key,
            "file_hash": response.source_file_hash,
            "group": group,
            "source_evidence_integrity_status": input_snapshot.get(
                "source_evidence_integrity_status"
            ),
        }
    ]
    movements = [
        item for item in _as_list(input_snapshot.get("movements")) if isinstance(item, dict)
    ]
    evidence.extend(
        {
            "evidence_kind": "finance_source_row_assertion",
            "source_row_number": item.get("source_row_number"),
            "source_row_hash": _as_dict(item.get("evidence")).get("source_row_hash"),
            "source_record_identity_hash": _as_dict(item.get("evidence")).get(
                "source_record_identity_hash"
            ),
            "cash_flow_type": item.get("cash_flow_type"),
            "basis": item.get("basis"),
            "cash_date": item.get("cash_date"),
            "amount_fingerprint": item.get("amount_fingerprint"),
        }
        for item in movements
    )
    return {
        "kind": "direct_cash_forecast_run",
        "id": str(response.id),
        "label": "13-week caller-asserted known-item cash roll-forward",
        "status": response.calculation_state,
        "period_start": response.as_of_date.isoformat(),
        "period_end": result.get("horizon_end_date"),
        "currency_codes": _currency_codes([response.currency_code]),
        "unit": "source currency amount",
        "quality": {
            "calculation_state": response.calculation_state,
            "forecast_completeness_status": response.forecast_completeness_status,
            "source_freshness_status": intake.get("freshness_status"),
            "source_quality_status": intake.get("quality_status"),
            "source_reconciliation_status": intake.get("reconciliation_status"),
            "cash_semantics_origin": result.get("cash_semantics_origin"),
            "reason_codes": response.reason_codes,
            "limitations": result.get("limitations", []),
        },
        "assumptions": [
            "Cash flow meaning, dates, basis, and supersession are caller assertions.",
            "This is a known-item roll-forward, not a complete or authoritative cash forecast.",
            "No payment, transfer, or external action is initiated.",
        ],
        "lineage": lineage,
        "evidence_count": len(evidence),
        "evidence_references": evidence,
        "evidence_truncated": False,
        "input_snapshot": input_snapshot,
        "result": result,
        "data_points": data_points,
    }


def _build_financial_calculation_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    reference: ReportSourceReference,
    *,
    settings: Settings,
    request_id: UUID,
) -> dict[str, object]:
    if reference.intake_id is None or reference.replay_request is None:
        raise ApiError(422, "invalid_report_source", "The financial report source is incomplete.")
    run = get_financial_calculation_run(
        session,
        user,
        tenant_id,
        reference.intake_id,
        reference.source_id,
        settings=settings,
    )
    verification, trace = replay_financial_calculation_for_report(
        session,
        user,
        tenant_id,
        reference.intake_id,
        reference.source_id,
        reference.replay_request,
        settings=settings,
        request_id=request_id,
    )
    if not verification.passed:
        raise ApiError(
            409,
            "financial_calculation_replay_mismatch",
            "The financial calculation inputs do not reproduce the exact saved BFS result.",
        )
    output = trace.output.model_dump(mode="json", exclude_none=True) if trace.output else None
    group = _as_dict(run.evidence_snapshot.get("group"))
    intake = _as_dict(run.evidence_snapshot.get("intake"))
    evidence = [
        item
        for item in _as_list(run.evidence_snapshot.get("observations"))
        if isinstance(item, dict)
    ]
    lineage = {
        "run_id": str(run.id),
        "intake_id": str(run.intake_id),
        "source_key": run.source_key,
        "source_file_hash": run.file_hash,
        "contract_key": run.contract_key,
        "contract_version": run.contract_version,
        "contract_hash": run.contract_hash,
        "mapping_version": run.mapping_version,
        "mapping_hash": run.mapping_hash,
        "reconciliation_group_hash": run.reconciliation_group_hash,
        "formula_id": run.formula_id,
        "formula_version": run.formula_version,
        "formula_hash": run.formula_hash,
        "engine_version": run.engine_version,
        "catalog_version": run.catalog_version,
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
        "trace_hash": run.trace_hash,
        "evidence_snapshot_hash": run.evidence_snapshot_hash,
        "replayed_trace_hash": verification.replayed_trace_hash,
    }
    data_points = (
        [
            {
                "period_start": output.get("period_start"),
                "period_end": output.get("period_end"),
                "dimensions": {
                    "reconciliation_group_hash": output.get("reconciliation_group_hash")
                },
                "value": output.get("value"),
                "currency_code": output.get("currency_code"),
                "unit": "source currency amount",
                "status": run.state,
                "details": {
                    "label": output.get("label"),
                    "amount_origin": output.get("amount_origin"),
                    "rounding_applied": output.get("rounding_applied"),
                    "accounting_interpretation": output.get("accounting_interpretation"),
                    "source_row_numbers": output.get("source_row_numbers"),
                },
            }
        ]
        if output is not None
        else []
    )
    return {
        "kind": "financial_calculation_run",
        "id": str(run.id),
        "label": run.formula_id,
        "status": run.state,
        "period_start": group.get("period_start"),
        "period_end": group.get("period_end"),
        "currency_codes": _currency_codes([group.get("currency_code")]),
        "unit": "source currency amount",
        "quality": {
            "calculation_state": run.state,
            "reason_codes": list(run.reason_codes),
            "source_evidence_integrity_status": run.evidence_snapshot.get(
                "source_evidence_integrity_status"
            ),
            "source_quality_status": intake.get("quality_status", "unknown"),
            "source_freshness_status": intake.get("freshness_status", "unknown"),
            "source_freshness_max_age_hours": intake.get("freshness_max_age_hours"),
            "source_as_of_at": intake.get("source_as_of_at"),
            "source_reconciliation_status": intake.get("reconciliation_status", "unknown"),
            "replay_status": "verified",
            "accounting_policy_status": "mechanical_only_not_accounting_policy",
        },
        "assumptions": [
            "The exact M15 inputs were resubmitted and matched to the saved BFS commitments.",
            "The formula is a mechanical subtotal only, not recognized revenue, cost, profit, "
            "or accounting truth.",
            "Raw amount inputs are excluded from the report manifest and lineage sheet.",
        ],
        "lineage": lineage,
        "evidence_count": len(evidence),
        "evidence_references": evidence,
        "evidence_truncated": False,
        "result": {
            "output": output,
            "formula": trace.formula.model_dump(mode="json", exclude_none=True),
            "replay_status": "verified",
            "replayed_trace_hash": verification.replayed_trace_hash,
            "reason_codes": list(trace.reason_codes),
        },
        "data_points": data_points,
    }


def _build_source_payload(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    reference: ReportSourceReference,
    *,
    settings: Settings,
    request_id: UUID,
) -> dict[str, object]:
    if reference.kind == "business_intelligence_analysis":
        return _build_bi_analysis_source(session, user, tenant_id, reference, settings=settings)
    if reference.kind == "forecast_run":
        return _build_forecast_run_source(session, user, tenant_id, reference, settings=settings)
    if reference.kind == "forecast_evaluation":
        return _build_forecast_evaluation_source(
            session, user, tenant_id, reference, settings=settings
        )
    if reference.kind == "direct_cash_forecast_run":
        return _build_direct_cash_source(session, user, tenant_id, reference, settings=settings)
    if reference.kind == "financial_calculation_run":
        return _build_financial_calculation_source(
            session,
            user,
            tenant_id,
            reference,
            settings=settings,
            request_id=request_id,
        )
    raise ApiError(422, "unsupported_report_source", "This report source type is unsupported.")


def _build_manifest(
    *,
    report_id: UUID,
    tenant_id: UUID,
    company: Company,
    actor: UserAccount,
    body: ReportExportCreateRequest,
    sources: list[dict[str, object]],
    created_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    periods: list[dict[str, object]] = []
    currencies: set[str] = set()
    manifest_sources: list[dict[str, object]] = []
    assumptions = [
        "Prototype export only; production use and customer/finance authorization "
        "are not established.",
        "The report renderer does not calculate or reclassify business measures.",
        "Currency values remain separate; no foreign-exchange conversion is performed.",
        "Workbook formulas are not generated and arbitrary formula input is not accepted.",
        "Source claims and caller-asserted provenance are not independently verified by reporting.",
    ]
    for source in sources:
        source_period = _period(source.get("period_start"), source.get("period_end"))
        if source_period:
            periods.append(
                {"source_kind": source.get("kind"), "source_id": source.get("id"), **source_period}
            )
        currencies.update(
            item
            for item in _as_list(source.get("currency_codes"))
            if isinstance(item, str) and len(item) == 3
        )
        lineage = _as_dict(source.get("lineage"))
        manifest_sources.append(
            {
                "kind": source.get("kind"),
                "id": source.get("id"),
                "label": source.get("label"),
                "domain": source.get("domain"),
                "status": source.get("status"),
                "period": source_period,
                "currency_codes": source.get("currency_codes", []),
                "unit": source.get("unit"),
                "quality": source.get("quality", {}),
                "assumptions": source.get("assumptions", []),
                "lineage": lineage,
                "evidence_count": source.get("evidence_count", 0),
                "evidence_truncated": source.get("evidence_truncated", False),
            }
        )
        source_assumptions = _as_list(source.get("assumptions"))
        assumptions.extend(item for item in source_assumptions if isinstance(item, str))

    return {
        "schema_version": 1,
        "engine_version": REPORT_ENGINE_VERSION,
        "report_id": str(report_id),
        "tenant_id": str(tenant_id),
        "company_id": str(company.id),
        "company_name": company.legal_name,
        "company_base_currency": company.base_currency,
        "company_timezone": company.timezone,
        "created_by_user_id": str(actor.id),
        "title": body.title,
        "format": body.format,
        "mode": REPORT_MODE,
        "generated_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "retention_days": body.retention_days,
        "source_count": len(sources),
        "scope": {
            "periods": periods,
            "currency_codes": sorted(currencies),
            "currency_treatment": "separate_no_conversion",
        },
        "governance": {
            "production_approved": False,
            "production_use": "blocked_pending_governance",
            "calculation_authority": "saved BusinessFlow capability outputs only",
            "report_renderer_calculates_business_values": False,
            "workbook_formulas": "none",
            "retention": "rendered bytes expire at expires_at; owner/admin may revoke early",
            "evidence_and_lineage": (
                "pinned source IDs, versions, and hashes; no source-authority claim"
            ),
            "assumptions_and_limitations": list(dict.fromkeys(assumptions)),
        },
        "sources": manifest_sources,
    }


def _artifact_status(
    row: BusinessReportExport,
    *,
    now: datetime,
    revoked: bool,
    artifact_exists: bool,
) -> str:
    if revoked:
        return "revoked"
    if row.expires_at <= now:
        return "expired"
    if artifact_exists:
        return "available"
    return "purged"


def _summary(
    row: BusinessReportExport,
    *,
    now: datetime,
    revoked: bool,
    artifact_exists: bool,
) -> ReportExportSummary:
    _verify_export_record(row)
    status = _artifact_status(row, now=now, revoked=revoked, artifact_exists=artifact_exists)
    return ReportExportSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        title=row.title,
        format=cast(Any, row.format),
        mode=cast(Any, row.mode),
        manifest_hash=row.manifest_hash,
        artifact_sha256=row.artifact_sha256,
        artifact_size_bytes=row.artifact_size_bytes,
        source_count=row.source_count,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
        artifact_status=cast(Any, status),
    )


def _status_records(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    rows: list[BusinessReportExport],
    now: datetime,
) -> list[ReportExportSummary]:
    if not rows:
        return []
    ids = [row.id for row in rows]
    artifact_ids = set(
        session.scalars(
            select(BusinessReportArtifactContent.report_id).where(
                BusinessReportArtifactContent.tenant_id == tenant_id,
                BusinessReportArtifactContent.company_id == company_id,
                BusinessReportArtifactContent.report_id.in_(ids),
            )
        )
    )
    revoked_ids = set(
        session.scalars(
            select(BusinessReportArtifactRevocation.report_id).where(
                BusinessReportArtifactRevocation.tenant_id == tenant_id,
                BusinessReportArtifactRevocation.company_id == company_id,
                BusinessReportArtifactRevocation.report_id.in_(ids),
            )
        )
    )
    return [
        _summary(
            row,
            now=now,
            revoked=row.id in revoked_ids,
            artifact_exists=row.id in artifact_ids,
        )
        for row in rows
    ]


def create_report_export(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ReportExportCreateRequest,
    *,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ReportExportDetail:
    access = require_company_context_access(
        session, user, tenant_id, "report:create", request_id=request_id
    )
    _require_report_exports_enabled(settings)
    created_at = _database_now(session, now)
    sources = [
        _build_source_payload(
            session,
            user,
            tenant_id,
            reference,
            settings=settings,
            request_id=request_id,
        )
        for reference in body.sources
    ]
    report_id = uuid4()
    expires_at = created_at + timedelta(days=body.retention_days)
    manifest = _build_manifest(
        report_id=report_id,
        tenant_id=tenant_id,
        company=access.company,
        actor=user,
        body=body,
        sources=sources,
        created_at=created_at,
        expires_at=expires_at,
    )
    document = {"manifest": manifest, "sources": sources}
    try:
        content, _media_type, _extension = render_report(document, body.format)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ApiError(
            422,
            "report_export_render_failed",
            "The requested report exceeded the supported format or size constraints.",
        ) from exc
    if len(content) > MAX_REPORT_ARTIFACT_BYTES:
        raise ApiError(
            422,
            "report_export_too_large",
            "The rendered report exceeds the maximum supported artifact size.",
        )

    row = BusinessReportExport(
        id=report_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        title=body.title,
        format=body.format,
        mode=REPORT_MODE,
        manifest=manifest,
        manifest_hash=_manifest_hash(manifest),
        artifact_sha256=sha256(content).hexdigest(),
        artifact_size_bytes=len(content),
        source_count=len(sources),
        created_at=created_at,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    session.add(
        BusinessReportArtifactContent(
            tenant_id=tenant_id,
            company_id=access.company.id,
            report_id=report_id,
            content_bytes=content,
            created_at=created_at,
        )
    )
    session.flush()
    record_event(
        session,
        "business_report.export_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_report_export",
        target_id=report_id,
        request_id=request_id,
        details={
            "format": body.format,
            "mode": REPORT_MODE,
            "manifest_hash": row.manifest_hash,
            "artifact_sha256": row.artifact_sha256,
            "source_count": row.source_count,
            "expires_at": expires_at.isoformat(),
        },
    )
    return _detail(
        session,
        row,
        now=created_at,
        tenant_id=tenant_id,
        company_id=access.company.id,
    )


def _get_export(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    report_id: UUID,
) -> BusinessReportExport:
    row = session.scalar(
        select(BusinessReportExport).where(
            BusinessReportExport.id == report_id,
            BusinessReportExport.tenant_id == tenant_id,
            BusinessReportExport.company_id == company_id,
        )
    )
    if row is None:
        raise _not_found()
    _verify_export_record(row)
    return row


def _detail(
    session: Session,
    row: BusinessReportExport,
    *,
    now: datetime,
    tenant_id: UUID,
    company_id: UUID,
) -> ReportExportDetail:
    revoked = (
        session.scalar(
            select(BusinessReportArtifactRevocation.report_id).where(
                BusinessReportArtifactRevocation.tenant_id == tenant_id,
                BusinessReportArtifactRevocation.company_id == company_id,
                BusinessReportArtifactRevocation.report_id == row.id,
            )
        )
        is not None
    )
    artifact_exists = (
        session.scalar(
            select(BusinessReportArtifactContent.report_id).where(
                BusinessReportArtifactContent.tenant_id == tenant_id,
                BusinessReportArtifactContent.company_id == company_id,
                BusinessReportArtifactContent.report_id == row.id,
            )
        )
        is not None
    )
    summary = _summary(row, now=now, revoked=revoked, artifact_exists=artifact_exists)
    return ReportExportDetail(
        **summary.model_dump(),
        manifest=row.manifest,
        download_path=(
            f"/api/v1/tenants/{tenant_id}/reports/{row.id}/download"
            if summary.artifact_status == "available"
            else None
        ),
    )


def get_report_export(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    report_id: UUID,
    *,
    now: datetime,
    settings: Settings,
) -> ReportExportDetail:
    access = require_company_context_access(session, user, tenant_id, "report:read")
    _require_report_exports_enabled(settings)
    row = _get_export(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        report_id=report_id,
    )
    return _detail(
        session,
        row,
        now=_database_now(session, now),
        tenant_id=tenant_id,
        company_id=access.company.id,
    )


def list_report_exports(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    now: datetime,
    settings: Settings,
) -> ReportExportPage:
    access = require_company_context_access(session, user, tenant_id, "report:read")
    _require_report_exports_enabled(settings)
    filters = [
        BusinessReportExport.tenant_id == tenant_id,
        BusinessReportExport.company_id == access.company.id,
    ]
    total = int(
        session.scalar(select(func.count()).select_from(BusinessReportExport).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(BusinessReportExport)
            .where(*filters)
            .order_by(BusinessReportExport.created_at.desc(), BusinessReportExport.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return ReportExportPage(
        items=_status_records(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            rows=rows,
            now=_database_now(session, now),
        ),
        total=total,
        limit=limit,
        offset=offset,
    )


def download_report_export(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    report_id: UUID,
    *,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> tuple[ReportExportSummary, bytes]:
    access = require_company_context_access(session, user, tenant_id, "report:export")
    _require_report_exports_enabled(settings)
    row = _get_export(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        report_id=report_id,
    )
    current_time = _database_now(session, now)
    revoked = (
        session.scalar(
            select(BusinessReportArtifactRevocation.report_id).where(
                BusinessReportArtifactRevocation.tenant_id == tenant_id,
                BusinessReportArtifactRevocation.company_id == access.company.id,
                BusinessReportArtifactRevocation.report_id == report_id,
            )
        )
        is not None
    )
    if revoked:
        raise ApiError(410, "report_export_revoked", "This report artifact has been revoked.")
    if row.expires_at <= current_time:
        raise ApiError(410, "report_export_expired", "This report artifact has expired.")
    artifact = session.scalar(
        select(BusinessReportArtifactContent).where(
            BusinessReportArtifactContent.tenant_id == tenant_id,
            BusinessReportArtifactContent.company_id == access.company.id,
            BusinessReportArtifactContent.report_id == report_id,
        )
    )
    if artifact is None:
        raise ApiError(410, "report_artifact_purged", "This report artifact is no longer retained.")
    content = bytes(artifact.content_bytes)
    if (
        len(content) != row.artifact_size_bytes
        or sha256(content).hexdigest() != row.artifact_sha256
    ):
        raise ApiError(
            409,
            "report_artifact_integrity_error",
            "The stored report artifact failed its integrity check.",
        )
    summary = _summary(row, now=current_time, revoked=False, artifact_exists=True)
    record_event(
        session,
        "business_report.export_downloaded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_report_export",
        target_id=report_id,
        request_id=request_id,
        details={"format": row.format, "artifact_sha256": row.artifact_sha256},
    )
    return summary, content


def revoke_report_export(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    report_id: UUID,
    body: ReportExportRevokeRequest,
    *,
    request_id: UUID,
    now: datetime,
) -> ReportExportRevocationResponse:
    access = require_company_context_access(
        session, user, tenant_id, "report:retention_manage", request_id=request_id
    )
    row = _get_export(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        report_id=report_id,
    )
    existing = session.scalar(
        select(BusinessReportArtifactRevocation).where(
            BusinessReportArtifactRevocation.tenant_id == tenant_id,
            BusinessReportArtifactRevocation.company_id == access.company.id,
            BusinessReportArtifactRevocation.report_id == report_id,
        )
    )
    if existing is not None:
        return ReportExportRevocationResponse(
            report_id=report_id,
            revoked_by_user_id=existing.revoked_by_user_id,
            revoked_at=existing.revoked_at,
            artifact_status="revoked",
        )
    revoked_at = _database_now(session, now)
    session.add(
        BusinessReportArtifactRevocation(
            tenant_id=tenant_id,
            company_id=access.company.id,
            report_id=report_id,
            revoked_by_user_id=user.id,
            reason=body.reason.strip(),
            revoked_at=revoked_at,
        )
    )
    session.flush()
    record_event(
        session,
        "business_report.export_revoked",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_report_export",
        target_id=report_id,
        request_id=request_id,
        outcome="revoked",
        details={"manifest_hash": row.manifest_hash},
    )
    return ReportExportRevocationResponse(
        report_id=report_id,
        revoked_by_user_id=user.id,
        revoked_at=revoked_at,
        artifact_status="revoked",
    )


def purge_report_artifacts(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    now: datetime,
    request_id: UUID,
    limit: int = 100,
) -> ReportArtifactPurgeResponse:
    access = require_company_context_access(
        session, user, tenant_id, "report:retention_manage", request_id=request_id
    )
    current_time = _database_now(session, now)
    revoked = exists(
        select(BusinessReportArtifactRevocation.report_id).where(
            BusinessReportArtifactRevocation.tenant_id == tenant_id,
            BusinessReportArtifactRevocation.company_id == access.company.id,
            BusinessReportArtifactRevocation.report_id == BusinessReportExport.id,
        )
    )
    eligible = select(BusinessReportExport.id).where(
        BusinessReportExport.tenant_id == tenant_id,
        BusinessReportExport.company_id == access.company.id,
        or_(BusinessReportExport.expires_at <= current_time, revoked),
    )
    report_ids = list(
        session.scalars(
            eligible.order_by(BusinessReportExport.expires_at, BusinessReportExport.id).limit(limit)
        )
    )
    purged = 0
    if report_ids:
        artifact_ids = list(
            session.scalars(
                select(BusinessReportArtifactContent.report_id).where(
                    BusinessReportArtifactContent.tenant_id == tenant_id,
                    BusinessReportArtifactContent.company_id == access.company.id,
                    BusinessReportArtifactContent.report_id.in_(report_ids),
                )
            )
        )
        session.execute(
            delete(BusinessReportArtifactContent).where(
                BusinessReportArtifactContent.tenant_id == tenant_id,
                BusinessReportArtifactContent.company_id == access.company.id,
                BusinessReportArtifactContent.report_id.in_(report_ids),
            )
        )
        purged = len(artifact_ids)
    if purged:
        record_event(
            session,
            "business_report.retention_purged",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            request_id=request_id,
            details={"purged_artifact_count": purged},
        )
    return ReportArtifactPurgeResponse(
        purged_artifact_count=purged,
        maximum_batch_size=limit,
    )
