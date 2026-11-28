from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.config import Settings
from businessflow.errors import ApiError
from businessflow.forecasting.calculation import (
    FORECAST_ENGINE_VERSION,
    FORECAST_MAX_EVALUATION_POINTS,
    FORECAST_MAX_FORECAST_POINTS,
    assess_forecast_dataset,
    calculate_forecast_evaluation,
    calculate_future_forecast,
    model_comparison_summary,
    validate_replay_snapshot,
)
from businessflow.forecasting.contracts import (
    ForecastCatalogResponse,
    ForecastDatasetCreate,
    ForecastDatasetDetail,
    ForecastDatasetEligibility,
    ForecastDatasetPage,
    ForecastDatasetSummary,
    ForecastEvaluationCreate,
    ForecastEvaluationPage,
    ForecastEvaluationProtocolCreate,
    ForecastEvaluationReplayResponse,
    ForecastEvaluationResponse,
    ForecastEvaluationSummary,
    ForecastModelCreate,
    ForecastModelDefinition,
    ForecastModelPage,
    ForecastModelSummary,
    ForecastPolicyDefaults,
    ForecastPromotionCreate,
    ForecastPromotionPage,
    ForecastPromotionResponse,
    ForecastPromotionSummary,
    ForecastProtocolPage,
    ForecastProtocolSummary,
    ForecastRunCreate,
    ForecastRunPage,
    ForecastRunReplayResponse,
    ForecastRunResponse,
    ForecastRunSummary,
)
from businessflow.forecasting.models import (
    ForecastDatasetVersion,
    ForecastEvaluation,
    ForecastEvaluationModelVersion,
    ForecastEvaluationProtocolVersion,
    ForecastModelVersion,
    ForecastPromotionDecision,
    ForecastRun,
)
from businessflow.metrics.snapshot import content_hash
from businessflow.models import Company, UserAccount
from businessflow.services.company_context_access import require_company_context_access

_MODEL_DEFINITION_ADAPTER: TypeAdapter[ForecastModelDefinition] = TypeAdapter(
    cast(Any, ForecastModelDefinition)
)
_PAGE_LIMIT_MAX = 100
_PAGE_OFFSET_MAX = 100_000


def ensure_forecasting_enabled(settings: Settings | None) -> None:
    """Fail closed by default and block use in production until governance is established."""
    if (
        getattr(settings, "forecasting_enabled", None) is not True
        or getattr(settings, "app_env", None) == "production"
    ):
        raise ApiError(
            503,
            "forecasting_disabled",
            "Forecasting is disabled by deployment configuration or production governance gates.",
        )


def ensure_forecast_promotion_enabled(settings: Settings | None) -> None:
    ensure_forecasting_enabled(settings)
    if (
        getattr(settings, "forecast_promotion_enabled", None) is not True
        or getattr(settings, "app_env", None) == "production"
    ):
        raise ApiError(
            503,
            "forecast_promotion_disabled",
            "Forecast promotion decisions are disabled or blocked by production governance gates.",
        )


def _aware_utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("forecast evidence timestamps must include a timezone")
    return now.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _aware_utc(value).isoformat()


def _resource_not_found(resource: str) -> ApiError:
    return ApiError(404, f"forecast_{resource}_not_found", f"Forecast {resource} was not found.")


def _integrity_error() -> ApiError:
    return ApiError(
        409,
        "forecast_evidence_integrity_error",
        "Saved forecasting evidence could not be verified.",
    )


def _version_conflict(resource: str, message: str) -> ApiError:
    return ApiError(409, f"forecast_{resource}_version_conflict", message)


