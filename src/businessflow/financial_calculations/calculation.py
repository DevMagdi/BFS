from __future__ import annotations

import hmac
from collections import defaultdict
from decimal import Decimal
from typing import Literal
from uuid import UUID

from businessflow.calculation_core import (
    CalculationDomainError,
    CalculationInputSnapshot,
    MechanicalSumFormulaDefinition,
    NumericObservation,
    RoundingPolicy,
    SourceReference,
    calculate_formula,
    decimal_text,
    parse_decimal_text,
    verify_replay,
)
from businessflow.calculation_core.engine import CalculationPrecisionError
from businessflow.company_data.quality import evaluate_freshness
from businessflow.finance_sources.commitments import (
    AMOUNT_COMMITMENT_KEY_VERSION,
    finance_amount_commitment,
)
from businessflow.financial_calculations.catalog import (
    FINANCIAL_CALCULATION_CATALOG_VERSION,
    FINANCIAL_CALCULATION_ENGINE_VERSION,
    SOURCE_GROUP_SUBTOTAL,
)
from businessflow.financial_calculations.contracts import (
    FinanceSourceEvidenceReference,
    FinancialCalculationInputSnapshot,
    FinancialCalculationOutput,
    FinancialCalculationReplayVerificationReport,
    FinancialCalculationTrace,
)
from businessflow.metrics.snapshot import content_hash


def financial_calculation_formula_hash() -> str:
    """Hash the exact BFS-owned M15 formula catalog entry, excluding caller data."""
    return content_hash(SOURCE_GROUP_SUBTOTAL.model_dump(mode="json"))


