from __future__ import annotations

import json
from datetime import date, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from businessflow.config import Settings
from businessflow.errors import ApiError
from businessflow.forecasting.calculation import (
    FORECAST_MAX_FORECAST_POINTS,
    _metric_result,
    assess_forecast_dataset,
    calculate_forecast_evaluation,
    calculate_future_forecast,
    model_comparison_summary,
)
from businessflow.forecasting.contracts import (
    ForecastDatasetCreate,
    ForecastEvaluationCreate,
    ForecastEvaluationProtocolCreate,
    ForecastPromotionCreate,
)
from businessflow.forecasting.service import (
    _failed_evaluation_result,
    ensure_forecast_promotion_enabled,
    ensure_forecasting_enabled,
)
from businessflow.metrics.snapshot import content_hash


def _dataset(
    values: list[str | None],
    *,
    data_origin: str = "synthetic_fixture",
    start: date = date(2026, 1, 1),
    series_count: int = 1,
) -> ForecastDatasetCreate:
    observations: list[dict[str, object]] = []
    for series_index in range(series_count):
        for index, value in enumerate(values):
            identity = {"series": f"series-{series_index:03d}"} if series_count > 1 else {}
            record = {
                "period_start": (start + timedelta(days=index)).isoformat(),
                "dimension_values": identity,
                "value": value,
                "source_reference": {
                    "evidence_type": data_origin,
                    "source_id": "test.forecast-fixture",
                    "source_hash": "a" * 64,
                    "record_locator": f"fixture:{series_index}:{index}",
                    "record_hash": content_hash(
                        {"series_index": series_index, "index": index, "value": value}
                    ),
                },
            }
            if value is None:
                record["missing_reason"] = "fixture_missing_value"
            observations.append(record)
    body: dict[str, object] = {
        "dataset_key": "test.daily-series",
        "display_name": "Forecast calculation test data",
        "data_origin": data_origin,
        "target_key": "revenue",
        "target_label": "Revenue",
        "unit": "USD",
        "currency_code": "USD",
        "frequency": "daily",
        "dimensions": ([{"key": "series", "label": "Series"}] if series_count > 1 else []),
        "observations": observations,
    }
    return ForecastDatasetCreate.model_validate_json(json.dumps(body))


def _protocol(
    *,
    validation_periods: int = 2,
    holdout_periods: int = 2,
    horizon_periods: int = 1,
    missing_actual_policy: str = "exclude_and_report",
    interval: bool = False,
) -> ForecastEvaluationProtocolCreate:
    body: dict[str, object] = {
        "protocol_key": "test.chronological-v1",
        "display_name": "Chronological test protocol",
        "target_key": "revenue",
        "frequency": "daily",
        "horizon_periods": horizon_periods,
        "validation_periods": validation_periods,
        "holdout_periods": holdout_periods,
        "metrics": ["mae", "rmse", "mape", "smape"],
        "missing_actual_policy": missing_actual_policy,
    }
    if interval:
        body["interval"] = {
            "method": "validation_absolute_error_nearest_rank",
            "coverage_level": "0.9",
            "minimum_calibration_points": 1,
        }
    return ForecastEvaluationProtocolCreate.model_validate_json(json.dumps(body))


def _models() -> list[dict[str, object]]:
    return [
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "model_key": "naive.last-value",
            "version": 1,
            "model_hash": "b" * 64,
            "definition": {"kind": "naive_last_value"},
        },
        {
            "id": "00000000-0000-4000-8000-000000000002",
            "model_key": "moving.average",
            "version": 1,
            "model_hash": "c" * 64,
            "definition": {"kind": "moving_average", "window_periods": 2},
        },
    ]