def _lock_company(session: Session, tenant_id: UUID, company_id: UUID) -> Company:
    """Serialize append-only company writes without granting UPDATE on M17 evidence."""
    company = session.scalar(
        select(Company)
        .where(Company.tenant_id == tenant_id, Company.id == company_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if company is None:
        raise ApiError(404, "company_not_found", "Company was not found.")
    return company


def _model_hash(
    model_key: str, model_version: int, display_name: str, definition: dict[str, object]
) -> str:
    return content_hash(
        {
            "snapshot_schema_version": 1,
            "model_key": model_key,
            "model_version": model_version,
            "display_name": display_name,
            "definition": definition,
        }
    )


def _protocol_hash(
    protocol_key: str,
    protocol_version: int,
    display_name: str,
    target_key: str,
    frequency: str,
    definition: dict[str, object],
) -> str:
    return content_hash(
        {
            "snapshot_schema_version": 1,
            "protocol_key": protocol_key,
            "protocol_version": protocol_version,
            "display_name": display_name,
            "target_key": target_key,
            "frequency": frequency,
            "definition": definition,
        }
    )


def _evaluation_hash(
    *,
    evaluation_id: UUID,
    tenant_id: UUID,
    company_id: UUID,
    creator_id: UUID,
    dataset_version_id: UUID,
    protocol_version_id: UUID,
    calibration_evaluation_id: UUID | None,
    stage: str,
    status: str,
    model_count: int,
    dataset_hash: str,
    protocol_hash: str,
    input_hash: str,
    result_hash: str,
    created_at: datetime,
) -> str:
    return content_hash(
        {
            "schema_version": 1,
            "evaluation_id": str(evaluation_id),
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "created_by_user_id": str(creator_id),
            "dataset_version_id": str(dataset_version_id),
            "protocol_version_id": str(protocol_version_id),
            "calibration_evaluation_id": (
                str(calibration_evaluation_id) if calibration_evaluation_id is not None else None
            ),
            "stage": stage,
            "evaluation_status": status,
            "model_count": model_count,
            "dataset_hash": dataset_hash,
            "protocol_hash": protocol_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "created_at": _timestamp(created_at),
        }
    )


def _run_hash(
    *,
    run_id: UUID,
    tenant_id: UUID,
    company_id: UUID,
    requester_id: UUID,
    dataset_version_id: UUID,
    protocol_version_id: UUID,
    model_version_id: UUID,
    calibration_evaluation_id: UUID | None,
    status: str,
    horizon_periods: int,
    dataset_hash: str,
    model_hash: str,
    protocol_hash: str,
    input_hash: str,
    result_hash: str,
    created_at: datetime,
) -> str:
    return content_hash(
        {
            "schema_version": 1,
            "forecast_run_id": str(run_id),
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "requested_by_user_id": str(requester_id),
            "dataset_version_id": str(dataset_version_id),
            "protocol_version_id": str(protocol_version_id),
            "model_version_id": str(model_version_id),
            "calibration_evaluation_id": (
                str(calibration_evaluation_id) if calibration_evaluation_id is not None else None
            ),
            "run_status": status,
            "horizon_periods": horizon_periods,
            "dataset_hash": dataset_hash,
            "model_hash": model_hash,
            "protocol_hash": protocol_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "created_at": _timestamp(created_at),
        }
    )


def _promotion_hash(
    *,
    promotion_id: UUID,
    tenant_id: UUID,
    company_id: UUID,
    evaluation_id: UUID,
    model_version_id: UUID,
    decision: str,
    evaluation_hash: str,
    model_hash: str,
    rationale: str,
    reviewer_id: UUID,
    created_at: datetime,
) -> str:
    return content_hash(
        {
            "schema_version": 1,
            "promotion_id": str(promotion_id),
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "evaluation_id": str(evaluation_id),
            "model_version_id": str(model_version_id),
            "decision": decision,
            "promotion_scope": "nonproduction_only",
            "production_activation_status": "blocked_pending_governance",
            "evaluation_hash": evaluation_hash,
            "model_hash": model_hash,
            "rationale": rationale,
            "decided_by_user_id": str(reviewer_id),
            "created_at": _timestamp(created_at),
        }
    )


def _dataset_contract(snapshot: dict[str, object]) -> ForecastDatasetCreate:
    try:
        return ForecastDatasetCreate.model_validate_json(json.dumps(snapshot))
    except (TypeError, ValueError, ValidationError) as exc:
        raise _integrity_error() from exc


def _dataset_series_count(dataset: ForecastDatasetCreate) -> int:
    return len({content_hash(dict(item.dimension_values)) for item in dataset.observations})


def _verify_dataset(row: ForecastDatasetVersion) -> ForecastDatasetCreate:
    dataset = _dataset_contract(row.snapshot)
    if (
        content_hash(row.snapshot) != row.snapshot_hash
        or row.dataset_key != dataset.dataset_key
        or row.display_name != dataset.display_name
        or row.data_origin != dataset.data_origin
        or row.target_key != dataset.target_key
        or row.target_label != dataset.target_label
        or row.unit != dataset.unit
        or row.currency_code != dataset.currency_code
        or row.frequency != dataset.frequency
        or row.start_period != min(item.period_start for item in dataset.observations)
        or row.end_period != max(item.period_start for item in dataset.observations)
        or row.observation_count != len(dataset.observations)
        or row.series_count != _dataset_series_count(dataset)
        or (
            dataset.source_lineage is not None
            and dataset.source_lineage.company_id != row.company_id
        )
    ):
        raise _integrity_error()
    return dataset


def _require_dataset_ready_for_forecast(row: ForecastDatasetVersion) -> None:
    dataset = _verify_dataset(row)
    lineage = dataset.source_lineage
    if lineage is not None and (
        lineage.readiness_status != "ready_for_synthetic_evaluation"
        or lineage.point_in_time_status != "eligible"
    ):
        raise ApiError(
            409,
            "forecast_dataset_not_ready",
            "Dataset readiness or point-in-time lineage blocks forecasting and evaluation.",
        )


def _dataset_eligibility(row: ForecastDatasetVersion) -> ForecastDatasetEligibility:
    dataset = _verify_dataset(row)
    try:
        return ForecastDatasetEligibility.model_validate(
            assess_forecast_dataset(dataset), strict=True
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise _integrity_error() from exc


def _dataset_summary(row: ForecastDatasetVersion) -> ForecastDatasetSummary:
    _verify_dataset(row)
    return ForecastDatasetSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        dataset_key=row.dataset_key,
        version=row.dataset_version,
        display_name=row.display_name,
        data_origin=cast(Literal["synthetic_fixture", "caller_assertion"], row.data_origin),
        target_key=row.target_key,
        target_label=row.target_label,
        unit=row.unit,
        currency_code=row.currency_code,
        frequency=cast(Literal["daily", "weekly", "monthly"], row.frequency),
        start_period=row.start_period,
        end_period=row.end_period,
        observation_count=row.observation_count,
        series_count=row.series_count,
        snapshot_hash=row.snapshot_hash,
        created_by_user_id=row.requested_by_user_id,
        created_at=_timestamp(row.created_at),
    )


def _get_dataset(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    dataset_version_id: UUID,
) -> ForecastDatasetVersion:
    row = session.scalar(
        select(ForecastDatasetVersion).where(
            ForecastDatasetVersion.tenant_id == tenant_id,
            ForecastDatasetVersion.company_id == company_id,
            ForecastDatasetVersion.id == dataset_version_id,
        )
    )
    if row is None:
        raise _resource_not_found("dataset")
    _verify_dataset(row)
    return row


def _get_model(
    session: Session, tenant_id: UUID, company_id: UUID, model_version_id: UUID
) -> ForecastModelVersion:
    row = session.scalar(
        select(ForecastModelVersion).where(
            ForecastModelVersion.tenant_id == tenant_id,
            ForecastModelVersion.company_id == company_id,
            ForecastModelVersion.id == model_version_id,
        )
    )
    if row is None:
        raise _resource_not_found("model")
    try:
        definition = _MODEL_DEFINITION_ADAPTER.validate_python(row.definition, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise _integrity_error() from exc
    expected_hash = _model_hash(
        row.model_key, row.model_version, row.display_name, definition.model_dump(mode="json")
    )
    if row.model_kind != definition.kind or row.model_hash != expected_hash:
        raise _integrity_error()
    return row


def _model_summary(row: ForecastModelVersion) -> ForecastModelSummary:
    _MODEL_DEFINITION_ADAPTER.validate_python(row.definition, strict=True)
    return ForecastModelSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        model_key=row.model_key,
        version=row.model_version,
        display_name=row.display_name,
        definition=_MODEL_DEFINITION_ADAPTER.validate_python(row.definition, strict=True),
        model_hash=row.model_hash,
        created_by_user_id=row.created_by_user_id,
        created_at=_timestamp(row.created_at),
    )


def _get_protocol(
    session: Session, tenant_id: UUID, company_id: UUID, protocol_version_id: UUID
) -> ForecastEvaluationProtocolVersion:
    row = session.scalar(
        select(ForecastEvaluationProtocolVersion).where(
            ForecastEvaluationProtocolVersion.tenant_id == tenant_id,
            ForecastEvaluationProtocolVersion.company_id == company_id,
            ForecastEvaluationProtocolVersion.id == protocol_version_id,
        )
    )
    if row is None:
        raise _resource_not_found("protocol")
    try:
        definition = ForecastEvaluationProtocolCreate.model_validate(row.definition, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise _integrity_error() from exc
    expected_hash = _protocol_hash(
        row.protocol_key,
        row.protocol_version,
        row.display_name,
        row.target_key,
        row.frequency,
        definition.model_dump(mode="json", exclude={"expected_version"}),
    )
    if (
        row.target_key != definition.target_key
        or row.frequency != definition.frequency
        or row.protocol_hash != expected_hash
    ):
        raise _integrity_error()
    return row


def _protocol_summary(row: ForecastEvaluationProtocolVersion) -> ForecastProtocolSummary:
    _get_protocol_definition = ForecastEvaluationProtocolCreate.model_validate(
        row.definition, strict=True
    )
    return ForecastProtocolSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        protocol_key=row.protocol_key,
        version=row.protocol_version,
        display_name=row.display_name,
        target_key=row.target_key,
        frequency=cast(Literal["daily", "weekly", "monthly"], row.frequency),
        definition=_get_protocol_definition.model_dump(mode="json", exclude={"expected_version"}),
        protocol_hash=row.protocol_hash,
        policy_approval_status="not_established",
        created_by_user_id=row.created_by_user_id,
        created_at=_timestamp(row.created_at),
    )


def _get_evaluation(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    evaluation_id: UUID,
) -> ForecastEvaluation:
    row = session.scalar(
        select(ForecastEvaluation).where(
            ForecastEvaluation.tenant_id == tenant_id,
            ForecastEvaluation.company_id == company_id,
            ForecastEvaluation.id == evaluation_id,
        )
    )
    if row is None:
        raise _resource_not_found("evaluation")
    return row


def _evaluation_models(session: Session, row: ForecastEvaluation) -> list[ForecastModelVersion]:
    links = list(
        session.scalars(
            select(ForecastEvaluationModelVersion)
            .where(
                ForecastEvaluationModelVersion.tenant_id == row.tenant_id,
                ForecastEvaluationModelVersion.company_id == row.company_id,
                ForecastEvaluationModelVersion.evaluation_id == row.id,
            )
            .order_by(ForecastEvaluationModelVersion.model_index)
        )
    )
    if len(links) != row.model_count or not 1 <= len(links) <= 8:
        raise _integrity_error()
    return [
        _get_model(session, row.tenant_id, row.company_id, link.model_version_id) for link in links
    ]


def _evaluation_input_snapshot(
    *,
    evaluation_id: UUID,
    tenant_id: UUID,
    company_id: UUID,
    created_by_user_id: UUID,
    dataset: ForecastDatasetVersion,
    protocol: ForecastEvaluationProtocolVersion,
    models: list[ForecastModelVersion],
    stage: str,
    calibration: ForecastEvaluation | None,
    engine_version: str = FORECAST_ENGINE_VERSION,
) -> dict[str, object]:
    return {
        "snapshot_schema_version": 1,
        "engine_version": engine_version,
        "evaluation_id": str(evaluation_id),
        "tenant_id": str(tenant_id),
        "company_id": str(company_id),
        "created_by_user_id": str(created_by_user_id),
        "dataset": {"id": str(dataset.id), "snapshot_hash": dataset.snapshot_hash},
        "protocol": {"id": str(protocol.id), "protocol_hash": protocol.protocol_hash},
        "models": [{"id": str(model.id), "model_hash": model.model_hash} for model in models],
        "stage": stage,
        "calibration_evaluation": (
            {
                "id": str(calibration.id),
                "evaluation_hash": calibration.evaluation_hash,
                "result_hash": calibration.result_hash,
            }
            if calibration is not None
            else None
        ),
        "policy_origin": "system_default_not_company_approved",
        "source_authority": "caller_assertions_not_independently_verified",
    }


def _evaluation_status(
    result: dict[str, object],
) -> Literal["evaluated", "partial", "not_evaluable", "failed"]:
    status = result.get("evaluation_status")
    if isinstance(status, str) and status in {
        "evaluated",
        "partial",
        "not_evaluable",
        "failed",
    }:
        return cast(Literal["evaluated", "partial", "not_evaluable", "failed"], status)
    return "failed"


def _failed_evaluation_result(stage: str, exc: Exception) -> dict[str, object]:
    return {
        "snapshot_schema_version": 1,
        "engine_version": FORECAST_ENGINE_VERSION,
        "stage": stage,
        "evaluation_status": "failed",
        "reason_codes": ["deterministic_calculation_failed"],
        "failure_type": type(exc).__name__[:80],
        "prediction_points": [],
        "model_results": [],
        "comparison": {
            "selection": "none",
            "recommendation": "none",
            "ranking_applied": False,
        },
        "governance_status": {
            "source_authority": "not_established",
            "company_policy_approval": "not_established",
            "forecast_accuracy_claim": "not_asserted",
            "production_customer_data_authorization": "not_authorized",
        },
    }


def _evaluation_summary(session: Session, row: ForecastEvaluation) -> ForecastEvaluationSummary:
    models = _evaluation_models(session, row)
    return ForecastEvaluationSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        dataset_version_id=row.dataset_version_id,
        protocol_version_id=row.protocol_version_id,
        calibration_evaluation_id=row.calibration_evaluation_id,
        stage=cast(Literal["validation", "holdout"], row.stage),
        evaluation_status=cast(
            Literal["evaluated", "partial", "not_evaluable", "failed"],
            row.evaluation_status,
        ),
        model_version_ids=[model.id for model in models],
        dataset_hash=row.dataset_hash,
        protocol_hash=row.protocol_hash,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        evaluation_hash=row.evaluation_hash,
        created_by_user_id=row.created_by_user_id,
        created_at=_timestamp(row.created_at),
    )


def _verify_evaluation_record(
    session: Session, row: ForecastEvaluation, _seen: set[UUID] | None = None
) -> None:
    seen = _seen if _seen is not None else set()
    if row.id in seen:
        raise _integrity_error()
    seen.add(row.id)
    engine_version = row.input_snapshot.get("engine_version")
    if (
        not isinstance(engine_version, str)
        or content_hash(row.input_snapshot) != row.input_hash
        or content_hash(row.result_snapshot) != row.result_hash
        or type(row.result_snapshot.get("snapshot_schema_version")) is not int
        or row.result_snapshot.get("snapshot_schema_version") != 1
        or row.result_snapshot.get("engine_version") != engine_version
        or row.result_snapshot.get("stage") != row.stage
        or row.result_snapshot.get("evaluation_status") != row.evaluation_status
        or row.stage not in {"validation", "holdout"}
        or row.evaluation_status not in {"evaluated", "partial", "not_evaluable", "failed"}
    ):
        raise _integrity_error()
    dataset = _get_dataset(session, row.tenant_id, row.company_id, row.dataset_version_id)
    protocol = _get_protocol(session, row.tenant_id, row.company_id, row.protocol_version_id)
    models = _evaluation_models(session, row)
    calibration: ForecastEvaluation | None = None
    if row.calibration_evaluation_id is not None:
        calibration = _get_evaluation(
            session, row.tenant_id, row.company_id, row.calibration_evaluation_id
        )
        _verify_evaluation_record(session, calibration, seen)
        calibration_models = _evaluation_models(session, calibration)
        if (
            row.stage != "holdout"
            or calibration.stage != "validation"
            or calibration.evaluation_status != "evaluated"
            or not isinstance(calibration.result_snapshot.get("prediction_points"), list)
            or calibration.dataset_version_id != row.dataset_version_id
            or calibration.protocol_version_id != row.protocol_version_id
            or [model.id for model in calibration_models] != [model.id for model in models]
        ):
            raise _integrity_error()
    elif row.stage == "holdout":
        raise _integrity_error()
    expected_input = _evaluation_input_snapshot(
        evaluation_id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        created_by_user_id=row.created_by_user_id,
        dataset=dataset,
        protocol=protocol,
        models=models,
        stage=row.stage,
        calibration=calibration,
        engine_version=engine_version,
    )
    if (
        row.dataset_hash != dataset.snapshot_hash
        or row.protocol_hash != protocol.protocol_hash
        or content_hash(expected_input) != row.input_hash
        or row.evaluation_hash
        != _evaluation_hash(
            evaluation_id=row.id,
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            creator_id=row.created_by_user_id,
            dataset_version_id=row.dataset_version_id,
            protocol_version_id=row.protocol_version_id,
            calibration_evaluation_id=row.calibration_evaluation_id,
            stage=row.stage,
            status=row.evaluation_status,
            model_count=row.model_count,
            dataset_hash=row.dataset_hash,
            protocol_hash=row.protocol_hash,
            input_hash=row.input_hash,
            result_hash=row.result_hash,
            created_at=row.created_at,
        )
    ):
        raise _integrity_error()
    seen.remove(row.id)


def _evaluation_response(session: Session, row: ForecastEvaluation) -> ForecastEvaluationResponse:
    _verify_evaluation_record(session, row)
    comparison = model_comparison_summary(row.result_snapshot)
    return ForecastEvaluationResponse(
        evaluation=_evaluation_summary(session, row),
        result_snapshot=row.result_snapshot,
        comparison=comparison,
    )


def _get_run(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    run_id: UUID,
) -> ForecastRun:
    row = session.scalar(
        select(ForecastRun).where(
            ForecastRun.tenant_id == tenant_id,
            ForecastRun.company_id == company_id,
            ForecastRun.id == run_id,
        )
    )
    if row is None:
        raise _resource_not_found("run")
    return row


def _run_input_snapshot(
    *,
    run_id: UUID,
    tenant_id: UUID,
    company_id: UUID,
    requested_by_user_id: UUID,
    dataset: ForecastDatasetVersion,
    protocol: ForecastEvaluationProtocolVersion,
    model: ForecastModelVersion,
    calibration: ForecastEvaluation | None,
    engine_version: str = FORECAST_ENGINE_VERSION,
) -> dict[str, object]:
    definition = ForecastEvaluationProtocolCreate.model_validate(protocol.definition, strict=True)
    return {
        "snapshot_schema_version": 1,
        "engine_version": engine_version,
        "forecast_run_id": str(run_id),
        "tenant_id": str(tenant_id),
        "company_id": str(company_id),
        "requested_by_user_id": str(requested_by_user_id),
        "dataset": {"id": str(dataset.id), "snapshot_hash": dataset.snapshot_hash},
        "protocol": {"id": str(protocol.id), "protocol_hash": protocol.protocol_hash},
        "model": {"id": str(model.id), "model_hash": model.model_hash},
        "calibration_evaluation": (
            {
                "id": str(calibration.id),
                "evaluation_hash": calibration.evaluation_hash,
                "result_hash": calibration.result_hash,
            }
            if calibration is not None
            else None
        ),
        "horizon_periods": definition.horizon_periods,
        "policy_origin": "system_default_not_company_approved",
        "source_authority": "caller_assertions_not_independently_verified",
    }


def _run_status(
    result: dict[str, object],
) -> Literal["forecasted", "partial", "not_evaluable", "failed"]:
    status = result.get("forecast_status")
    if isinstance(status, str) and status in {
        "forecasted",
        "partial",
        "not_evaluable",
        "failed",
    }:
        return cast(Literal["forecasted", "partial", "not_evaluable", "failed"], status)
    return "failed"


def _failed_forecast_result(exc: Exception) -> dict[str, object]:
    return {
        "snapshot_schema_version": 1,
        "engine_version": FORECAST_ENGINE_VERSION,
        "forecast_status": "failed",
        "reason_codes": ["deterministic_calculation_failed"],
        "failure_type": type(exc).__name__[:80],
        "forecast_points": [],
        "governance_status": {
            "source_authority": "not_established",
            "company_policy_approval": "not_established",
            "forecast_accuracy_claim": "not_asserted",
            "production_customer_data_authorization": "not_authorized",
        },
    }


def _run_summary(row: ForecastRun) -> ForecastRunSummary:
    return ForecastRunSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        dataset_version_id=row.dataset_version_id,
        protocol_version_id=row.protocol_version_id,
        model_version_id=row.model_version_id,
        calibration_evaluation_id=row.calibration_evaluation_id,
        run_status=cast(
            Literal["forecasted", "partial", "not_evaluable", "failed"], row.run_status
        ),
        horizon_periods=row.horizon_periods,
        dataset_hash=row.dataset_hash,
        model_hash=row.model_hash,
        protocol_hash=row.protocol_hash,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        run_hash=row.run_hash,
        created_by_user_id=row.requested_by_user_id,
        created_at=_timestamp(row.created_at),
    )


