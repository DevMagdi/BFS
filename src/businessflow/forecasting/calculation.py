from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from businessflow.calculation_core.decimal import decimal_text, parse_decimal_text
from businessflow.forecasting.contracts import (
    ForecastDatasetCreate,
    ForecastEvaluationProtocolCreate,
    ForecastModelDefinition,
)
from businessflow.metrics.snapshot import content_hash

FORECAST_ENGINE_VERSION = "businessflow.forecasting-engine.v1"
FORECAST_SNAPSHOT_SCHEMA_VERSION = 1
FORECAST_OUTPUT_SCALE = 18
FORECAST_MAX_TIMELINE_PERIODS = 10_000
FORECAST_MAX_PANEL_CELLS = 100_000
FORECAST_MAX_EVALUATION_POINTS = 20_000
FORECAST_MAX_FORECAST_POINTS = 20_000
FORECAST_MAX_EVALUATION_BLOCKS = 256
FORECAST_MODEL_DEFINITION_ADAPTER: TypeAdapter[ForecastModelDefinition] = TypeAdapter(
    cast(Any, ForecastModelDefinition)
)
_OUTPUT_QUANTUM = Decimal(1).scaleb(-FORECAST_OUTPUT_SCALE)


@dataclass(frozen=True, slots=True)
class _Series:
    key: str
    dimensions: dict[str, str]
    values: dict[date, Decimal | None]
    missing_reasons: dict[date, str]
    observed_periods: tuple[date, ...]
    observed_values: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class _Prediction:
    value: Decimal | None
    reason_codes: tuple[str, ...]


def _next_period(value: date, frequency: str) -> date:
    if frequency == "daily":
        return value + timedelta(days=1)
    if frequency == "weekly":
        return value + timedelta(days=7)
    if frequency == "monthly":
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        return date(year, month, 1)
    raise ValueError("unsupported forecast frequency")