def financial_input_snapshot_hash(snapshot: FinancialCalculationInputSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json"))


def _mechanical_formula(input_keys: tuple[str, ...]) -> MechanicalSumFormulaDefinition:
    return MechanicalSumFormulaDefinition(
        formula_id="businessflow.mechanical.exact-sum",
        formula_version="1.0.0",
        input_keys=input_keys,
        output_key="exact-subtotal",
        output_label="Exact sum (mechanical only)",
        rounding_policy=RoundingPolicy(target="none", mode="half_even"),
    )


def _core_snapshot(
    snapshot: FinancialCalculationInputSnapshot,
    observations: tuple[NumericObservation, ...],
) -> CalculationInputSnapshot:
    return CalculationInputSnapshot(
        schema_version=1,
        snapshot_id=snapshot.snapshot_id,
        tenant_id=snapshot.tenant_id,
        company_id=snapshot.company_id,
        captured_at=snapshot.captured_at,
        observations=observations,
        assumptions=(),
    )


def _input_rows_by_number(snapshot: FinancialCalculationInputSnapshot):
    rows: dict[int, list[object]] = defaultdict(list)
    for item in snapshot.observations:
        rows[item.source_row_number].append(item)
    return rows


def _verify_amount_commitment(
    evidence: FinanceSourceEvidenceReference,
    amount: Decimal,
    snapshot: FinancialCalculationInputSnapshot,
    *,
    secret: bytes,
) -> bool:
    intake = snapshot.intake
    if (
        evidence.amount_commitment_hash is None
        or evidence.amount_commitment_key_version != AMOUNT_COMMITMENT_KEY_VERSION
        or evidence.reconciliation_group_hash is None
    ):
        return False
    expected = finance_amount_commitment(
        secret=secret,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        intake_id=intake.intake_id,
        source_key=intake.source_key,
        file_hash=intake.file_hash,
        source_row_number=evidence.source_row_number,
        source_row_hash=evidence.source_row_hash,
        source_record_identity_hash=evidence.source_record_identity_hash,
        reconciliation_group_hash=evidence.reconciliation_group_hash,
        contract_key=intake.contract_key,
        contract_version=intake.contract_version,
        contract_hash=intake.contract_hash,
        mapping_version=intake.mapping_version,
        mapping_hash=intake.mapping_hash,
        canonical_amount_text=decimal_text(amount),
    )
    return hmac.compare_digest(evidence.amount_commitment_hash, expected)


def _core_input(
    item: object,
    snapshot: FinancialCalculationInputSnapshot,
    *,
    currency_code: str,
) -> NumericObservation:
    # The caller has checked the strict model type and HMAC binding before this conversion.
    from businessflow.financial_calculations.contracts import FinancialAmountObservation

    if not isinstance(item, FinancialAmountObservation) or item.evidence is None:
        raise ValueError("verified financial source observation is unavailable")
    evidence = item.evidence
    source = SourceReference(
        schema_version=1,
        source_id=snapshot.intake.source_key,
        source_hash=snapshot.intake.file_hash,
        record_hash=evidence.source_row_hash,
        dataset_type=snapshot.intake.contract_key,
        mapping_version=snapshot.intake.mapping_version,
        record_locator=f"finance-source:{snapshot.intake.intake_id}:row:{item.source_row_number}",
        tenant_id=snapshot.tenant_id,
        company_id=snapshot.company_id,
    )
    if item.amount_text is None:
        raise ValueError("verified financial source amount is unavailable")
    return NumericObservation(
        schema_version=1,
        key=f"row-{item.source_row_number}",
        value=item.amount_text,
        currency_code=currency_code,
        source=source,
    )


def _abstained_core_trace(
    snapshot: FinancialCalculationInputSnapshot,
    input_keys: tuple[str, ...],
):
    formula = _mechanical_formula(input_keys or ("missing-source-evidence",))
    core_input = _core_snapshot(snapshot, ())
    return formula, core_input, calculate_formula(formula, core_input)


def _snapshot_reasons(
    snapshot: FinancialCalculationInputSnapshot,
    *,
    secret: bytes,
) -> tuple[tuple[str, ...], tuple[NumericObservation, ...], tuple[int, ...]]:
    reasons: set[str] = set()
    intake = snapshot.intake
    group = snapshot.group

    if group is None:
        reasons.add("source_group_not_found")
    else:
        if group.source_row_count < 1:
            reasons.add("source_group_empty")
        if group.source_row_count > 10_000:
            reasons.add("source_group_size_limit_exceeded")

    if snapshot.source_evidence_integrity_status != "verified":
        reasons.add("source_row_evidence_integrity_unverified")
    if intake.quality_status != "passed":
        reasons.add("source_quality_not_passed")
    freshness_at_calculation = evaluate_freshness(
        max_age_hours=intake.freshness_max_age_hours,
        source_as_of_at=intake.source_as_of_at,
        evaluated_at=snapshot.captured_at,
    )
    if intake.freshness_status != "fresh" or freshness_at_calculation != "fresh":
        reasons.add("source_not_fresh_at_calculation")

    by_row = _input_rows_by_number(snapshot)
    submitted_row_numbers = tuple(sorted(by_row))
    parsed_by_row: dict[int, Decimal] = {}
    valid_observations: list[NumericObservation] = []
    expected_group_hash = snapshot.requested_group_hash

    for row_number, items in by_row.items():
        if len(items) > 1:
            parsed_values: set[Decimal] = set()
            every_value_valid = True
            for item in items:
                amount_text = getattr(item, "amount_text", None)
                if amount_text is None:
                    every_value_valid = False
                    continue
                try:
                    parsed_values.add(parse_decimal_text(amount_text))
                except ValueError:
                    every_value_valid = False
            reasons.add(
                "conflicting_amounts_for_duplicate_row"
                if len(parsed_values) > 1
                else "duplicate_input_row"
            )
            if not every_value_valid:
                reasons.add("invalid_or_missing_amount")
            continue

        item = items[0]
        evidence = getattr(item, "evidence", None)
        amount_text = getattr(item, "amount_text", None)
        item_status = getattr(item, "status", "invalid")
        if item_status == "missing":
            reasons.add("amount_missing" if amount_text is None else "source_row_evidence_missing")
        elif item_status == "invalid":
            reasons.add("invalid_amount_or_source_evidence")
        elif item_status == "duplicate":
            reasons.add("duplicate_source_evidence")
        elif item_status == "conflicting":
            reasons.add("conflicting_amounts_for_duplicate_row")
        elif item_status != "valid":
            reasons.add("invalid_input_status")

        if evidence is None:
            reasons.add("source_row_evidence_missing")
            continue
        if evidence.source_row_number != row_number:
            reasons.add("source_row_evidence_mismatch")
            continue
        if evidence.row_status == "duplicate":
            reasons.add("duplicate_source_evidence")
        elif evidence.row_status != "valid":
            reasons.add("invalid_source_evidence")
        if (
            group is None
            or evidence.reconciliation_group_hash != expected_group_hash
            or group.reconciliation_group_hash != expected_group_hash
        ):
            reasons.add("incompatible_period_or_currency_evidence")
        if amount_text is None:
            reasons.add("amount_missing")
            continue
        try:
            amount = parse_decimal_text(amount_text)
        except ValueError:
            reasons.add("amount_invalid")
            continue
        parsed_by_row[row_number] = amount
        if not _verify_amount_commitment(evidence, amount, snapshot, secret=secret):
            reasons.add("amount_commitment_unverifiable")
            continue
        if item_status != "valid" or evidence.row_status != "valid":
            continue
        if group is None:
            continue
        try:
            valid_observations.append(
                _core_input(item, snapshot, currency_code=group.currency_code)
            )
        except (ValueError, TypeError):
            reasons.add("source_row_evidence_invalid")

    if group is not None:
        if len(by_row) != group.source_row_count:
            reasons.add("source_group_rows_incomplete")
        if len(parsed_by_row) != group.source_row_count:
            reasons.add("source_group_rows_incomplete")
        if len(valid_observations) != group.source_row_count:
            reasons.add("source_group_evidence_incomplete")
    if not by_row:
        reasons.add("source_group_rows_missing")

    ordered_observations = tuple(
        sorted(valid_observations, key=lambda item: int(item.key.removeprefix("row-")))
    )
    ordered_row_numbers = tuple(sorted(submitted_row_numbers))
    return tuple(sorted(reasons)), ordered_observations, ordered_row_numbers


def _result_hash(
    *,
    state: str,
    reason_codes: tuple[str, ...],
    output: FinancialCalculationOutput | None,
) -> str:
    return content_hash(
        {
            "state": state,
            "reason_codes": list(reason_codes),
            "output": output.model_dump(mode="json") if output is not None else None,
        }
    )


def calculate_financial_calculation(
    run_id: UUID,
    snapshot: FinancialCalculationInputSnapshot,
    *,
    identity_secret: bytes,
) -> FinancialCalculationTrace:
    """Build a single-group exact subtotal only after every M14 amount commitment verifies."""
    snapshot = FinancialCalculationInputSnapshot.model_validate(snapshot.model_dump(mode="python"))
    ordered_snapshot = snapshot.model_copy(
        update={
            "observations": tuple(
                sorted(
                    snapshot.observations,
                    key=lambda item: (
                        item.source_row_number,
                        item.amount_text or "",
                        str(item.evidence.row_evidence_id) if item.evidence is not None else "",
                    ),
                )
            )
        }
    )
    reason_codes, valid_observations, _observed_row_numbers = _snapshot_reasons(
        ordered_snapshot, secret=identity_secret
    )
    formula_keys: tuple[str, ...]
    if ordered_snapshot.group is not None and 0 < ordered_snapshot.group.source_row_count <= 10_000:
        formula_keys = tuple(
            f"row-{number}"
            for number in sorted({item.source_row_number for item in ordered_snapshot.observations})
        )
        if len(formula_keys) != ordered_snapshot.group.source_row_count:
            formula_keys = tuple(
                f"expected-row-{index + 1}"
                for index in range(ordered_snapshot.group.source_row_count)
            )
    else:
        formula_keys = ("source-group-evidence-required",)

    core_trace = None
    output: FinancialCalculationOutput | None = None
    state: Literal["calculated", "incomplete"] = "incomplete"
    final_reasons = reason_codes

    if reason_codes:
        formula, core_input, core_trace = _abstained_core_trace(ordered_snapshot, formula_keys)
    else:
        if ordered_snapshot.group is None:
            raise ValueError("complete financial calculation unexpectedly has no source group")
        formula = _mechanical_formula(tuple(item.key for item in valid_observations))
        core_input = _core_snapshot(ordered_snapshot, valid_observations)
        try:
            core_trace = calculate_formula(formula, core_input)
            if core_trace.state != "calculated" or core_trace.output is None:
                final_reasons = ("m11_core_calculation_abstained",)
            else:
                intake = ordered_snapshot.intake
                group = ordered_snapshot.group
                output = FinancialCalculationOutput(
                    schema_version=1,
                    tenant_id=ordered_snapshot.tenant_id,
                    company_id=ordered_snapshot.company_id,
                    key="m14_source_group_exact_subtotal",
                    label="M14 source-group exact subtotal (not accounting truth)",
                    value=core_trace.output.value,
                    currency_code=group.currency_code,
                    period_start=group.period_start,
                    period_end=group.period_end,
                    intake_id=intake.intake_id,
                    source_key=intake.source_key,
                    source_file_hash=intake.file_hash,
                    contract_key=intake.contract_key,
                    contract_version=intake.contract_version,
                    contract_hash=intake.contract_hash,
                    mapping_version=intake.mapping_version,
                    mapping_hash=intake.mapping_hash,
                    reconciliation_group_hash=group.reconciliation_group_hash,
                    source_row_numbers=tuple(
                        int(item.key.removeprefix("row-"))
                        for item in sorted(
                            valid_observations,
                            key=lambda item: int(item.key.removeprefix("row-")),
                        )
                    ),
                )
                state = "calculated"
        except (CalculationDomainError, CalculationPrecisionError, ValueError):
            final_reasons = ("decimal_calculation_unavailable",)
            formula, core_input, core_trace = _abstained_core_trace(ordered_snapshot, formula_keys)

    if state != "calculated":
        output = None
        if not final_reasons:
            final_reasons = ("calculation_incomplete",)
    else:
        final_reasons = ()

    input_hash = financial_input_snapshot_hash(ordered_snapshot)
    formula_hash = financial_calculation_formula_hash()
    result_hash = _result_hash(state=state, reason_codes=final_reasons, output=output)
    placeholder = FinancialCalculationTrace(
        schema_version=1,
        run_id=run_id,
        engine_version=FINANCIAL_CALCULATION_ENGINE_VERSION,
        catalog_version=FINANCIAL_CALCULATION_CATALOG_VERSION,
        formula=SOURCE_GROUP_SUBTOTAL,
        formula_hash=formula_hash,
        input_snapshot=ordered_snapshot,
        input_hash=input_hash,
        core_trace=core_trace,
        state=state,
        reason_codes=final_reasons,
        output=output,
        result_hash=result_hash,
        trace_hash="0" * 64,
    )
    trace_payload = placeholder.model_dump(mode="json", exclude={"trace_hash"})
    trace_hash = content_hash(trace_payload)
    return placeholder.model_copy(update={"trace_hash": trace_hash})


def _trace_integrity_valid(trace: FinancialCalculationTrace) -> bool:
    try:
        payload = trace.model_dump(mode="json", exclude={"trace_hash"})
        return content_hash(payload) == trace.trace_hash
    except (TypeError, ValueError):
        return False


def verify_financial_calculation_replay(
    recorded_trace: FinancialCalculationTrace,
    *,
    identity_secret: bytes,
) -> FinancialCalculationReplayVerificationReport:
    """Verify the outer M15 binding and the nested M11 calculation/replay trace."""
    replayed = calculate_financial_calculation(
        recorded_trace.run_id,
        recorded_trace.input_snapshot,
        identity_secret=identity_secret,
    )
    integrity_valid = _trace_integrity_valid(recorded_trace)
    reasons: list[str] = []
    if not integrity_valid:
        reasons.append("recorded_trace_integrity_invalid")
    if recorded_trace.engine_version != FINANCIAL_CALCULATION_ENGINE_VERSION:
        reasons.append("m15_engine_version_mismatch")
    if recorded_trace.catalog_version != FINANCIAL_CALCULATION_CATALOG_VERSION:
        reasons.append("m15_catalog_version_mismatch")
    if recorded_trace.formula_hash != financial_calculation_formula_hash():
        reasons.append("m15_formula_hash_mismatch")
    if recorded_trace.input_hash != financial_input_snapshot_hash(recorded_trace.input_snapshot):
        reasons.append("m15_input_hash_mismatch")
    if recorded_trace.result_hash != _result_hash(
        state=recorded_trace.state,
        reason_codes=recorded_trace.reason_codes,
        output=recorded_trace.output,
    ):
        reasons.append("m15_result_hash_mismatch")

    if recorded_trace.core_trace is None:
        core_replay_passed = False
        reasons.append("m11_core_trace_missing")
    else:
        core_report = verify_replay(
            recorded_trace.core_trace.formula,
            recorded_trace.core_trace.input_snapshot,
            recorded_trace.core_trace,
        )
        core_replay_passed = core_report.passed
        if not core_report.passed:
            reasons.extend(f"m11_{code}" for code in core_report.reason_codes)

    if replayed.trace_hash != recorded_trace.trace_hash:
        reasons.append("m15_replay_trace_hash_mismatch")
    return FinancialCalculationReplayVerificationReport(
        schema_version=1,
        passed=not reasons,
        reason_codes=tuple(reasons),
        run_id=recorded_trace.run_id,
        recorded_trace_hash=recorded_trace.trace_hash,
        replayed_trace_hash=replayed.trace_hash,
        recorded_input_hash=recorded_trace.input_hash,
        replayed_input_hash=replayed.input_hash,
        core_replay_passed=core_replay_passed,
        recorded_evidence_integrity_valid=integrity_valid,
    )