def _verify_run_record(session: Session, row: ForecastRun) -> None:
    engine_version = row.input_snapshot.get("engine_version")
    if (
        not isinstance(engine_version, str)
        or content_hash(row.input_snapshot) != row.input_hash
        or content_hash(row.result_snapshot) != row.result_hash
        or type(row.result_snapshot.get("snapshot_schema_version")) is not int
        or row.result_snapshot.get("snapshot_schema_version") != 1
        or row.result_snapshot.get("engine_version") != engine_version
        or row.result_snapshot.get("forecast_status") != row.run_status
        or row.run_status not in {"forecasted", "partial", "not_evaluable", "failed"}
    ):
        raise _integrity_error()
    dataset = _get_dataset(session, row.tenant_id, row.company_id, row.dataset_version_id)
    protocol = _get_protocol(session, row.tenant_id, row.company_id, row.protocol_version_id)
    model = _get_model(session, row.tenant_id, row.company_id, row.model_version_id)
    calibration: ForecastEvaluation | None = None
    if row.calibration_evaluation_id is not None:
        calibration = _get_evaluation(
            session, row.tenant_id, row.company_id, row.calibration_evaluation_id
        )
        _verify_evaluation_record(session, calibration)
        calibration_models = _evaluation_models(session, calibration)
        if (
            calibration.stage != "validation"
            or calibration.evaluation_status != "evaluated"
            or not isinstance(calibration.result_snapshot.get("prediction_points"), list)
            or calibration.dataset_version_id != dataset.id
            or calibration.protocol_version_id != protocol.id
            or model.id not in {candidate.id for candidate in calibration_models}
        ):
            raise _integrity_error()
    protocol_contract = ForecastEvaluationProtocolCreate.model_validate(
        protocol.definition, strict=True
    )
    if (
        dataset.target_key != protocol.target_key
        or dataset.frequency != protocol.frequency
        or (protocol_contract.interval is not None and calibration is None)
    ):
        raise _integrity_error()
    expected_input = _run_input_snapshot(
        run_id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        requested_by_user_id=row.requested_by_user_id,
        dataset=dataset,
        protocol=protocol,
        model=model,
        calibration=calibration,
        engine_version=engine_version,
    )
    if (
        row.dataset_hash != dataset.snapshot_hash
        or row.protocol_hash != protocol.protocol_hash
        or row.model_hash != model.model_hash
        or row.horizon_periods != protocol_contract.horizon_periods
        or content_hash(expected_input) != row.input_hash
        or row.run_hash
        != _run_hash(
            run_id=row.id,
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            requester_id=row.requested_by_user_id,
            dataset_version_id=row.dataset_version_id,
            protocol_version_id=row.protocol_version_id,
            model_version_id=row.model_version_id,
            calibration_evaluation_id=row.calibration_evaluation_id,
            status=row.run_status,
            horizon_periods=row.horizon_periods,
            dataset_hash=row.dataset_hash,
            model_hash=row.model_hash,
            protocol_hash=row.protocol_hash,
            input_hash=row.input_hash,
            result_hash=row.result_hash,
            created_at=row.created_at,
        )
    ):
        raise _integrity_error()