def _decimal_output(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("forecast output must be finite")
    with localcontext() as context:
        context.prec = 256
        quantized = value.quantize(_OUTPUT_QUANTUM, rounding=ROUND_HALF_EVEN)
    return decimal_text(quantized)


def _decimal_mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("cannot calculate a mean over an empty history")
    with localcontext() as context:
        context.prec = 256
        mean = sum(values, Decimal(0)) / Decimal(len(values))
        return mean.quantize(_OUTPUT_QUANTUM, rounding=ROUND_HALF_EVEN)


def _dataset_period_count(dataset: ForecastDatasetCreate) -> int:
    periods = [observation.period_start for observation in dataset.observations]
    first, last = min(periods), max(periods)
    if dataset.frequency == "daily":
        return (last - first).days + 1
    if dataset.frequency == "weekly":
        delta_days = (last - first).days
        if delta_days % 7:
            raise ValueError("weekly dataset periods must align to a common weekday")
        return delta_days // 7 + 1
    if dataset.frequency == "monthly":
        return (last.year - first.year) * 12 + last.month - first.month + 1
    raise ValueError("unsupported forecast frequency")


def assess_forecast_dataset(dataset: ForecastDatasetCreate) -> dict[str, object]:
    """Return bounded technical input diagnostics, not source verification or approval."""
    series_count = len({content_hash(dict(item.dimension_values)) for item in dataset.observations})
    period_count = _dataset_period_count(dataset)
    panel_cell_count = period_count * series_count
    observed_value_count = sum(item.value is not None for item in dataset.observations)
    missing_or_unobserved_count = max(0, panel_cell_count - observed_value_count)
    reasons: list[str] = []
    if period_count > FORECAST_MAX_TIMELINE_PERIODS:
        reasons.append("timeline_period_budget_exceeded")
    if panel_cell_count > FORECAST_MAX_PANEL_CELLS:
        reasons.append("dataset_panel_budget_exceeded")
    if observed_value_count == 0:
        reasons.append("no_observed_values")
    if missing_or_unobserved_count:
        reasons.append("missing_or_unobserved_periods_present")
    if dataset.data_origin == "synthetic_fixture":
        reasons.append("synthetic_fixture_not_customer_or_accuracy_evidence")
    else:
        reasons.append("caller_asserted_source_not_independently_verified")
    blocked = any(
        reason
        in {
            "timeline_period_budget_exceeded",
            "dataset_panel_budget_exceeded",
            "no_observed_values",
        }
        for reason in reasons
    )
    return {
        "status": (
            "not_evaluable" if blocked else "eligible_with_warnings" if reasons else "eligible"
        ),
        "scope": "technical_input_assessment_not_business_approval",
        "eligibility_for_backtesting": "blocked" if blocked else "candidate",
        "period_count": period_count,
        "series_count": series_count,
        "panel_cell_count": panel_cell_count,
        "observed_value_count": observed_value_count,
        "missing_or_unobserved_period_count": missing_or_unobserved_count,
        "reason_codes": reasons,
    }


def _dataset_series(dataset: ForecastDatasetCreate) -> tuple[list[date], list[_Series]]:
    grouped: dict[str, tuple[dict[str, str], dict[date, Decimal | None], dict[date, str]]] = {}
    period_dates: set[date] = set()
    for observation in dataset.observations:
        period = observation.period_start
        period_dates.add(period)
        dimensions = dict(observation.dimension_values)
        series_key = content_hash(dimensions)
        if series_key not in grouped:
            grouped[series_key] = (dimensions, {}, {})
        values = grouped[series_key][1]
        missing_reasons = grouped[series_key][2]
        if period in values:
            raise ValueError("dataset contains a duplicate period within one series")
        if observation.value is None:
            values[period] = None
            missing_reasons[period] = observation.missing_reason or "missing_value"
        else:
            values[period] = parse_decimal_text(observation.value)
    if not period_dates:
        raise ValueError("forecast dataset has no periods")
    ordered_observed_periods = sorted(period_dates)
    timeline: list[date] = []
    current = ordered_observed_periods[0]
    final = ordered_observed_periods[-1]
    while current <= final:
        timeline.append(current)
        if len(timeline) > FORECAST_MAX_TIMELINE_PERIODS:
            raise ValueError("forecast dataset period range exceeds the supported limit")
        if current == final:
            break
        current = _next_period(current, dataset.frequency)
    series: list[_Series] = []
    for key, (dimensions, values, reasons) in sorted(grouped.items()):
        observed = sorted((period, value) for period, value in values.items() if value is not None)
        series.append(
            _Series(
                key=key,
                dimensions=dimensions,
                values=values,
                missing_reasons=reasons,
                observed_periods=tuple(period for period, _value in observed),
                observed_values=tuple(value for _period, value in observed),
            )
        )
    return timeline, series


def _history_observation_count(series: _Series, timeline: list[date], block_start: int) -> int:
    if block_start <= 0:
        return 0
    return bisect_right(series.observed_periods, timeline[block_start - 1])


def _model_forecasts(
    *,
    series: _Series,
    timeline: list[date],
    block_start: int,
    block_end: int,
    definition: dict[str, object],
) -> dict[date, _Prediction]:
    kind = definition.get("kind")
    history_count = _history_observation_count(series, timeline, block_start)
    result: dict[date, _Prediction] = {}
    if kind == "naive_last_value":
        if not history_count:
            return {
                timeline[index]: _Prediction(None, ("insufficient_training_history",))
                for index in range(block_start, block_end)
            }
        if block_start == 0 or series.values.get(timeline[block_start - 1]) is None:
            return {
                timeline[index]: _Prediction(None, ("latest_training_period_missing",))
                for index in range(block_start, block_end)
            }
        last = series.observed_values[history_count - 1]
        missing_periods = block_start - history_count
        reasons = ("training_history_contains_missing_periods",) if missing_periods else ()
        return {
            timeline[index]: _Prediction(last, reasons) for index in range(block_start, block_end)
        }

    if kind == "seasonal_naive":
        season_length = definition.get("season_length")
        if isinstance(season_length, bool) or not isinstance(season_length, int):
            raise ValueError("seasonal-naive definition is invalid")
        if history_count < season_length:
            return {
                timeline[index]: _Prediction(None, ("insufficient_training_history",))
                for index in range(block_start, block_end)
            }
        for index in range(block_start, block_end):
            lag_index = index - season_length
            if lag_index < 0:
                result[timeline[index]] = _Prediction(None, ("seasonal_lag_outside_history",))
                continue
            lag_period = timeline[lag_index]
            if lag_index >= block_start:
                result[timeline[index]] = result.get(
                    lag_period,
                    _Prediction(None, ("seasonal_lag_prediction_missing",)),
                )
                continue
            lag_value = series.values.get(lag_period)
            result[timeline[index]] = (
                _Prediction(lag_value, ())
                if lag_value is not None
                else _Prediction(None, ("seasonal_lag_observation_missing",))
            )
        return result

    if kind == "moving_average":
        window_periods = definition.get("window_periods")
        if isinstance(window_periods, bool) or not isinstance(window_periods, int):
            raise ValueError("moving-average definition is invalid")
        for index in range(block_start, block_end):
            prior_values: list[Decimal] = []
            incomplete = False
            for prior_index in range(index - window_periods, index):
                if prior_index < 0:
                    incomplete = True
                    break
                prior_period = timeline[prior_index]
                prior_value = (
                    result[prior_period].value
                    if prior_index >= block_start and prior_period in result
                    else series.values.get(prior_period)
                )
                if prior_value is None:
                    incomplete = True
                    break
                prior_values.append(prior_value)
            if incomplete or len(prior_values) != window_periods:
                result[timeline[index]] = _Prediction(None, ("moving_average_window_incomplete",))
            else:
                result[timeline[index]] = _Prediction(_decimal_mean(prior_values), ())
        return result

    raise ValueError("unsupported forecast model definition")


def _nearest_rank(sorted_values: list[Decimal], quantile: Decimal) -> Decimal:
    if not sorted_values:
        raise ValueError("nearest-rank quantile requires values")
    with localcontext() as context:
        context.prec = 256
        rank = int(
            (quantile * Decimal(len(sorted_values))).to_integral_value(rounding=ROUND_CEILING)
        )
    return sorted_values[max(0, min(len(sorted_values) - 1, rank - 1))]


def _tukey_fences(values: list[Decimal], multiplier: Decimal) -> tuple[Decimal, Decimal] | None:
    if len(values) < 4:
        return None
    ordered = sorted(values)
    q1 = _nearest_rank(ordered, Decimal("0.25"))
    q3 = _nearest_rank(ordered, Decimal("0.75"))
    with localcontext() as context:
        context.prec = 256
        spread = q3 - q1
        return q1 - multiplier * spread, q3 + multiplier * spread


def _metric_result(
    metric: str,
    pairs: list[tuple[Decimal, Decimal]],
    unit: str,
) -> dict[str, object]:
    count = len(pairs)
    if not pairs:
        return {
            "metric": metric,
            "status": "not_evaluable",
            "reason_code": "no_common_observations",
            "value": None,
            "sample_count": 0,
            "unit": unit,
        }
    with localcontext() as context:
        context.prec = 256
        errors = [forecast - actual for actual, forecast in pairs]
        absolute_errors = [abs(error) for error in errors]
    if metric == "mae":
        value = _decimal_mean(absolute_errors)
        result_unit = unit
    elif metric == "rmse":
        with localcontext() as context:
            context.prec = 256
            squared_mean = sum((error * error for error in errors), Decimal(0)) / Decimal(count)
            value = squared_mean.sqrt().quantize(_OUTPUT_QUANTUM, rounding=ROUND_HALF_EVEN)
        result_unit = unit
    elif metric == "mape":
        if any(actual == 0 for actual, _forecast in pairs):
            return {
                "metric": metric,
                "status": "undefined",
                "reason_code": "zero_actual_denominator_present",
                "value": None,
                "sample_count": count,
                "unit": "percent",
            }
        with localcontext() as context:
            context.prec = 256
            value = _decimal_mean(
                [
                    Decimal(100) * error / abs(actual)
                    for (actual, _forecast), error in zip(pairs, absolute_errors, strict=True)
                ]
            )
        result_unit = "percent"
    elif metric == "smape":
        if any(actual == 0 and forecast == 0 for actual, forecast in pairs):
            return {
                "metric": metric,
                "status": "undefined",
                "reason_code": "zero_actual_and_forecast_denominator_present",
                "value": None,
                "sample_count": count,
                "unit": "percent",
            }
        with localcontext() as context:
            context.prec = 256
            values = [
                Decimal(200) * abs(forecast - actual) / (abs(actual) + abs(forecast))
                for actual, forecast in pairs
            ]
            value = _decimal_mean(values)
        result_unit = "percent"
    else:
        raise ValueError("unsupported evaluation metric")
    return {
        "metric": metric,
        "status": "computed",
        "reason_code": None,
        "value": _decimal_output(value),
        "sample_count": count,
        "unit": result_unit,
        "rounding": {
            "scale": FORECAST_OUTPUT_SCALE,
            "mode": "ROUND_HALF_EVEN",
            "purpose": "technical_metric_rendering_not_currency_rounding",
        },
    }


def _validation_residuals(
    calibration_snapshot: dict[str, object] | None,
    model_id: str,
) -> dict[str, list[Decimal]]:
    residuals: dict[str, list[Decimal]] = defaultdict(list)
    if calibration_snapshot is None:
        return residuals
    points = calibration_snapshot.get("prediction_points")
    if not isinstance(points, list):
        return residuals
    for point in points:
        if not isinstance(point, dict):
            continue
        series_key = point.get("series_key")
        actual_text = point.get("actual")
        predictions = point.get("model_predictions")
        if not isinstance(series_key, str) or not isinstance(actual_text, str):
            continue
        if not isinstance(predictions, list):
            continue
        prediction = next(
            (
                item
                for item in predictions
                if isinstance(item, dict) and item.get("model_version_id") == model_id
            ),
            None,
        )
        prediction_text = prediction.get("prediction") if isinstance(prediction, dict) else None
        if not isinstance(prediction_text, str):
            continue
        with localcontext() as context:
            context.prec = 256
            residual = parse_decimal_text(actual_text) - parse_decimal_text(prediction_text)
        residuals[series_key].append(abs(residual))
    return residuals


def _interval_for_prediction(
    prediction: Decimal | None,
    residuals: list[Decimal],
    interval_config: dict[str, object] | None,
) -> dict[str, object]:
    if prediction is None:
        return {
            "status": "not_available",
            "reason_code": "point_forecast_not_available",
            "lower": None,
            "upper": None,
        }
    if interval_config is None:
        return {
            "status": "not_configured",
            "reason_code": None,
            "lower": None,
            "upper": None,
        }
    minimum_count = interval_config.get("minimum_calibration_points")
    coverage_text = interval_config.get("coverage_level")
    if (
        isinstance(minimum_count, bool)
        or not isinstance(minimum_count, int)
        or not isinstance(coverage_text, str)
    ):
        raise ValueError("forecast interval configuration is invalid")
    if len(residuals) < minimum_count:
        return {
            "status": "insufficient_calibration",
            "reason_code": "minimum_calibration_points_not_met",
            "calibration_count": len(residuals),
            "minimum_calibration_points": minimum_count,
            "lower": None,
            "upper": None,
        }
    coverage = parse_decimal_text(coverage_text)
    width = _nearest_rank(sorted(residuals), coverage)
    with localcontext() as context:
        context.prec = 256
        lower, upper = prediction - width, prediction + width
    return {
        "status": "empirical_non_guaranteed",
        "method": "validation_absolute_error_nearest_rank",
        "coverage_level": coverage_text,
        "calibration_count": len(residuals),
        "coverage_guarantee": "not_established_for_time_series_dependence",
        "lower": _decimal_output(lower),
        "upper": _decimal_output(upper),
    }


def _model_minimum_history(definition: dict[str, object]) -> int:
    kind = definition.get("kind")
    if kind == "naive_last_value":
        return 1
    if kind == "seasonal_naive":
        value = definition.get("season_length")
    elif kind == "moving_average":
        value = definition.get("window_periods")
    else:
        raise ValueError("unsupported forecast model definition")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("forecast model minimum history is invalid")
    return value


def _no_evaluation_result(
    *,
    stage: str,
    reason_code: str,
    dataset_origin: str,
    target_key: str,
    frequency: str,
    dataset_eligibility: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "snapshot_schema_version": FORECAST_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": FORECAST_ENGINE_VERSION,
        "stage": stage,
        "evaluation_status": "not_evaluable",
        "reason_codes": [reason_code],
        "target_key": target_key,
        "frequency": frequency,
        "prediction_points": [],
        "model_results": [],
        "comparison": {
            "selection": "none",
            "recommendation": "none",
            "ranking_applied": False,
            "basis": "common_complete_cases_only",
        },
        "governance_status": {
            "dataset_origin": dataset_origin,
            "source_authority": "not_established",
            "company_policy_approval": "not_established",
            "forecast_accuracy_claim": "not_asserted",
            "production_customer_data_authorization": "not_authorized",
        },
    }
    if dataset_eligibility is not None:
        result["dataset_eligibility"] = dataset_eligibility
    return result


def _no_forecast_result(
    *,
    reason_code: str,
    dataset_origin: str,
    dataset_eligibility: dict[str, object],
) -> dict[str, object]:
    return {
        "snapshot_schema_version": FORECAST_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": FORECAST_ENGINE_VERSION,
        "forecast_status": "not_evaluable",
        "reason_codes": [reason_code],
        "forecast_points": [],
        "dataset_eligibility": dataset_eligibility,
        "governance_status": {
            "dataset_origin": dataset_origin,
            "source_authority": "not_established",
            "company_policy_approval": "not_established",
            "forecast_accuracy_claim": "not_asserted",
            "production_customer_data_authorization": "not_authorized",
            "synthetic_customer_validation": "not_claimed",
        },
    }


def calculate_forecast_evaluation(
    *,
    dataset_snapshot: dict[str, object],
    protocol_definition: dict[str, object],
    model_versions: list[dict[str, object]],
    stage: str,
    calibration_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    """Evaluate deterministic baselines on chronological rolling-origin windows.

    Every prediction in a block sees only observations strictly before that block. Missing values
    are never imputed. The holdout is disjoint from training/validation and may be consumed only
    once for a dataset/protocol pair by the persistence service.
    """
    dataset = ForecastDatasetCreate.model_validate_json(json.dumps(dataset_snapshot))
    protocol = ForecastEvaluationProtocolCreate.model_validate(protocol_definition)
    if stage not in {"validation", "holdout"}:
        raise ValueError("evaluation stage is invalid")
    if dataset.target_key != protocol.target_key or dataset.frequency != protocol.frequency:
        raise ValueError("dataset and protocol target/frequency do not match")
    dataset_eligibility = assess_forecast_dataset(dataset)
    if dataset_eligibility["eligibility_for_backtesting"] == "blocked":
        codes = cast(list[str], dataset_eligibility["reason_codes"])
        reason_code = next(
            (
                code
                for code in codes
                if code
                in {
                    "timeline_period_budget_exceeded",
                    "dataset_panel_budget_exceeded",
                    "no_observed_values",
                }
            ),
            "dataset_not_eligible_for_backtesting",
        )
        return _no_evaluation_result(
            stage=stage,
            reason_code=reason_code,
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
            dataset_eligibility=dataset_eligibility,
        )
    timeline, series = _dataset_series(dataset)
    if len(timeline) * len(series) > FORECAST_MAX_PANEL_CELLS:
        return _no_evaluation_result(
            stage=stage,
            reason_code="dataset_panel_budget_exceeded",
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
        )
    validation_start = len(timeline) - protocol.validation_periods - protocol.holdout_periods
    holdout_start = len(timeline) - protocol.holdout_periods
    if validation_start < 1:
        return _no_evaluation_result(
            stage=stage,
            reason_code="insufficient_history_for_chronological_splits",
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
        )
    if stage == "validation":
        evaluation_start, evaluation_end = validation_start, holdout_start
    else:
        evaluation_start, evaluation_end = holdout_start, len(timeline)
    if evaluation_start >= evaluation_end:
        return _no_evaluation_result(
            stage=stage,
            reason_code="evaluation_window_empty",
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
            dataset_eligibility=dataset_eligibility,
        )
    expected_count = (evaluation_end - evaluation_start) * len(series)
    window_missing_actual_count = sum(
        series_item.values.get(timeline[index]) is None
        for series_item in series
        for index in range(evaluation_start, evaluation_end)
    )
    if (
        protocol.missing_actual_policy == "block_evaluation_if_missing_actuals"
        and window_missing_actual_count
    ):
        result = _no_evaluation_result(
            stage=stage,
            reason_code="evaluation_actuals_missing_blocked",
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
            dataset_eligibility=dataset_eligibility,
        )
        result["sample_summary"] = {
            "series_count": len(series),
            "expected_evaluation_points": expected_count,
            "observed_actual_points": expected_count - window_missing_actual_count,
            "missing_actual_points": window_missing_actual_count,
            "missing_actual_policy": protocol.missing_actual_policy,
        }
        return result

    normalized_models: list[dict[str, object]] = []
    for model_ref in model_versions:
        model_id = model_ref.get("id")
        definition_raw = model_ref.get("definition")
        if not isinstance(model_id, str) or not isinstance(definition_raw, dict):
            raise ValueError("forecast model reference is invalid")
        definition = FORECAST_MODEL_DEFINITION_ADAPTER.validate_python(definition_raw, strict=True)
        normalized_models.append(
            {
                "id": model_id,
                "model_key": model_ref.get("model_key"),
                "version": model_ref.get("version"),
                "model_hash": model_ref.get("model_hash"),
                "definition": definition.model_dump(mode="json"),
                "minimum_training_observations": _model_minimum_history(
                    definition.model_dump(mode="json")
                ),
            }
        )
    if not normalized_models:
        raise ValueError("at least one forecast model is required")

    expected_period_count = evaluation_end - evaluation_start
    expected_count = expected_period_count * len(series)
    block_count = (
        1
        if stage == "holdout"
        else (expected_period_count + protocol.horizon_periods - 1) // protocol.horizon_periods
    )
    if expected_count > FORECAST_MAX_EVALUATION_POINTS:
        return _no_evaluation_result(
            stage=stage,
            reason_code="evaluation_point_budget_exceeded",
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
        )
    if block_count > FORECAST_MAX_EVALUATION_BLOCKS:
        return _no_evaluation_result(
            stage=stage,
            reason_code="evaluation_block_budget_exceeded",
            dataset_origin=dataset.data_origin,
            target_key=dataset.target_key,
            frequency=dataset.frequency,
        )
    if len(normalized_models) > 8:
        raise ValueError("forecast evaluation exceeds the eight-model limit")
    model_ids = [cast(str, model["id"]) for model in normalized_models]
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("forecast evaluation contains duplicate model identifiers")

    prediction_points: list[dict[str, object]] = []
    missing_actual_count = 0
    missing_prediction_count = 0
    calibration_residuals = {
        model_id: _validation_residuals(calibration_snapshot, model_id) for model_id in model_ids
    }
    interval_config = (
        protocol.interval.model_dump(mode="json")
        if protocol.interval is not None and stage == "holdout"
        else None
    )
    if stage == "holdout":
        evaluation_blocks = [(evaluation_start, evaluation_end)]
    else:
        evaluation_blocks = [
            (block_start, min(evaluation_end, block_start + protocol.horizon_periods))
            for block_start in range(evaluation_start, evaluation_end, protocol.horizon_periods)
        ]
    for block_start, block_end in evaluation_blocks:
        window_predictions: dict[str, dict[str, dict[date, _Prediction]]] = {}
        for model_record in normalized_models:
            model_id = cast(str, model_record["id"])
            predictions_by_series: dict[str, dict[date, _Prediction]] = {}
            for series_item in series:
                predictions_by_series[series_item.key] = _model_forecasts(
                    series=series_item,
                    timeline=timeline,
                    block_start=block_start,
                    block_end=block_end,
                    definition=cast(dict[str, object], model_record["definition"]),
                )
            window_predictions[model_id] = predictions_by_series
        for series_item in series:
            training_observation_count = _history_observation_count(
                series_item, timeline, block_start
            )
            training_values = (
                list(series_item.observed_values[:training_observation_count])
                if protocol.outlier_method == "tukey_iqr_flag_only"
                else []
            )
            multiplier = (
                parse_decimal_text(protocol.outlier_iqr_multiplier)
                if protocol.outlier_iqr_multiplier is not None
                else Decimal(0)
            )
            fences = (
                _tukey_fences(training_values, multiplier)
                if protocol.outlier_method == "tukey_iqr_flag_only"
                else None
            )
            for index in range(block_start, block_end):
                period = timeline[index]
                actual = series_item.values.get(period)
                actual_text = _decimal_output(actual) if actual is not None else None
                if actual is None:
                    missing_actual_count += 1
                model_predictions: list[dict[str, object]] = []
                for model_record in normalized_models:
                    model_id = cast(str, model_record["id"])
                    model_prediction = window_predictions[model_id][series_item.key].get(
                        period, _Prediction(None, ("forecast_not_generated",))
                    )
                    prediction_text = (
                        _decimal_output(model_prediction.value)
                        if model_prediction.value is not None
                        else None
                    )
                    if model_prediction.value is None:
                        missing_prediction_count += 1
                    residuals = calibration_residuals[model_id].get(series_item.key, [])
                    interval = _interval_for_prediction(
                        model_prediction.value,
                        residuals,
                        interval_config,
                    )
                    model_predictions.append(
                        {
                            "model_version_id": model_id,
                            "model_hash": model_record["model_hash"],
                            "prediction": prediction_text,
                            "reason_codes": list(model_prediction.reason_codes),
                            "prediction_interval": interval,
                        }
                    )
                outlier_status = "not_assessed"
                if protocol.outlier_method == "tukey_iqr_flag_only":
                    if fences is None:
                        outlier_status = "insufficient_training_history"
                    elif actual is None:
                        outlier_status = "not_assessed_missing_actual"
                    else:
                        outlier_status = (
                            "flagged" if actual < fences[0] or actual > fences[1] else "not_flagged"
                        )
                prediction_points.append(
                    {
                        "period_start": period.isoformat(),
                        "series_key": series_item.key,
                        "dimension_values": series_item.dimensions,
                        "actual": actual_text,
                        "actual_status": "observed" if actual is not None else "missing",
                        "actual_missing_reason": (
                            None
                            if actual is not None
                            else series_item.missing_reasons.get(
                                period, "no_observation_for_period"
                            )
                        ),
                        "outlier_status": outlier_status,
                        "training_observation_count": training_observation_count,
                        "training_missing_period_count": block_start - training_observation_count,
                        "window_origin_period": timeline[block_start].isoformat(),
                        "model_predictions": model_predictions,
                    }
                )

    common_points = [
        point
        for point in prediction_points
        if isinstance(point.get("actual"), str)
        and isinstance(point.get("model_predictions"), list)
        and all(
            isinstance(prediction, dict) and isinstance(prediction.get("prediction"), str)
            for prediction in cast(list[object], point["model_predictions"])
        )
    ]
    common_by_model: dict[str, list[tuple[Decimal, Decimal]]] = {
        cast(str, model["id"]): [] for model in normalized_models
    }
    for point in common_points:
        actual_text = cast(str, point["actual"])
        actual = parse_decimal_text(actual_text)
        for prediction_record in cast(list[dict[str, object]], point["model_predictions"]):
            model_id = cast(str, prediction_record["model_version_id"])
            predicted = parse_decimal_text(cast(str, prediction_record["prediction"]))
            common_by_model[model_id].append((actual, predicted))
    model_results: list[dict[str, object]] = []
    for model in normalized_models:
        model_id = cast(str, model["id"])
        pairs = common_by_model[model_id]
        model_results.append(
            {
                "model_version_id": model_id,
                "model_key": model["model_key"],
                "model_version": model["version"],
                "model_hash": model["model_hash"],
                "minimum_training_observations": model["minimum_training_observations"],
                "common_sample_count": len(pairs),
                "metrics": [
                    _metric_result(metric, pairs, dataset.unit) for metric in protocol.metrics
                ],
            }
        )
    reason_codes: list[str] = []
    if missing_actual_count:
        reason_codes.append("missing_evaluation_actuals_excluded_and_reported")
    if missing_prediction_count:
        reason_codes.append("unavailable_model_predictions_excluded_from_common_sample")
    if not common_points:
        reason_codes.append("no_common_complete_evaluation_observations")
    if dataset.data_origin == "synthetic_fixture":
        reason_codes.append("synthetic_fixture_not_customer_or_accuracy_evidence")
    status = "evaluated"
    if not common_points:
        status = "not_evaluable"
    elif missing_actual_count or missing_prediction_count or len(common_points) != expected_count:
        status = "partial"
    validation_id = (
        calibration_snapshot.get("evaluation_id") if calibration_snapshot is not None else None
    )
    return {
        "snapshot_schema_version": FORECAST_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": FORECAST_ENGINE_VERSION,
        "stage": stage,
        "evaluation_status": status,
        "reason_codes": reason_codes,
        "dataset_eligibility": dataset_eligibility,
        "target": {
            "key": dataset.target_key,
            "label": dataset.target_label,
            "unit": dataset.unit,
            "currency_code": dataset.currency_code,
        },
        "frequency": dataset.frequency,
        "split": {
            "initial_training_end_period": timeline[validation_start - 1].isoformat(),
            "validation_start_period": timeline[validation_start].isoformat(),
            "validation_end_period": timeline[holdout_start - 1].isoformat(),
            "holdout_start_period": timeline[holdout_start].isoformat(),
            "holdout_end_period": timeline[-1].isoformat(),
            "evaluation_start_period": timeline[evaluation_start].isoformat(),
            "evaluation_end_period": timeline[evaluation_end - 1].isoformat(),
            "first_window_fit_history_end_period": timeline[evaluation_start - 1].isoformat(),
            "validation_periods": protocol.validation_periods,
            "holdout_periods": protocol.holdout_periods,
            "validation_roll_origin_horizon_periods": protocol.horizon_periods,
            "holdout_forecast_horizon_periods": protocol.holdout_periods,
            "missing_actual_policy": protocol.missing_actual_policy,
            "method": "chronological_expanding_window_rolling_origin",
            "random_split_used": False,
            "windows_chronological_and_disjoint": True,
            "holdout_uses_one_fixed_forecast_origin": stage == "holdout",
            "validation_history_used_when_scoring_holdout": stage == "holdout",
            "holdout_used_for_fitting_or_calibration": False,
        },
        "sample_summary": {
            "series_count": len(series),
            "expected_evaluation_points": expected_count,
            "common_scored_points": len(common_points),
            "missing_actual_points": missing_actual_count,
            "unavailable_model_predictions": missing_prediction_count,
            "missing_actual_policy": protocol.missing_actual_policy,
            "scoring_population": "intersection_of_observed_actuals_and_all_model_predictions",
        },
        "calibration_evaluation_id": validation_id,
        "prediction_points": prediction_points,
        "model_results": model_results,
        "comparison": {
            "selection": "none",
            "recommendation": "none",
            "ranking_applied": False,
            "basis": "same_common_complete_cases_for_every_model",
        },
        "numeric_contract": {
            "input_representation": "plain_base_10_decimal_text",
            "forecast_and_metric_rendering_scale": FORECAST_OUTPUT_SCALE,
            "rounding_mode": "ROUND_HALF_EVEN",
            "rounding_purpose": "technical_forecast_rendering_not_currency_rounding",
            "currency_conversion": "none",
            "missing_value_behavior": "never_impute_or_default_to_zero",
        },
        "governance_status": {
            "dataset_origin": dataset.data_origin,
            "source_authority": "not_established",
            "company_policy_approval": "not_established",
            "forecast_accuracy_claim": "not_asserted",
            "production_customer_data_authorization": "not_authorized",
            "synthetic_customer_validation": "not_claimed",
        },
    }


def calculate_future_forecast(
    *,
    dataset_snapshot: dict[str, object],
    protocol_definition: dict[str, object],
    model_definition: dict[str, object],
    model_version_id: str,
    model_hash: str,
    calibration_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    dataset = ForecastDatasetCreate.model_validate_json(json.dumps(dataset_snapshot))
    protocol = ForecastEvaluationProtocolCreate.model_validate(protocol_definition)
    definition = FORECAST_MODEL_DEFINITION_ADAPTER.validate_python(
        model_definition, strict=True
    ).model_dump(mode="json")
    if dataset.target_key != protocol.target_key or dataset.frequency != protocol.frequency:
        raise ValueError("dataset and protocol target/frequency do not match")
    dataset_eligibility = assess_forecast_dataset(dataset)
    if dataset_eligibility["eligibility_for_backtesting"] == "blocked":
        codes = cast(list[str], dataset_eligibility["reason_codes"])
        reason_code = next(
            (
                code
                for code in codes
                if code
                in {
                    "timeline_period_budget_exceeded",
                    "dataset_panel_budget_exceeded",
                    "no_observed_values",
                }
            ),
            "dataset_not_eligible_for_forecasting",
        )
        return _no_forecast_result(
            reason_code=reason_code,
            dataset_origin=dataset.data_origin,
            dataset_eligibility=dataset_eligibility,
        )
    timeline, series = _dataset_series(dataset)
    if not timeline:
        return _no_forecast_result(
            reason_code="history_empty",
            dataset_origin=dataset.data_origin,
            dataset_eligibility=dataset_eligibility,
        )
    if len(timeline) * len(series) > FORECAST_MAX_PANEL_CELLS:
        return _no_forecast_result(
            reason_code="dataset_panel_budget_exceeded",
            dataset_origin=dataset.data_origin,
            dataset_eligibility=dataset_eligibility,
        )
    if protocol.horizon_periods * len(series) > FORECAST_MAX_FORECAST_POINTS:
        return _no_forecast_result(
            reason_code="forecast_point_budget_exceeded",
            dataset_origin=dataset.data_origin,
            dataset_eligibility=dataset_eligibility,
        )
    future_dates: list[date] = []
    try:
        current = _next_period(timeline[-1], dataset.frequency)
        for index in range(protocol.horizon_periods):
            future_dates.append(current)
            if index + 1 < protocol.horizon_periods:
                current = _next_period(current, dataset.frequency)
    except (OverflowError, ValueError) as exc:
        raise ValueError("future forecast dates exceed the supported calendar") from exc
    combined_timeline = [*timeline, *future_dates]
    start = len(timeline)
    end = len(combined_timeline)
    points: list[dict[str, object]] = []
    missing_predictions = 0
    calibration_residuals = _validation_residuals(calibration_snapshot, model_version_id)
    for item in series:
        predictions = _model_forecasts(
            series=item,
            timeline=combined_timeline,
            block_start=start,
            block_end=end,
            definition=definition,
        )
        for period in future_dates:
            prediction = predictions.get(period, _Prediction(None, ("forecast_not_generated",)))
            residuals = calibration_residuals.get(item.key, [])
            interval_config = (
                protocol.interval.model_dump(mode="json") if protocol.interval is not None else None
            )
            interval = _interval_for_prediction(
                prediction.value, residuals, cast(dict[str, object] | None, interval_config)
            )
            if prediction.value is None:
                missing_predictions += 1
            points.append(
                {
                    "period_start": period.isoformat(),
                    "series_key": item.key,
                    "dimension_values": item.dimensions,
                    "prediction": (
                        _decimal_output(prediction.value) if prediction.value is not None else None
                    ),
                    "reason_codes": list(prediction.reason_codes),
                    "prediction_interval": interval,
                    "training_observation_count": _history_observation_count(
                        item, timeline, len(timeline)
                    ),
                    "source_freshness": "not_assessed",
                }
            )
    status = "partial" if missing_predictions else "forecasted"
    reason_codes = ["source_freshness_not_assessed"]
    if dataset.data_origin == "synthetic_fixture":
        reason_codes.append("synthetic_fixture_not_customer_or_accuracy_evidence")
    if missing_predictions:
        reason_codes.append("some_series_or_horizons_not_forecastable")
    return {
        "snapshot_schema_version": FORECAST_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": FORECAST_ENGINE_VERSION,
        "forecast_status": status,
        "reason_codes": reason_codes,
        "dataset_eligibility": dataset_eligibility,
        "target": {
            "key": dataset.target_key,
            "label": dataset.target_label,
            "unit": dataset.unit,
            "currency_code": dataset.currency_code,
        },
        "frequency": dataset.frequency,
        "forecast_horizon_periods": protocol.horizon_periods,
        "history_start_period": timeline[0].isoformat(),
        "history_end_period": timeline[-1].isoformat(),
        "forecast_start_period": future_dates[0].isoformat(),
        "forecast_end_period": future_dates[-1].isoformat(),
        "model_version_id": model_version_id,
        "model_hash": model_hash,
        "forecast_points": points,
        "numeric_contract": {
            "input_representation": "plain_base_10_decimal_text",
            "forecast_rendering_scale": FORECAST_OUTPUT_SCALE,
            "rounding_mode": "ROUND_HALF_EVEN",
            "rounding_purpose": "technical_forecast_rendering_not_currency_rounding",
            "currency_conversion": "none",
            "missing_value_behavior": "never_impute_or_default_to_zero",
        },
        "governance_status": {
            "dataset_origin": dataset.data_origin,
            "source_authority": "not_established",
            "company_policy_approval": "not_established",
            "forecast_accuracy_claim": "not_asserted",
            "production_customer_data_authorization": "not_authorized",
            "synthetic_customer_validation": "not_claimed",
        },
    }


def model_comparison_summary(evaluation_snapshot: dict[str, object]) -> dict[str, object]:
    """Return a non-ranking summary; it deliberately never chooses a champion."""
    rows = evaluation_snapshot.get("model_results")
    if not isinstance(rows, list):
        raise ValueError("evaluation model results are invalid")
    return {
        "evaluation_status": evaluation_snapshot.get("evaluation_status"),
        "comparison_basis": (
            "All models use the same dataset/protocol and common complete-case scoring population."
        ),
        "models": rows,
        "selection": "none",
        "recommendation": "none",
        "ranking_applied": False,
    }


def verify_snapshot_hash(snapshot: dict[str, object], expected_hash: str) -> bool:
    return content_hash(snapshot) == expected_hash


def validate_replay_snapshot(
    dataset_snapshot: dict[str, object],
    protocol_definition: dict[str, object],
    model_versions: list[dict[str, object]],
) -> None:
    try:
        dataset = ForecastDatasetCreate.model_validate_json(json.dumps(dataset_snapshot))
        protocol = ForecastEvaluationProtocolCreate.model_validate(protocol_definition, strict=True)
        if dataset.target_key != protocol.target_key or dataset.frequency != protocol.frequency:
            raise ValueError("dataset/protocol identity mismatch")
        if not model_versions:
            raise ValueError("no models are referenced")
        for model in model_versions:
            FORECAST_MODEL_DEFINITION_ADAPTER.validate_python(model.get("definition"), strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("forecast replay snapshot is invalid") from exc