def _evaluate(
    dataset: ForecastDatasetCreate,
    protocol: ForecastEvaluationProtocolCreate,
    *,
    stage: str,
    calibration_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    return calculate_forecast_evaluation(
        dataset_snapshot=dataset.model_dump(mode="json", exclude={"expected_version"}),
        protocol_definition=protocol.model_dump(mode="json", exclude={"expected_version"}),
        model_versions=_models(),
        stage=stage,
        calibration_snapshot=calibration_snapshot,
    )


def test_validation_holdout_and_future_run_are_deterministic_and_disjoint() -> None:
    dataset = _dataset([str(value) for value in range(1, 9)])
    protocol = _protocol(validation_periods=2, holdout_periods=2, interval=True)

    validation = _evaluate(dataset, protocol, stage="validation")
    assert validation["evaluation_status"] == "evaluated"
    assert validation == _evaluate(dataset, protocol, stage="validation")
    validation_points = cast(list[dict[str, object]], validation["prediction_points"])
    assert [point["actual"] for point in validation_points] == ["5", "6"]
    assert [point["window_origin_period"] for point in validation_points] == [
        "2026-01-05",
        "2026-01-06",
    ]
    assert validation["comparison"]["ranking_applied"] is False  # type: ignore[index]

    calibration = {
        "evaluation_id": "00000000-0000-4000-8000-000000000003",
        "prediction_points": validation_points,
    }
    holdout = _evaluate(dataset, protocol, stage="holdout", calibration_snapshot=calibration)
    assert holdout["evaluation_status"] == "evaluated"
    holdout_points = cast(list[dict[str, object]], holdout["prediction_points"])
    assert [point["actual"] for point in holdout_points] == ["7", "8"]
    assert {point["window_origin_period"] for point in holdout_points} == {"2026-01-07"}
    assert holdout["split"]["holdout_uses_one_fixed_forecast_origin"] is True  # type: ignore[index]
    first_naive = cast(list[dict[str, object]], holdout_points[0]["model_predictions"])[0]
    assert first_naive["prediction"] == "6"
    assert first_naive["prediction_interval"]["status"] == "empirical_non_guaranteed"  # type: ignore[index]

    forecast = calculate_future_forecast(
        dataset_snapshot=dataset.model_dump(mode="json", exclude={"expected_version"}),
        protocol_definition=protocol.model_dump(mode="json", exclude={"expected_version"}),
        model_definition=_models()[0]["definition"],
        model_version_id=cast(str, _models()[0]["id"]),
        model_hash=cast(str, _models()[0]["model_hash"]),
        calibration_snapshot=calibration,
    )
    assert forecast["forecast_status"] == "forecasted"
    forecast_points = cast(list[dict[str, object]], forecast["forecast_points"])
    assert [point["period_start"] for point in forecast_points] == ["2026-01-09"]
    assert forecast_points[0]["prediction"] == "8"
    assert forecast_points[0]["prediction_interval"]["coverage_guarantee"] == (  # type: ignore[index]
        "not_established_for_time_series_dependence"
    )


def test_missing_actual_policy_can_report_or_block_the_evaluation() -> None:
    dataset = _dataset(["1", "2", "3", "4", "5", "6", None, "8", "9", "10", "11", "12"])
    report_protocol = _protocol(validation_periods=4, holdout_periods=2)
    report_result = _evaluate(dataset, report_protocol, stage="validation")
    assert report_result["evaluation_status"] == "partial"
    assert "missing_evaluation_actuals_excluded_and_reported" in report_result["reason_codes"]  # type: ignore[operator]

    block_protocol = _protocol(
        validation_periods=4,
        holdout_periods=2,
        missing_actual_policy="block_evaluation_if_missing_actuals",
    )
    blocked_result = _evaluate(dataset, block_protocol, stage="validation")
    assert blocked_result["evaluation_status"] == "not_evaluable"
    assert blocked_result["reason_codes"] == ["evaluation_actuals_missing_blocked"]
    assert blocked_result["prediction_points"] == []
    assert blocked_result["sample_summary"]["missing_actual_points"] == 1  # type: ignore[index]


def test_dataset_eligibility_reports_source_and_bounded_timeline_warnings() -> None:
    complete = _dataset(["1", "2", "3"])
    assessment = assess_forecast_dataset(complete)
    assert assessment["status"] == "eligible_with_warnings"
    assert assessment["eligibility_for_backtesting"] == "candidate"
    assert "synthetic_fixture_not_customer_or_accuracy_evidence" in assessment["reason_codes"]  # type: ignore[operator]

    long_gap = _dataset(["1", "2"], start=date(2000, 1, 1))
    # Widen the second observation beyond the maximum supported daily timeline.
    snapshot = long_gap.model_dump(mode="json")
    observations = cast(list[dict[str, object]], snapshot["observations"])
    observations[1]["period_start"] = (date(2000, 1, 1) + timedelta(days=10_000)).isoformat()
    long_gap = ForecastDatasetCreate.model_validate_json(json.dumps(snapshot))
    long_assessment = assess_forecast_dataset(long_gap)
    assert long_assessment["status"] == "not_evaluable"
    assert long_assessment["eligibility_for_backtesting"] == "blocked"
    assert "timeline_period_budget_exceeded" in long_assessment["reason_codes"]  # type: ignore[operator]

    result = _evaluate(long_gap, _protocol(), stage="validation")
    assert result["evaluation_status"] == "not_evaluable"
    assert result["reason_codes"] == ["timeline_period_budget_exceeded"]


def test_future_forecast_point_budget_is_checked_before_calculation() -> None:
    series_count = FORECAST_MAX_FORECAST_POINTS // 366 + 1
    dataset = _dataset(["1"], series_count=series_count)
    protocol = _protocol(
        validation_periods=1,
        holdout_periods=1,
        horizon_periods=366,
    )
    result = calculate_future_forecast(
        dataset_snapshot=dataset.model_dump(mode="json", exclude={"expected_version"}),
        protocol_definition=protocol.model_dump(mode="json", exclude={"expected_version"}),
        model_definition={"kind": "naive_last_value"},
        model_version_id="00000000-0000-4000-8000-000000000001",
        model_hash="b" * 64,
    )
    assert result["forecast_status"] == "not_evaluable"
    assert result["reason_codes"] == ["forecast_point_budget_exceeded"]
    assert result["forecast_points"] == []


def test_failed_evaluation_retains_a_valid_empty_comparison_shape() -> None:
    result = _failed_evaluation_result("validation", ValueError("internal detail is not exposed"))
    assert result["evaluation_status"] == "failed"
    assert result["prediction_points"] == []
    assert result["model_results"] == []
    assert model_comparison_summary(result)["models"] == []


def test_metrics_with_zero_denominators_are_undefined_not_filled() -> None:
    mape = _metric_result("mape", [(0, 2)], "USD")
    smape = _metric_result("smape", [(0, 0)], "USD")
    assert mape["status"] == "undefined"
    assert mape["reason_code"] == "zero_actual_denominator_present"
    assert smape["status"] == "undefined"
    assert smape["reason_code"] == "zero_actual_and_forecast_denominator_present"


@pytest.mark.parametrize(
    ("frequency", "bad_period"),
    [("weekly", "2026-01-04"), ("monthly", "2026-01-02")],
)
def test_calendar_contracts_require_aligned_period_starts(frequency: str, bad_period: str) -> None:
    dataset = _dataset(["1", "2"])
    snapshot = dataset.model_dump(mode="json")
    snapshot["frequency"] = frequency
    observations = cast(list[dict[str, object]], snapshot["observations"])
    observations[0]["period_start"] = bad_period
    with pytest.raises(ValueError):
        ForecastDatasetCreate.model_validate_json(json.dumps(snapshot))


def test_json_http_payload_mode_accepts_iso_dates_and_string_identifiers() -> None:
    dataset = _dataset(["1", "2"])
    payload = dataset.model_dump(mode="json")
    parsed = ForecastDatasetCreate.model_validate(payload)
    assert parsed.observations[0].period_start == date(2026, 1, 1)
    invalid_date_payload = {
        **payload,
        "observations": [{**payload["observations"][0], "period_start": 1_767_225_600}],
    }
    with pytest.raises(ValueError):
        ForecastDatasetCreate.model_validate(invalid_date_payload)

    evaluation = ForecastEvaluationCreate.model_validate(
        {
            "dataset_version_id": str(uuid4()),
            "protocol_version_id": str(uuid4()),
            "model_version_ids": [str(uuid4())],
            "stage": "validation",
        }
    )
    assert isinstance(evaluation.dataset_version_id, UUID)

    promotion_payload = {
        "evaluation_id": str(uuid4()),
        "model_version_id": str(uuid4()),
        "expected_evaluation_hash": "a" * 64,
        "expected_model_hash": "b" * 64,
        "decision": "approve",
        "rationale": "Independent reviewer confirms sufficient evidence.",
    }
    promotion = ForecastPromotionCreate.model_validate(promotion_payload)
    assert promotion.decision == "approve"
    with pytest.raises(ValueError, match="non-whitespace"):
        ForecastPromotionCreate.model_validate({**promotion_payload, "rationale": " " * 30})


def test_forecasting_and_promotion_are_default_disabled_and_production_blocked() -> None:
    settings = Settings(
        _env_file=None,
        app_secret_key="test-only-" + "x" * 48,
        app_env="test",
        database_url="postgresql+psycopg://bf_runtime:test@localhost/test_db",
    )
    assert settings.forecasting_enabled is False
    assert settings.forecast_promotion_enabled is False
    with pytest.raises(ApiError, match="disabled"):
        ensure_forecasting_enabled(settings)

    enabled = settings.model_copy(
        update={"forecasting_enabled": True, "forecast_promotion_enabled": True}
    )
    ensure_forecasting_enabled(enabled)
    ensure_forecast_promotion_enabled(enabled)
    production = enabled.model_copy(update={"app_env": "production"})
    with pytest.raises(ApiError, match="disabled"):
        ensure_forecasting_enabled(production)
    with pytest.raises(ApiError, match="disabled"):
        ensure_forecast_promotion_enabled(production)