def _run_response(session: Session, row: ForecastRun) -> ForecastRunResponse:
    _verify_run_record(session, row)
    return ForecastRunResponse(run=_run_summary(row), result_snapshot=row.result_snapshot)


def _promotion_summary(row: ForecastPromotionDecision) -> ForecastPromotionSummary:
    return ForecastPromotionSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        evaluation_id=row.evaluation_id,
        model_version_id=row.model_version_id,
        decision=cast(Literal["approve", "reject"], row.decision),
        promotion_scope=cast(Literal["nonproduction_only"], row.promotion_scope),
        production_activation_status=cast(
            Literal["blocked_pending_governance"], row.production_activation_status
        ),
        evaluation_hash=row.evaluation_hash,
        model_hash=row.model_hash,
        rationale=row.rationale,
        decided_by_user_id=row.decided_by_user_id,
        promotion_hash=row.promotion_hash,
        created_at=_timestamp(row.created_at),
    )


def _verify_promotion(session: Session, row: ForecastPromotionDecision) -> None:
    if (
        row.promotion_scope != "nonproduction_only"
        or row.production_activation_status != "blocked_pending_governance"
        or row.promotion_hash
        != _promotion_hash(
            promotion_id=row.id,
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            evaluation_id=row.evaluation_id,
            model_version_id=row.model_version_id,
            decision=row.decision,
            evaluation_hash=row.evaluation_hash,
            model_hash=row.model_hash,
            rationale=row.rationale,
            reviewer_id=row.decided_by_user_id,
            created_at=row.created_at,
        )
    ):
        raise _integrity_error()
    evaluation = _get_evaluation(session, row.tenant_id, row.company_id, row.evaluation_id)
    _verify_evaluation_record(session, evaluation)
    model = _get_model(session, row.tenant_id, row.company_id, row.model_version_id)
    dataset = _get_dataset(session, row.tenant_id, row.company_id, evaluation.dataset_version_id)
    protocol = _get_protocol(session, row.tenant_id, row.company_id, evaluation.protocol_version_id)
    model_ids = {candidate.id for candidate in _evaluation_models(session, evaluation)}
    if (
        evaluation.stage != "holdout"
        or evaluation.evaluation_status != "evaluated"
        or row.evaluation_hash != evaluation.evaluation_hash
        or row.model_hash != model.model_hash
        or row.model_version_id not in model_ids
        or row.decided_by_user_id
        in {
            evaluation.created_by_user_id,
            model.created_by_user_id,
            dataset.requested_by_user_id,
            protocol.created_by_user_id,
        }
        or (row.decision == "approve" and dataset.data_origin == "synthetic_fixture")
    ):
        raise _integrity_error()


def get_forecast_catalog(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    settings: Settings,
) -> ForecastCatalogResponse:
    ensure_forecasting_enabled(settings)
    require_company_context_access(session, user, tenant_id, "forecast:read")
    return ForecastCatalogResponse(
        schema_version=1,
        engine_version=FORECAST_ENGINE_VERSION,
        supported_frequencies=["daily", "weekly", "monthly"],
        supported_baselines=["naive_last_value", "seasonal_naive", "moving_average"],
        supported_metrics=["mae", "rmse", "mape", "smape"],
        maximum_observations_per_dataset=20_000,
        maximum_models_per_evaluation=8,
        maximum_evaluation_points=FORECAST_MAX_EVALUATION_POINTS,
        maximum_forecast_points=FORECAST_MAX_FORECAST_POINTS,
        policy_defaults=ForecastPolicyDefaults(
            policy_origin="system_default_not_company_approved",
            split_method="chronological_expanding_window_rolling_origin",
            missing_actuals="exclude_and_report_without_imputation",
            zero_actual_mape="undefined_not_zero_filled",
            zero_actual_and_forecast_smape="undefined_not_zero_filled",
            sparse_or_insufficient_history="return_not_evaluable_or_abstain",
            outliers="not_assessed_or_flag_only_never_silently_removed",
            uncertainty="not_configured_unless_validation_calibration_is_available",
            model_selection="no_automatic_ranking_or_recommendation",
            production_activation="blocked_pending_governance_and_approval",
            source_authority="caller_assertions_are_not_independently_verified",
        ),
    )


