from __future__ import annotations

from typing import cast

from businessflow.financial_assessments.catalog import (
    FINANCIAL_POLICY_CATALOG_STATUS,
    FINANCIAL_POLICY_CATALOG_VERSION,
    policy_catalog_payload,
)
from businessflow.metrics.snapshot import content_hash

RESULT_SCHEMA_VERSION = 1


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _observation_payload(financial_input: dict[str, object] | None) -> dict[str, object]:
    if financial_input is None:
        return {
            "commercial_amounts": [],
            "cost_observations": [],
            "effort_hours": None,
            "payment_terms": None,
            "known_risks": [],
        }
    return {
        "commercial_amounts": _list(financial_input.get("commercial_amounts")),
        "cost_observations": _list(financial_input.get("cost_observations")),
        "effort_hours": financial_input.get("effort_hours"),
        "payment_terms": financial_input.get("payment_terms"),
        "known_risks": _list(financial_input.get("known_risks")),
    }


def _currency_codes(observations: dict[str, object]) -> list[str]:
    currencies: set[str] = set()
    for collection_name in ("commercial_amounts", "cost_observations"):
        for item in _list(observations.get(collection_name)):
            currency = _object(item).get("currency")
            if isinstance(currency, str):
                currencies.add(currency)
    return sorted(currencies)


def _assumptions(
    financial_input: dict[str, object] | None,
) -> list[dict[str, object]]:
    if financial_input is None:
        return []
    result: list[dict[str, object]] = []
    for field_name in (
        "commercial_amounts",
        "cost_observations",
        "effort_hours",
        "payment_terms",
        "known_risks",
    ):
        raw = financial_input.get(field_name)
        if isinstance(raw, list):
            entries: list[object] = raw
        elif raw is not None:
            entries = [raw]
        else:
            entries = []
        for index, entry in enumerate(entries):
            fact = _object(entry)
            source = _object(fact.get("source"))
            assumption = _object(source.get("assumption"))
            if assumption:
                result.append(
                    {
                        "field": field_name,
                        "index": index,
                        "source_reference": source.get("source_reference"),
                        "rationale": assumption.get("rationale"),
                        "attributed_to_user_id": financial_input.get("created_by_user_id"),
                        "attributed_at": financial_input.get("created_at"),
                    }
                )
    return result


def evaluate_financial_input_snapshot(input_snapshot: dict[str, object]) -> dict[str, object]:
    """Return only directly supported observations; no financial formula is approved."""
    binding = _object(input_snapshot.get("financial_input"))
    financial_input = _object(binding.get("snapshot")) if binding else None
    observations = _observation_payload(financial_input)
    missing_inputs = ["approved_financial_decision_policy"]
    if financial_input is None:
        missing_inputs.append("financial_input_version")
    else:
        intake_binding = _object(input_snapshot.get("intake"))
        input_intake_version = binding.get("intake_version")
        current_intake_version = intake_binding.get("version")
        if input_intake_version != current_intake_version:
            missing_inputs.append("financial_input_version_for_current_intake_version")

    unprovided_observations = [
        name
        for name in (
            "commercial_amounts",
            "cost_observations",
            "effort_hours",
            "payment_terms",
            "known_risks",
        )
        if not observations[name]
    ]
    warnings = [
        "no_approved_financial_decision_policy",
        "no_financial_metrics_calculated",
    ]
    if financial_input is None:
        warnings.append("financial_inputs_not_provided; no amounts, costs, or effort were inferred")
    if "cost_observations" in unprovided_observations:
        warnings.append(
            "costs_not_provided; missing costs remain unknown and are never treated as zero"
        )
    if (
        financial_input is not None
        and "financial_input_version_for_current_intake_version" in missing_inputs
    ):
        warnings.append("financial_inputs_are_bound_to_a_prior_intake_version")
    if len(_currency_codes(observations)) > 1:
        warnings.append(
            "multiple_source_currencies_preserved; no FX conversion or cross-currency arithmetic"
        )

    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "financial_verdict": "incomplete",
        "policy_status": FINANCIAL_POLICY_CATALOG_STATUS,
        "policy_catalog": policy_catalog_payload(),
        "approved_policy": None,
        "calculated_metrics": [],
        "observations": observations,
        "currency_codes": _currency_codes(observations),
        "assumptions": _assumptions(financial_input),
        "missing_inputs": missing_inputs,
        "unprovided_observations": unprovided_observations,
        "warnings": warnings,
        "rounding_applied": False,
        "formula_catalog_version": FINANCIAL_POLICY_CATALOG_VERSION,
    }


def financial_result_hash(input_snapshot: dict[str, object]) -> str:
    return content_hash(evaluate_financial_input_snapshot(input_snapshot))


def financial_observation_summary(result_snapshot: dict[str, object]) -> dict[str, object]:
    value = result_snapshot.get("observations")
    return cast(dict[str, object], value) if isinstance(value, dict) else {}