def create_forecast_dataset(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ForecastDatasetCreate,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ForecastDatasetDetail:
    ensure_forecasting_enabled(settings)
    captured_at = _aware_utc(now)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    if body.source_lineage is not None and body.source_lineage.company_id != access.company.id:
        raise ApiError(
            403,
            "forecast_dataset_company_scope_mismatch",
            "Synthetic dataset lineage does not match the authenticated Company scope.",
        )
    _lock_company(session, tenant_id, access.company.id)
    previous = session.scalar(
        select(ForecastDatasetVersion)
        .where(
            ForecastDatasetVersion.tenant_id == tenant_id,
            ForecastDatasetVersion.company_id == access.company.id,
            ForecastDatasetVersion.dataset_key == body.dataset_key,
        )
        .order_by(ForecastDatasetVersion.dataset_version.desc())
        .limit(1)
    )
    if previous is None:
        if body.expected_version is not None:
            raise _version_conflict("dataset", "No prior dataset version exists.")
        version = 1
        previous_version = None
    else:
        if body.expected_version != previous.dataset_version:
            raise _version_conflict(
                "dataset", "expected_version must match the latest dataset version."
            )
        version = previous.dataset_version + 1
        previous_version = previous.dataset_version
    snapshot = body.model_dump(mode="json", exclude={"expected_version"})
    series_count = _dataset_series_count(body)
    start_period = min(item.period_start for item in body.observations)
    end_period = max(item.period_start for item in body.observations)
    row = ForecastDatasetVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        dataset_key=body.dataset_key,
        dataset_version=version,
        previous_version=previous_version,
        display_name=body.display_name,
        data_origin=body.data_origin,
        target_key=body.target_key,
        target_label=body.target_label,
        unit=body.unit,
        currency_code=body.currency_code,
        frequency=body.frequency,
        start_period=start_period,
        end_period=end_period,
        observation_count=len(body.observations),
        series_count=series_count,
        snapshot_hash=content_hash(snapshot),
        snapshot=snapshot,
        created_at=captured_at,
    )
    session.add(row)
    session.flush()
    eligibility = _dataset_eligibility(row)
    record_event(
        session,
        "forecast.dataset_version_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="forecast_dataset_version",
        target_id=row.id,
        request_id=request_id,
        details={
            "dataset_key": row.dataset_key,
            "dataset_version": row.dataset_version,
            "snapshot_hash": row.snapshot_hash,
            "data_origin": row.data_origin,
            "target_key": row.target_key,
            "frequency": row.frequency,
            "observation_count": row.observation_count,
            "series_count": row.series_count,
            "dataset_eligibility": eligibility.status,
            "eligibility_reason_codes": eligibility.reason_codes,
            "source_authority": "not_established",
            "data_foundation_readiness": (
                body.source_lineage.readiness_status if body.source_lineage is not None else None
            ),
            "data_foundation_reason_codes": (
                body.source_lineage.readiness_reason_codes
                if body.source_lineage is not None
                else []
            ),
            "point_in_time_status": (
                body.source_lineage.point_in_time_status
                if body.source_lineage is not None
                else "not_established"
            ),
        },
    )
    return ForecastDatasetDetail(
        dataset=_dataset_summary(row),
        snapshot=row.snapshot,
        eligibility=eligibility,
    )


def list_forecast_datasets(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> ForecastDatasetPage:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    if not 1 <= limit <= _PAGE_LIMIT_MAX or not 0 <= offset <= _PAGE_OFFSET_MAX:
        raise ApiError(422, "invalid_pagination", "Pagination values are outside supported limits.")
    filters = (
        ForecastDatasetVersion.tenant_id == tenant_id,
        ForecastDatasetVersion.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(ForecastDatasetVersion).where(*filters))
        or 0
    )
    rows = list(
        session.scalars(
            select(ForecastDatasetVersion)
            .where(*filters)
            .order_by(
                ForecastDatasetVersion.dataset_key,
                ForecastDatasetVersion.dataset_version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return ForecastDatasetPage(
        items=[_dataset_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


def get_forecast_dataset(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    dataset_version_id: UUID,
    *,
    settings: Settings,
) -> ForecastDatasetDetail:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    row = _get_dataset(session, tenant_id, access.company.id, dataset_version_id)
    return ForecastDatasetDetail(
        dataset=_dataset_summary(row),
        snapshot=row.snapshot,
        eligibility=_dataset_eligibility(row),
    )


def create_forecast_model(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ForecastModelCreate,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ForecastModelSummary:
    ensure_forecasting_enabled(settings)
    captured_at = _aware_utc(now)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    _lock_company(session, tenant_id, access.company.id)
    previous = session.scalar(
        select(ForecastModelVersion)
        .where(
            ForecastModelVersion.tenant_id == tenant_id,
            ForecastModelVersion.company_id == access.company.id,
            ForecastModelVersion.model_key == body.model_key,
        )
        .order_by(ForecastModelVersion.model_version.desc())
        .limit(1)
    )
    if previous is None:
        if body.expected_version is not None:
            raise _version_conflict("model", "No prior model version exists.")
        version = 1
        previous_version = None
    else:
        if body.expected_version != previous.model_version:
            raise _version_conflict(
                "model", "expected_version must match the latest model version."
            )
        version = previous.model_version + 1
        previous_version = previous.model_version
    definition = body.definition.model_dump(mode="json")
    model_hash = _model_hash(body.model_key, version, body.display_name, definition)
    row = ForecastModelVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        model_key=body.model_key,
        model_version=version,
        previous_version=previous_version,
        display_name=body.display_name,
        model_kind=cast(str, definition["kind"]),
        definition=definition,
        model_hash=model_hash,
        created_at=captured_at,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "forecast.model_version_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="forecast_model_version",
        target_id=row.id,
        request_id=request_id,
        details={
            "model_key": row.model_key,
            "model_version": row.model_version,
            "model_kind": row.model_kind,
            "model_hash": row.model_hash,
        },
    )
    return _model_summary(row)


def list_forecast_models(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> ForecastModelPage:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    if not 1 <= limit <= _PAGE_LIMIT_MAX or not 0 <= offset <= _PAGE_OFFSET_MAX:
        raise ApiError(422, "invalid_pagination", "Pagination values are outside supported limits.")
    filters = (
        ForecastModelVersion.tenant_id == tenant_id,
        ForecastModelVersion.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(ForecastModelVersion).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(ForecastModelVersion)
            .where(*filters)
            .order_by(ForecastModelVersion.model_key, ForecastModelVersion.model_version.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    for row in rows:
        _get_model(session, tenant_id, access.company.id, row.id)
    return ForecastModelPage(
        items=[_model_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


def create_forecast_protocol(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ForecastEvaluationProtocolCreate,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ForecastProtocolSummary:
    ensure_forecasting_enabled(settings)
    captured_at = _aware_utc(now)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    _lock_company(session, tenant_id, access.company.id)
    previous = session.scalar(
        select(ForecastEvaluationProtocolVersion)
        .where(
            ForecastEvaluationProtocolVersion.tenant_id == tenant_id,
            ForecastEvaluationProtocolVersion.company_id == access.company.id,
            ForecastEvaluationProtocolVersion.protocol_key == body.protocol_key,
        )
        .order_by(ForecastEvaluationProtocolVersion.protocol_version.desc())
        .limit(1)
    )
    if previous is None:
        if body.expected_version is not None:
            raise _version_conflict("protocol", "No prior protocol version exists.")
        version = 1
        previous_version = None
    else:
        if body.expected_version != previous.protocol_version:
            raise _version_conflict(
                "protocol", "expected_version must match the latest protocol version."
            )
        version = previous.protocol_version + 1
        previous_version = previous.protocol_version
    definition = body.model_dump(mode="json", exclude={"expected_version"})
    protocol_hash = _protocol_hash(
        body.protocol_key,
        version,
        body.display_name,
        body.target_key,
        body.frequency,
        definition,
    )
    row = ForecastEvaluationProtocolVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        protocol_key=body.protocol_key,
        protocol_version=version,
        previous_version=previous_version,
        display_name=body.display_name,
        target_key=body.target_key,
        frequency=body.frequency,
        definition=definition,
        protocol_hash=protocol_hash,
        created_at=captured_at,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "forecast.protocol_version_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="forecast_evaluation_protocol_version",
        target_id=row.id,
        request_id=request_id,
        details={
            "protocol_key": row.protocol_key,
            "protocol_version": row.protocol_version,
            "protocol_hash": row.protocol_hash,
            "policy_approval_status": "not_established",
            "policy_origin": "caller_configured_not_company_approved",
        },
    )
    return _protocol_summary(row)


def list_forecast_protocols(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> ForecastProtocolPage:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    if not 1 <= limit <= _PAGE_LIMIT_MAX or not 0 <= offset <= _PAGE_OFFSET_MAX:
        raise ApiError(422, "invalid_pagination", "Pagination values are outside supported limits.")
    filters = (
        ForecastEvaluationProtocolVersion.tenant_id == tenant_id,
        ForecastEvaluationProtocolVersion.company_id == access.company.id,
    )
    total = (
        session.scalar(
            select(func.count()).select_from(ForecastEvaluationProtocolVersion).where(*filters)
        )
        or 0
    )
    rows = list(
        session.scalars(
            select(ForecastEvaluationProtocolVersion)
            .where(*filters)
            .order_by(
                ForecastEvaluationProtocolVersion.protocol_key,
                ForecastEvaluationProtocolVersion.protocol_version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    for row in rows:
        _get_protocol(session, tenant_id, access.company.id, row.id)
    return ForecastProtocolPage(
        items=[_protocol_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _validation_calibration(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    dataset: ForecastDatasetVersion,
    protocol: ForecastEvaluationProtocolVersion,
    models: list[ForecastModelVersion],
    calibration_id: UUID,
    require_exact_model_set: bool = True,
) -> ForecastEvaluation:
    calibration = _get_evaluation(session, tenant_id, company_id, calibration_id)
    _verify_evaluation_record(session, calibration)
    linked_models = _evaluation_models(session, calibration)
    linked_model_ids = [model.id for model in linked_models]
    requested_model_ids = [model.id for model in models]
    model_reference_is_valid = (
        linked_model_ids == requested_model_ids
        if require_exact_model_set
        else len(requested_model_ids) == 1 and requested_model_ids[0] in linked_model_ids
    )
    if (
        calibration.stage != "validation"
        or calibration.evaluation_status != "evaluated"
        or calibration.dataset_version_id != dataset.id
        or calibration.protocol_version_id != protocol.id
        or not model_reference_is_valid
    ):
        raise ApiError(
            409,
            "forecast_calibration_ineligible",
            "Calibration must be a complete validation evaluation on this exact dataset, "
            "protocol, and ordered candidate-model set.",
        )
    return calibration


def create_forecast_evaluation(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ForecastEvaluationCreate,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ForecastEvaluationResponse:
    ensure_forecasting_enabled(settings)
    captured_at = _aware_utc(now)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    _lock_company(session, tenant_id, access.company.id)
    dataset = _get_dataset(session, tenant_id, access.company.id, body.dataset_version_id)
    _require_dataset_ready_for_forecast(dataset)
    protocol = _get_protocol(session, tenant_id, access.company.id, body.protocol_version_id)
    models = [
        _get_model(session, tenant_id, access.company.id, model_id)
        for model_id in body.model_version_ids
    ]
    if dataset.target_key != protocol.target_key or dataset.frequency != protocol.frequency:
        raise ApiError(
            409,
            "forecast_dataset_protocol_mismatch",
            "Dataset target and frequency must match the selected protocol.",
        )
    calibration: ForecastEvaluation | None = None
    if body.stage == "holdout":
        if body.calibration_evaluation_id is None:
            raise ApiError(
                422,
                "forecast_calibration_required",
                "A holdout must reference its prior validation evaluation.",
            )
        previous_holdout_id = session.scalar(
            select(ForecastEvaluation.id).where(
                ForecastEvaluation.tenant_id == tenant_id,
                ForecastEvaluation.company_id == access.company.id,
                ForecastEvaluation.dataset_version_id == dataset.id,
                ForecastEvaluation.stage == "holdout",
            )
        )
        if previous_holdout_id is not None:
            raise ApiError(
                409,
                "forecast_holdout_already_consumed",
                "This dataset's one-time holdout has already been consumed.",
            )
        calibration = _validation_calibration(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            dataset=dataset,
            protocol=protocol,
            models=models,
            calibration_id=body.calibration_evaluation_id,
        )
    elif body.calibration_evaluation_id is not None:
        raise ApiError(
            422,
            "forecast_validation_calibration_invalid",
            "Validation evaluations cannot reference a calibration evaluation.",
        )

    evaluation_id = uuid4()
    input_snapshot = _evaluation_input_snapshot(
        evaluation_id=evaluation_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        dataset=dataset,
        protocol=protocol,
        models=models,
        stage=body.stage,
        calibration=calibration,
    )
    input_hash = content_hash(input_snapshot)
    protocol_contract = ForecastEvaluationProtocolCreate.model_validate(
        protocol.definition, strict=True
    )
    model_inputs = [
        {
            "id": str(model.id),
            "model_key": model.model_key,
            "version": model.model_version,
            "model_hash": model.model_hash,
            "definition": model.definition,
        }
        for model in models
    ]
    calibration_snapshot = (
        {
            "evaluation_id": str(calibration.id),
            "prediction_points": calibration.result_snapshot.get("prediction_points", []),
        }
        if calibration is not None
        else None
    )
    try:
        result_snapshot = calculate_forecast_evaluation(
            dataset_snapshot=dataset.snapshot,
            protocol_definition=protocol_contract.model_dump(
                mode="json", exclude={"expected_version"}
            ),
            model_versions=model_inputs,
            stage=body.stage,
            calibration_snapshot=calibration_snapshot,
        )
    except Exception as exc:
        result_snapshot = _failed_evaluation_result(body.stage, exc)
    status = _evaluation_status(result_snapshot)
    result_hash = content_hash(result_snapshot)
    created_at = captured_at
    evaluation_hash = _evaluation_hash(
        evaluation_id=evaluation_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        creator_id=user.id,
        dataset_version_id=dataset.id,
        protocol_version_id=protocol.id,
        calibration_evaluation_id=calibration.id if calibration is not None else None,
        stage=body.stage,
        status=status,
        model_count=len(models),
        dataset_hash=dataset.snapshot_hash,
        protocol_hash=protocol.protocol_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        created_at=created_at,
    )
    row = ForecastEvaluation(
        id=evaluation_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        dataset_version_id=dataset.id,
        protocol_version_id=protocol.id,
        calibration_evaluation_id=calibration.id if calibration is not None else None,
        stage=body.stage,
        evaluation_status=status,
        model_count=len(models),
        dataset_hash=dataset.snapshot_hash,
        protocol_hash=protocol.protocol_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        evaluation_hash=evaluation_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    for index, model in enumerate(models, start=1):
        session.add(
            ForecastEvaluationModelVersion(
                tenant_id=tenant_id,
                company_id=access.company.id,
                evaluation_id=evaluation_id,
                model_version_id=model.id,
                model_index=index,
            )
        )
    session.flush()
    record_event(
        session,
        "forecast.evaluation_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="forecast_evaluation",
        target_id=evaluation_id,
        request_id=request_id,
        details={
            "stage": body.stage,
            "evaluation_status": status,
            "dataset_version_id": dataset.id,
            "dataset_hash": dataset.snapshot_hash,
            "protocol_version_id": protocol.id,
            "protocol_hash": protocol.protocol_hash,
            "model_version_ids": [model.id for model in models],
            "input_hash": input_hash,
            "result_hash": result_hash,
            "evaluation_hash": evaluation_hash,
            "calibration_evaluation_id": calibration.id if calibration is not None else None,
            "data_origin": dataset.data_origin,
            "production_activation_status": "blocked_pending_governance",
        },
    )
    return _evaluation_response(session, row)


def list_forecast_evaluations(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> ForecastEvaluationPage:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    if not 1 <= limit <= _PAGE_LIMIT_MAX or not 0 <= offset <= _PAGE_OFFSET_MAX:
        raise ApiError(422, "invalid_pagination", "Pagination values are outside supported limits.")
    filters = (
        ForecastEvaluation.tenant_id == tenant_id,
        ForecastEvaluation.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(ForecastEvaluation).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(ForecastEvaluation)
            .where(*filters)
            .order_by(ForecastEvaluation.created_at.desc(), ForecastEvaluation.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    for row in rows:
        _verify_evaluation_record(session, row)
    return ForecastEvaluationPage(
        items=[_evaluation_summary(session, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_forecast_evaluation(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    evaluation_id: UUID,
    *,
    settings: Settings,
) -> ForecastEvaluationResponse:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    row = _get_evaluation(session, tenant_id, access.company.id, evaluation_id)
    return _evaluation_response(session, row)


def _replay_calculation_resources(
    session: Session, row: ForecastEvaluation
) -> tuple[
    ForecastDatasetVersion,
    ForecastEvaluationProtocolVersion,
    list[ForecastModelVersion],
    ForecastEvaluation | None,
]:
    dataset = _get_dataset(session, row.tenant_id, row.company_id, row.dataset_version_id)
    protocol = _get_protocol(session, row.tenant_id, row.company_id, row.protocol_version_id)
    models = _evaluation_models(session, row)
    calibration = (
        _get_evaluation(session, row.tenant_id, row.company_id, row.calibration_evaluation_id)
        if row.calibration_evaluation_id is not None
        else None
    )
    return dataset, protocol, models, calibration


def verify_forecast_evaluation(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    evaluation_id: UUID,
    *,
    request_id: UUID,
    settings: Settings,
) -> ForecastEvaluationReplayResponse:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    row = _get_evaluation(session, tenant_id, access.company.id, evaluation_id)
    _verify_evaluation_record(session, row)
    stored_engine_version = row.input_snapshot.get("engine_version")
    if stored_engine_version != FORECAST_ENGINE_VERSION:
        return ForecastEvaluationReplayResponse(
            evaluation_id=row.id,
            replay_status="mismatch",
            stored_input_hash=row.input_hash,
            replayed_input_hash=row.input_hash,
            stored_result_hash=row.result_hash,
            replayed_result_hash=row.result_hash,
            stored_evaluation_hash=row.evaluation_hash,
            replayed_evaluation_hash=row.evaluation_hash,
            reason_codes=["evaluation_engine_version_not_supported"],
        )
    dataset, protocol, models, calibration = _replay_calculation_resources(session, row)
    expected_input = _evaluation_input_snapshot(
        evaluation_id=row.id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=row.created_by_user_id,
        dataset=dataset,
        protocol=protocol,
        models=models,
        stage=row.stage,
        calibration=calibration,
    )
    replayed_input_hash = content_hash(expected_input)
    protocol_definition = ForecastEvaluationProtocolCreate.model_validate(
        protocol.definition, strict=True
    )
    model_inputs = [
        {
            "id": str(model.id),
            "model_key": model.model_key,
            "version": model.model_version,
            "model_hash": model.model_hash,
            "definition": model.definition,
        }
        for model in models
    ]
    calibration_snapshot = (
        {
            "evaluation_id": str(calibration.id),
            "prediction_points": calibration.result_snapshot.get("prediction_points", []),
        }
        if calibration is not None
        else None
    )
    try:
        validate_replay_snapshot(dataset.snapshot, protocol.definition, model_inputs)
        replayed_result = calculate_forecast_evaluation(
            dataset_snapshot=dataset.snapshot,
            protocol_definition=protocol_definition.model_dump(
                mode="json", exclude={"expected_version"}
            ),
            model_versions=model_inputs,
            stage=row.stage,
            calibration_snapshot=calibration_snapshot,
        )
    except Exception as exc:
        replayed_result = _failed_evaluation_result(row.stage, exc)
    replayed_result_hash = content_hash(replayed_result)
    replayed_evaluation_hash = _evaluation_hash(
        evaluation_id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        creator_id=row.created_by_user_id,
        dataset_version_id=row.dataset_version_id,
        protocol_version_id=row.protocol_version_id,
        calibration_evaluation_id=row.calibration_evaluation_id,
        stage=row.stage,
        status=_evaluation_status(replayed_result),
        model_count=row.model_count,
        dataset_hash=row.dataset_hash,
        protocol_hash=row.protocol_hash,
        input_hash=replayed_input_hash,
        result_hash=replayed_result_hash,
        created_at=row.created_at,
    )
    reasons: list[str] = []
    if replayed_input_hash != row.input_hash:
        reasons.append("persisted_input_binding_mismatch")
    if replayed_result_hash != row.result_hash:
        reasons.append("persisted_result_mismatch")
    if replayed_evaluation_hash != row.evaluation_hash:
        reasons.append("persisted_evaluation_hash_mismatch")
    return ForecastEvaluationReplayResponse(
        evaluation_id=row.id,
        replay_status="verified" if not reasons else "mismatch",
        stored_input_hash=row.input_hash,
        replayed_input_hash=replayed_input_hash,
        stored_result_hash=row.result_hash,
        replayed_result_hash=replayed_result_hash,
        stored_evaluation_hash=row.evaluation_hash,
        replayed_evaluation_hash=replayed_evaluation_hash,
        reason_codes=reasons,
    )


def create_forecast_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ForecastRunCreate,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ForecastRunResponse:
    ensure_forecasting_enabled(settings)
    created_at = _aware_utc(now)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    dataset = _get_dataset(session, tenant_id, access.company.id, body.dataset_version_id)
    _require_dataset_ready_for_forecast(dataset)
    protocol = _get_protocol(session, tenant_id, access.company.id, body.protocol_version_id)
    model = _get_model(session, tenant_id, access.company.id, body.model_version_id)
    protocol_definition = ForecastEvaluationProtocolCreate.model_validate(
        protocol.definition, strict=True
    )
    if dataset.target_key != protocol.target_key or dataset.frequency != protocol.frequency:
        raise ApiError(
            409,
            "forecast_dataset_protocol_mismatch",
            "Dataset target and frequency must match the selected protocol.",
        )
    calibration: ForecastEvaluation | None = None
    if body.calibration_evaluation_id is not None:
        calibration = _validation_calibration(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            dataset=dataset,
            protocol=protocol,
            models=[model],
            calibration_id=body.calibration_evaluation_id,
            require_exact_model_set=False,
        )
    if protocol_definition.interval is not None and calibration is None:
        raise ApiError(
            409,
            "forecast_calibration_required",
            "This protocol requires a complete, same-model validation evaluation for intervals.",
        )
    run_id = uuid4()
    input_snapshot = _run_input_snapshot(
        run_id=run_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        dataset=dataset,
        protocol=protocol,
        model=model,
        calibration=calibration,
    )
    input_hash = content_hash(input_snapshot)
    dataset_contract = _verify_dataset(dataset)
    calibration_snapshot = (
        {
            "evaluation_id": str(calibration.id),
            "prediction_points": calibration.result_snapshot.get("prediction_points", []),
        }
        if calibration is not None
        else None
    )
    try:
        result_snapshot = calculate_future_forecast(
            dataset_snapshot=dataset.snapshot,
            protocol_definition=protocol_definition.model_dump(
                mode="json", exclude={"expected_version"}
            ),
            model_definition=model.definition,
            model_version_id=str(model.id),
            model_hash=model.model_hash,
            calibration_snapshot=calibration_snapshot,
        )
    except Exception as exc:
        result_snapshot = _failed_forecast_result(exc)
    status = _run_status(result_snapshot)
    result_hash = content_hash(result_snapshot)
    row = ForecastRun(
        id=run_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        dataset_version_id=dataset.id,
        protocol_version_id=protocol.id,
        model_version_id=model.id,
        calibration_evaluation_id=calibration.id if calibration is not None else None,
        run_status=status,
        horizon_periods=protocol_definition.horizon_periods,
        dataset_hash=dataset.snapshot_hash,
        model_hash=model.model_hash,
        protocol_hash=protocol.protocol_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=created_at,
    )
    row.run_hash = _run_hash(
        run_id=run_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requester_id=user.id,
        dataset_version_id=dataset.id,
        protocol_version_id=protocol.id,
        model_version_id=model.id,
        calibration_evaluation_id=calibration.id if calibration is not None else None,
        status=status,
        horizon_periods=protocol_definition.horizon_periods,
        dataset_hash=dataset.snapshot_hash,
        model_hash=model.model_hash,
        protocol_hash=protocol.protocol_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "forecast.run_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="forecast_run",
        target_id=run_id,
        request_id=request_id,
        details={
            "run_status": status,
            "dataset_version_id": dataset.id,
            "dataset_hash": dataset.snapshot_hash,
            "protocol_version_id": protocol.id,
            "protocol_hash": protocol.protocol_hash,
            "model_version_id": model.id,
            "model_hash": model.model_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "run_hash": row.run_hash,
            "calibration_evaluation_id": calibration.id if calibration is not None else None,
            "data_origin": dataset_contract.data_origin,
            "production_activation_status": "blocked_pending_governance",
        },
    )
    return _run_response(session, row)


def list_forecast_runs(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> ForecastRunPage:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    if not 1 <= limit <= _PAGE_LIMIT_MAX or not 0 <= offset <= _PAGE_OFFSET_MAX:
        raise ApiError(422, "invalid_pagination", "Pagination values are outside supported limits.")
    filters = (ForecastRun.tenant_id == tenant_id, ForecastRun.company_id == access.company.id)
    total = session.scalar(select(func.count()).select_from(ForecastRun).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(ForecastRun)
            .where(*filters)
            .order_by(ForecastRun.created_at.desc(), ForecastRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    for row in rows:
        _verify_run_record(session, row)
    return ForecastRunPage(
        items=[_run_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


def get_forecast_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    run_id: UUID,
    *,
    settings: Settings,
) -> ForecastRunResponse:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    row = _get_run(session, tenant_id, access.company.id, run_id)
    return _run_response(session, row)


def verify_forecast_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    run_id: UUID,
    *,
    request_id: UUID,
    settings: Settings,
) -> ForecastRunReplayResponse:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:write", request_id=request_id
    )
    row = _get_run(session, tenant_id, access.company.id, run_id)
    _verify_run_record(session, row)
    if row.input_snapshot.get("engine_version") != FORECAST_ENGINE_VERSION:
        return ForecastRunReplayResponse(
            forecast_run_id=row.id,
            replay_status="mismatch",
            stored_input_hash=row.input_hash,
            replayed_input_hash=row.input_hash,
            stored_result_hash=row.result_hash,
            replayed_result_hash=row.result_hash,
            reason_codes=["forecast_engine_version_not_supported"],
        )
    dataset = _get_dataset(session, tenant_id, access.company.id, row.dataset_version_id)
    protocol = _get_protocol(session, tenant_id, access.company.id, row.protocol_version_id)
    model = _get_model(session, tenant_id, access.company.id, row.model_version_id)
    calibration = (
        _get_evaluation(session, tenant_id, access.company.id, row.calibration_evaluation_id)
        if row.calibration_evaluation_id is not None
        else None
    )
    if calibration is not None:
        _verify_evaluation_record(session, calibration)
    expected_input = _run_input_snapshot(
        run_id=row.id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=row.requested_by_user_id,
        dataset=dataset,
        protocol=protocol,
        model=model,
        calibration=calibration,
    )
    replayed_input_hash = content_hash(expected_input)
    definition = ForecastEvaluationProtocolCreate.model_validate(protocol.definition, strict=True)
    calibration_snapshot = (
        {
            "evaluation_id": str(calibration.id),
            "prediction_points": calibration.result_snapshot.get("prediction_points", []),
        }
        if calibration is not None
        else None
    )
    try:
        validate_replay_snapshot(
            dataset.snapshot, protocol.definition, [{"definition": model.definition}]
        )
        replayed_result = calculate_future_forecast(
            dataset_snapshot=dataset.snapshot,
            protocol_definition=definition.model_dump(mode="json", exclude={"expected_version"}),
            model_definition=model.definition,
            model_version_id=str(model.id),
            model_hash=model.model_hash,
            calibration_snapshot=calibration_snapshot,
        )
    except Exception as exc:
        replayed_result = _failed_forecast_result(exc)
    replayed_result_hash = content_hash(replayed_result)
    reasons: list[str] = []
    if replayed_input_hash != row.input_hash:
        reasons.append("persisted_input_binding_mismatch")
    if replayed_result_hash != row.result_hash:
        reasons.append("persisted_result_mismatch")
    return ForecastRunReplayResponse(
        forecast_run_id=row.id,
        replay_status="verified" if not reasons else "mismatch",
        stored_input_hash=row.input_hash,
        replayed_input_hash=replayed_input_hash,
        stored_result_hash=row.result_hash,
        replayed_result_hash=replayed_result_hash,
        reason_codes=reasons,
    )


def create_forecast_promotion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ForecastPromotionCreate,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> ForecastPromotionResponse:
    ensure_forecast_promotion_enabled(settings)
    created_at = _aware_utc(now)
    access = require_company_context_access(
        session, user, tenant_id, "forecast:review", request_id=request_id
    )
    # Evidence tables are INSERT/SELECT-only for runtime; serialize promotion decisions on the
    # mutable company row instead of requesting SELECT FOR UPDATE on immutable M17 evidence.
    _lock_company(session, tenant_id, access.company.id)
    evaluation = _get_evaluation(session, tenant_id, access.company.id, body.evaluation_id)
    _verify_evaluation_record(session, evaluation)
    models = _evaluation_models(session, evaluation)
    model = next((item for item in models if item.id == body.model_version_id), None)
    if model is None:
        raise _resource_not_found("evaluation model")
    dataset = _get_dataset(session, tenant_id, access.company.id, evaluation.dataset_version_id)
    protocol = _get_protocol(session, tenant_id, access.company.id, evaluation.protocol_version_id)
    if (
        body.expected_evaluation_hash != evaluation.evaluation_hash
        or body.expected_model_hash != model.model_hash
    ):
        raise ApiError(
            409,
            "forecast_promotion_evidence_changed",
            "The expected evaluation or model evidence hash does not match the saved evidence.",
        )
    if evaluation.stage != "holdout" or evaluation.evaluation_status != "evaluated":
        raise ApiError(
            409,
            "forecast_promotion_evidence_ineligible",
            "Only a complete, non-failed holdout evaluation can be considered for promotion.",
        )
    if body.decision == "approve" and dataset.data_origin == "synthetic_fixture":
        raise ApiError(
            409,
            "forecast_synthetic_promotion_blocked",
            "Synthetic fixtures are test evidence and cannot support an approval decision.",
        )
    if user.id in {
        evaluation.created_by_user_id,
        model.created_by_user_id,
        dataset.requested_by_user_id,
        protocol.created_by_user_id,
    }:
        raise ApiError(
            403,
            "forecast_promotion_separation_of_duties",
            "An authorized reviewer who did not create the dataset, protocol, evaluation, or model "
            "must decide.",
        )
    existing = session.scalar(
        select(ForecastPromotionDecision.id).where(
            ForecastPromotionDecision.tenant_id == tenant_id,
            ForecastPromotionDecision.company_id == access.company.id,
            ForecastPromotionDecision.evaluation_id == evaluation.id,
            ForecastPromotionDecision.model_version_id == model.id,
        )
    )
    if existing is not None:
        raise ApiError(
            409,
            "forecast_promotion_decision_exists",
            "An immutable human decision already exists for this evaluation and model.",
        )
    promotion_id = uuid4()
    promotion_hash = _promotion_hash(
        promotion_id=promotion_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        evaluation_id=evaluation.id,
        model_version_id=model.id,
        decision=body.decision,
        evaluation_hash=evaluation.evaluation_hash,
        model_hash=model.model_hash,
        rationale=body.rationale,
        reviewer_id=user.id,
        created_at=created_at,
    )
    row = ForecastPromotionDecision(
        id=promotion_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        evaluation_id=evaluation.id,
        model_version_id=model.id,
        decided_by_user_id=user.id,
        decision=body.decision,
        promotion_scope="nonproduction_only",
        production_activation_status="blocked_pending_governance",
        evaluation_hash=evaluation.evaluation_hash,
        model_hash=model.model_hash,
        rationale=body.rationale,
        promotion_hash=promotion_hash,
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "forecast.promotion_decided",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="forecast_promotion_decision",
        target_id=promotion_id,
        request_id=request_id,
        details={
            "evaluation_id": evaluation.id,
            "model_version_id": model.id,
            "decision": body.decision,
            "evaluation_hash": evaluation.evaluation_hash,
            "model_hash": model.model_hash,
            "promotion_hash": promotion_hash,
            "promotion_scope": "nonproduction_only",
            "production_activation_status": "blocked_pending_governance",
        },
    )
    return ForecastPromotionResponse(promotion=_promotion_summary(row))


def list_forecast_promotions(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> ForecastPromotionPage:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    if not 1 <= limit <= _PAGE_LIMIT_MAX or not 0 <= offset <= _PAGE_OFFSET_MAX:
        raise ApiError(422, "invalid_pagination", "Pagination values are outside supported limits.")
    filters = (
        ForecastPromotionDecision.tenant_id == tenant_id,
        ForecastPromotionDecision.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(ForecastPromotionDecision).where(*filters))
        or 0
    )
    rows = list(
        session.scalars(
            select(ForecastPromotionDecision)
            .where(*filters)
            .order_by(
                ForecastPromotionDecision.created_at.desc(),
                ForecastPromotionDecision.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    for row in rows:
        _verify_promotion(session, row)
    return ForecastPromotionPage(
        items=[_promotion_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_forecast_model(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    model_version_id: UUID,
    *,
    settings: Settings,
) -> ForecastModelSummary:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    row = _get_model(session, tenant_id, access.company.id, model_version_id)
    return _model_summary(row)


def get_forecast_protocol(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    protocol_version_id: UUID,
    *,
    settings: Settings,
) -> ForecastProtocolSummary:
    ensure_forecasting_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "forecast:read")
    row = _get_protocol(session, tenant_id, access.company.id, protocol_version_id)
    return _protocol_summary(row)
