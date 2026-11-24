from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    parse_output_decimal_text,
)
from businessflow.company_data.quality import evaluate_freshness
from businessflow.direct_cash_forecasts.catalog import (
    DIRECT_CASH_FORECAST_HORIZON_WEEKS,
    DIRECT_CASH_FORECAST_MAX_GROUP_ROWS,
    DIRECT_CASH_FORECAST_WEEK_DAYS,
)
from businessflow.direct_cash_forecasts.contracts import (
    CashFlowSubtotal,
    CashForecastWeek,
    CashItemDisposition,
    CashItemDispositionStatus,
    DirectCashForecastInputItem,
    DirectCashForecastInputSnapshot,
    DirectCashForecastResultSnapshot,
)
from businessflow.metrics.snapshot import content_hash

FlowDirection = Literal["receipt", "disbursement"]
BucketKey = tuple[int, FlowDirection, Literal["actual", "forecast"]]


@dataclass(slots=True)
class _Bucket:
    amounts: list[Decimal] = field(default_factory=list)
    source_rows: set[int] = field(default_factory=set)
    unresolved_rows: set[int] = field(default_factory=set)
    reasons: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _MovementAssessment:
    item: DirectCashForecastInputItem
    amount: Decimal | None
    status: CashItemDispositionStatus
    reason_codes: tuple[str, ...]
    week_index: int | None
    blocking: bool


def direct_cash_input_snapshot_hash(snapshot: DirectCashForecastInputSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json"))


def direct_cash_result_snapshot_hash(snapshot: DirectCashForecastResultSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json"))


def _evidence_is_valid(
    item: DirectCashForecastInputItem, snapshot: DirectCashForecastInputSnapshot
) -> bool:
    evidence = item.evidence
    group = snapshot.group
    return (
        item.amount_commitment_status == "verified"
        and not item.issue_codes
        and evidence is not None
        and evidence.source_row_number == item.source_row_number
        and evidence.row_status == "valid"
        and evidence.reconciliation_group_hash == snapshot.requested_group_hash
        and group is not None
        and group.reconciliation_group_hash == snapshot.requested_group_hash
    )


def _amount_is_usable(
    item: DirectCashForecastInputItem,
    amounts: dict[str, Decimal],
    snapshot: DirectCashForecastInputSnapshot,
) -> Decimal | None:
    if not _evidence_is_valid(item, snapshot):
        return None
    amount = amounts.get(item.input_id)
    return amount if isinstance(amount, Decimal) and amount.is_finite() else None


def _base_movement_is_supersedable(
    item: DirectCashForecastInputItem,
    amounts: dict[str, Decimal],
    snapshot: DirectCashForecastInputSnapshot,
    request_counts: Counter[int],
) -> bool:
    amount = _amount_is_usable(item, amounts, snapshot)
    return (
        item.origin == "movement"
        and request_counts[item.source_row_number] == 1
        and item.cash_flow_type in {"receipt", "disbursement"}
        and item.basis == "actual"
        and item.cash_date is not None
        and amount is not None
        and amount >= 0
    )


def _resolve_supersession(
    movements: tuple[DirectCashForecastInputItem, ...],
    amounts: dict[str, Decimal],
    snapshot: DirectCashForecastInputSnapshot,
    request_counts: Counter[int],
) -> tuple[set[int], set[int], set[str]]:
    """Return valid superseded rows, unresolved rows, and deterministic reason codes."""
    by_row: dict[int, list[DirectCashForecastInputItem]] = defaultdict(list)
    for item in movements:
        if item.origin == "movement":
            by_row[item.source_row_number].append(item)

    candidates: list[tuple[DirectCashForecastInputItem, DirectCashForecastInputItem]] = []
    invalid_rows: set[int] = set()
    reasons: set[str] = set()
    for replacement in movements:
        old_number = replacement.supersedes_source_row_number
        if old_number is None:
            continue
        old_items = by_row.get(old_number, [])
        valid = (
            len(old_items) == 1
            and _base_movement_is_supersedable(replacement, amounts, snapshot, request_counts)
            and _base_movement_is_supersedable(old_items[0], amounts, snapshot, request_counts)
            and replacement.cash_flow_type == old_items[0].cash_flow_type
        )
        if valid:
            candidates.append((replacement, old_items[0]))
        else:
            invalid_rows.add(replacement.source_row_number)
            invalid_rows.add(old_number)
            reasons.add("supersession_unverifiable")

    targets: dict[int, list[DirectCashForecastInputItem]] = defaultdict(list)
    for replacement, old in candidates:
        targets[old.source_row_number].append(replacement)
    for old_number, replacements in targets.items():
        if len(replacements) > 1:
            invalid_rows.add(old_number)
            invalid_rows.update(item.source_row_number for item in replacements)
            reasons.add("supersession_conflict")

    # Edges point from a replacement to the row it supersedes. Detect all cycles before accepting
    # any edge in that cycle; no row-order or timestamp inference is used.
    graph = {
        replacement.source_row_number: old.source_row_number
        for replacement, old in candidates
        if len(targets[old.source_row_number]) == 1
    }
    cycle_nodes: set[int] = set()
    for start in graph:
        path: list[int] = []
        seen_at: dict[int, int] = {}
        current = start
        while current in graph:
            if current in seen_at:
                cycle_nodes.update(path[seen_at[current] :])
                break
            seen_at[current] = len(path)
            path.append(current)
            current = graph[current]
    if cycle_nodes:
        invalid_rows.update(cycle_nodes)
        invalid_rows.update(graph[node] for node in cycle_nodes if node in graph)
        reasons.add("supersession_cycle")

    superseded_rows: set[int] = set()
    for replacement, old in candidates:
        if (
            len(targets[old.source_row_number]) == 1
            and replacement.source_row_number not in invalid_rows
            and old.source_row_number not in invalid_rows
        ):
            superseded_rows.add(old.source_row_number)

    return superseded_rows, invalid_rows, reasons


def _global_quality_reasons(snapshot: DirectCashForecastInputSnapshot) -> set[str]:
    reasons: set[str] = set()
    group = snapshot.group
    intake = snapshot.intake
    if group is None:
        reasons.add("source_group_not_found")
    elif group.source_row_count < 1:
        reasons.add("source_group_empty")
    elif group.source_row_count > DIRECT_CASH_FORECAST_MAX_GROUP_ROWS:
        reasons.add("source_group_size_limit_exceeded")
    if snapshot.source_evidence_integrity_status != "verified":
        reasons.add("source_row_evidence_integrity_unverified")
    if intake.quality_status != "passed":
        reasons.add("source_quality_not_passed")
    if intake.reconciliation_status != "passed":
        reasons.add("source_reconciliation_not_passed")
    if intake.freshness_status != "fresh":
        reasons.add("source_not_fresh_at_calculation")
    freshness_now = evaluate_freshness(
        max_age_hours=intake.freshness_max_age_hours,
        source_as_of_at=intake.source_as_of_at,
        evaluated_at=snapshot.captured_at,
    )
    if freshness_now != "fresh":
        reasons.add("source_not_fresh_at_calculation")
    try:
        local_date = snapshot.captured_at.astimezone(ZoneInfo(snapshot.company_timezone)).date()
    except (ValueError, ZoneInfoNotFoundError):
        reasons.add("company_timezone_invalid")
    else:
        if snapshot.as_of_date > local_date:
            reasons.add("future_as_of_date_unsupported")
    return reasons


def _possible_buckets(
    item: DirectCashForecastInputItem,
    snapshot: DirectCashForecastInputSnapshot,
) -> set[BucketKey]:
    if item.cash_flow_type == "receipt":
        directions: tuple[FlowDirection, ...] = ("receipt",)
    elif item.cash_flow_type == "disbursement":
        directions = ("disbursement",)
    else:
        directions = ("receipt", "disbursement")

    if item.basis == "actual":
        bases: tuple[Literal["actual", "forecast"], ...] = ("actual",)
    elif item.basis == "forecast":
        bases = ("forecast",)
    else:
        bases = ("actual", "forecast")

    if item.cash_date is None:
        weeks: tuple[int, ...] = tuple(range(DIRECT_CASH_FORECAST_HORIZON_WEEKS))
    else:
        first_date = snapshot.as_of_date
        final_date = first_date + timedelta(
            days=DIRECT_CASH_FORECAST_HORIZON_WEEKS * DIRECT_CASH_FORECAST_WEEK_DAYS - 1
        )
        if item.cash_date < first_date:
            # A forecast item already dated before the as-of boundary is overdue and may move to
            # any future week. A historical actual is outside the forecast horizon.
            if item.basis == "actual":
                return set()
            weeks = tuple(range(DIRECT_CASH_FORECAST_HORIZON_WEEKS))
        elif item.cash_date > final_date:
            return set()
        else:
            weeks = ((item.cash_date - first_date).days // DIRECT_CASH_FORECAST_WEEK_DAYS,)

    return {
        (week, direction, basis) for week in weeks for direction in directions for basis in bases
    }


def _movement_assessments(
    snapshot: DirectCashForecastInputSnapshot,
    amounts: dict[str, Decimal],
    *,
    superseded_rows: set[int],
    invalid_supersession_rows: set[int],
    request_counts: Counter[int],
) -> tuple[_MovementAssessment, ...]:
    assessments: list[_MovementAssessment] = []
    final_date = snapshot.as_of_date + timedelta(
        days=DIRECT_CASH_FORECAST_HORIZON_WEEKS * DIRECT_CASH_FORECAST_WEEK_DAYS - 1
    )
    for item in snapshot.movements:
        reasons = set(item.issue_codes)
        amount = _amount_is_usable(item, amounts, snapshot)
        evidence_valid = _evidence_is_valid(item, snapshot)
        if item.origin == "missing_source_row":
            reasons.add("cash_item_missing_from_request")
        if request_counts[item.source_row_number] > 1:
            reasons.add(
                "conflicting_request_for_source_row"
                if item.amount_commitment_status == "conflicting"
                else "duplicate_request_for_source_row"
            )
        if not evidence_valid:
            if item.evidence is None:
                reasons.add("source_row_evidence_missing_or_unusable")
            elif item.evidence.row_status == "duplicate":
                reasons.add("duplicate_source_evidence")
            elif item.evidence.row_status != "valid":
                reasons.add("invalid_source_evidence")
            elif item.evidence.reconciliation_group_hash != snapshot.requested_group_hash:
                reasons.add("incompatible_source_group")
            else:
                reasons.add("amount_commitment_unverifiable")
        if amount is None:
            if (
                item.amount_commitment_status == "missing"
                or item.amount_commitment_status == "not_submitted"
            ):
                reasons.add("amount_missing")
            elif item.amount_commitment_status in {"duplicate", "conflicting"}:
                reasons.add("amount_request_duplicate_or_conflicting")
            else:
                reasons.add("amount_invalid_or_unverifiable")

        if item.cash_flow_type == "unknown":
            reasons.add("cash_flow_type_unknown")
        elif item.cash_flow_type == "unsupported":
            reasons.add("cash_flow_type_unsupported")
        if item.basis == "unknown":
            reasons.add("movement_basis_unknown")
        elif item.basis == "unsupported":
            reasons.add("movement_basis_unsupported")
        if item.cash_date is None:
            reasons.add("cash_date_missing")
        if item.cash_flow_type in {"receipt", "disbursement"} and amount is not None and amount < 0:
            reasons.add("negative_flow_magnitude")
            amount = None

        if item.source_row_number in invalid_supersession_rows:
            reasons.add("supersession_unverifiable")
        if item.source_row_number in superseded_rows and not reasons:
            assessments.append(
                _MovementAssessment(
                    item=item,
                    amount=amount,
                    status="superseded_actual_excluded",
                    reason_codes=("superseded_by_explicit_actual_record",),
                    week_index=None,
                    blocking=False,
                )
            )
            continue

        if reasons:
            status: CashItemDispositionStatus
            if request_counts[item.source_row_number] > 1:
                status = (
                    "conflicting_request"
                    if item.amount_commitment_status == "conflicting"
                    else "duplicate_request"
                )
            elif item.origin == "missing_source_row":
                status = "missing_request"
            elif "supersession_unverifiable" in reasons:
                status = "invalid_supersession"
            elif "cash_flow_type_unsupported" in reasons or "movement_basis_unsupported" in reasons:
                status = "unsupported_classification"
            elif "cash_flow_type_unknown" in reasons or "movement_basis_unknown" in reasons:
                status = "unknown_classification"
            elif "cash_date_missing" in reasons:
                status = "no_date_unresolved"
            elif (
                item.basis == "forecast"
                and item.cash_date is not None
                and item.cash_date < snapshot.as_of_date
            ):
                status = "overdue_forecast_unresolved"
                reasons.add("overdue_forecast_item")
            else:
                status = "invalid_evidence"
            assessments.append(
                _MovementAssessment(
                    item=item,
                    amount=None,
                    status=status,
                    reason_codes=tuple(sorted(reasons)),
                    week_index=None,
                    blocking=True,
                )
            )
            continue

        if item.cash_flow_type not in {"receipt", "disbursement"} or item.basis not in {
            "actual",
            "forecast",
        }:
            # The explicit unknown/unsupported conditions above normally cover this branch.
            assessments.append(
                _MovementAssessment(
                    item=item,
                    amount=None,
                    status="unknown_classification",
                    reason_codes=("cash_classification_unresolved",),
                    week_index=None,
                    blocking=True,
                )
            )
            continue
        if item.cash_date is None:
            assessments.append(
                _MovementAssessment(
                    item=item,
                    amount=None,
                    status="no_date_unresolved",
                    reason_codes=("cash_date_missing",),
                    week_index=None,
                    blocking=True,
                )
            )
            continue
        if item.cash_date < snapshot.as_of_date:
            if item.basis == "actual":
                assessments.append(
                    _MovementAssessment(
                        item=item,
                        amount=amount,
                        status="historical_actual_excluded",
                        reason_codes=("historical_actual_before_as_of_excluded",),
                        week_index=None,
                        blocking=False,
                    )
                )
            else:
                assessments.append(
                    _MovementAssessment(
                        item=item,
                        amount=None,
                        status="overdue_forecast_unresolved",
                        reason_codes=("overdue_forecast_item",),
                        week_index=None,
                        blocking=True,
                    )
                )
            continue
        if item.cash_date > final_date:
            assessments.append(
                _MovementAssessment(
                    item=item,
                    amount=amount,
                    status="out_of_horizon_excluded",
                    reason_codes=("cash_item_outside_horizon",),
                    week_index=None,
                    blocking=False,
                )
            )
            continue
        week_index = (item.cash_date - snapshot.as_of_date).days // DIRECT_CASH_FORECAST_WEEK_DAYS
        assessments.append(
            _MovementAssessment(
                item=item,
                amount=amount,
                status="included_in_known_subtotal",
                reason_codes=(),
                week_index=week_index,
                blocking=False,
            )
        )
    return tuple(assessments)


def _subtotal(bucket: _Bucket) -> CashFlowSubtotal:
    known_subtotal: str | None = None
    reasons = set(bucket.reasons)
    precision_failure = False
    if bucket.amounts:
        try:
            known_subtotal = decimal_text(exact_decimal_sum(bucket.amounts))
        except ValueError:
            precision_failure = True
            reasons.add("decimal_precision_unavailable")
    if precision_failure:
        reasons.add("decimal_precision_unavailable")
        return CashFlowSubtotal(
            status="partial" if bucket.amounts or bucket.unresolved_rows else "unknown",
            known_subtotal=None,
            source_row_numbers=tuple(sorted(bucket.source_rows)),
            unresolved_source_row_numbers=tuple(sorted(bucket.unresolved_rows)),
            reason_codes=tuple(sorted(reasons)),
        )
    if bucket.amounts and not bucket.unresolved_rows:
        status: Literal["known_subtotal", "partial", "unknown"] = "known_subtotal"
    elif bucket.amounts or bucket.unresolved_rows:
        status = "partial"
    else:
        status = "unknown"
        reasons.add("cash_category_has_no_explicit_evidence")
    if bucket.unresolved_rows:
        reasons.add("cash_category_has_unresolved_evidence")
    return CashFlowSubtotal(
        status=status,
        known_subtotal=known_subtotal,
        source_row_numbers=tuple(sorted(bucket.source_rows)),
        unresolved_source_row_numbers=tuple(sorted(bucket.unresolved_rows)),
        reason_codes=tuple(sorted(reasons)),
    )


def _combined_known_subtotal(
    first: CashFlowSubtotal, second: CashFlowSubtotal
) -> tuple[str | None, Literal["known_subtotal", "partial", "unknown"], bool]:
    values = [
        parse_output_decimal_text(value)
        for value in (first.known_subtotal, second.known_subtotal)
        if value is not None
    ]
    combined: str | None = None
    precision_failure = False
    if values:
        try:
            combined = decimal_text(exact_decimal_sum(values))
        except ValueError:
            precision_failure = True
    if first.status == second.status == "known_subtotal" and not precision_failure:
        status: Literal["known_subtotal", "partial", "unknown"] = "known_subtotal"
    elif (
        combined is not None
        or first.status == "partial"
        or second.status == "partial"
        or precision_failure
    ):
        status = "partial"
    else:
        status = "unknown"
    return combined, status, precision_failure


def _required_subtotal_decimal(subtotal: CashFlowSubtotal) -> Decimal:
    if subtotal.known_subtotal is None:
        raise ValueError("a resolved cash-flow subtotal must have a numeric value")
    return parse_output_decimal_text(subtotal.known_subtotal)


def _make_disposition(assessment: _MovementAssessment) -> CashItemDisposition:
    item = assessment.item
    assert item.basis is not None
    if item.cash_flow_type == "opening_cash":
        cash_flow_type: Literal["receipt", "disbursement", "unknown", "unsupported"] = "unknown"
    else:
        cash_flow_type = item.cash_flow_type
    return CashItemDisposition(
        schema_version=1,
        input_id=item.input_id,
        source_row_number=item.source_row_number,
        cash_flow_type=cash_flow_type,
        basis=item.basis,
        cash_date=item.cash_date,
        status=assessment.status,
        evidence=item.evidence,
        reason_codes=tuple(sorted(assessment.reason_codes)),
    )


def calculate_direct_cash_forecast(
    snapshot: DirectCashForecastInputSnapshot,
    verified_amounts: dict[str, Decimal],
) -> DirectCashForecastResultSnapshot:
    """Recompute the M16 schedule from caller-attested M14-bound amounts, never defaulting zero.

    `verified_amounts` exists only for this request/replay call. It is not included in the
    immutable input snapshot or persisted; the row-bound M14 commitment and a separate keyed
    fingerprint bind each exact submission.
    """
    snapshot = DirectCashForecastInputSnapshot.model_validate(snapshot.model_dump(mode="python"))
    if not isinstance(verified_amounts, dict):
        raise TypeError("verified_amounts must be a server-constructed mapping")

    reasons = {
        "cash_semantics_not_authenticated_by_m14",
        "forecast_completeness_not_established",
    }
    global_reasons = _global_quality_reasons(snapshot)
    reasons.update(global_reasons)

    submitted_items = [item for item in snapshot.movements if item.origin == "movement"]
    if snapshot.opening_cash is not None:
        submitted_items.append(snapshot.opening_cash)
    request_counts = Counter(item.source_row_number for item in submitted_items)
    if snapshot.group is not None:
        all_items = (
            *snapshot.movements,
            *([snapshot.opening_cash] if snapshot.opening_cash else []),
        )
        referenced_group_rows = {
            item.source_row_number
            for item in all_items
            if item.evidence is not None
            and item.evidence.reconciliation_group_hash == snapshot.requested_group_hash
        }
        if len(referenced_group_rows) != snapshot.group.source_row_count:
            global_reasons.add("source_group_rows_incomplete")
            reasons.add("source_group_rows_incomplete")
    for item in snapshot.movements:
        if item.origin == "missing_source_row":
            reasons.add("cash_item_missing_from_request")

    superseded_rows, invalid_supersession_rows, supersession_reasons = _resolve_supersession(
        snapshot.movements, verified_amounts, snapshot, request_counts
    )
    reasons.update(supersession_reasons)
    if superseded_rows:
        reasons.add("superseded_actuals_excluded")

    assessments = _movement_assessments(
        snapshot,
        verified_amounts,
        superseded_rows=superseded_rows,
        invalid_supersession_rows=invalid_supersession_rows,
        request_counts=request_counts,
    )
    for assessment in assessments:
        reasons.update(assessment.reason_codes)
        if assessment.blocking:
            reasons.add("cash_item_unresolved")
    if any(assessment.status == "historical_actual_excluded" for assessment in assessments):
        reasons.add("historical_actuals_before_as_of_excluded")
    if any(assessment.status == "out_of_horizon_excluded" for assessment in assessments):
        reasons.add("out_of_horizon_items_excluded")

    all_directions: tuple[FlowDirection, ...] = ("receipt", "disbursement")
    all_bases: tuple[Literal["actual", "forecast"], ...] = ("actual", "forecast")
    all_bucket_keys: tuple[BucketKey, ...] = tuple(
        (week, direction, basis)
        for week in range(DIRECT_CASH_FORECAST_HORIZON_WEEKS)
        for direction in all_directions
        for basis in all_bases
    )
    buckets = {key: _Bucket() for key in all_bucket_keys}
    blocking_global = set(global_reasons)
    if supersession_reasons:
        blocking_global.add("supersession_unverifiable")
    opening_item = snapshot.opening_cash
    opening_amount = (
        _amount_is_usable(opening_item, verified_amounts, snapshot)
        if opening_item is not None
        else None
    )
    opening_duplicate = (
        opening_item is not None and request_counts[opening_item.source_row_number] > 1
    )
    if opening_duplicate:
        opening_amount = None
        reasons.add("duplicate_source_row_reference")
        blocking_global.add("duplicate_source_row_reference")
    opening_evidence_valid = (
        opening_item is not None
        and opening_amount is not None
        and not opening_duplicate
        and opening_item.origin == "opening_cash"
    )
    if not opening_evidence_valid:
        reasons.add("opening_cash_missing_or_invalid")
        blocking_global.add("opening_cash_missing_or_invalid")

    # Any global quality failure blocks every weekly roll-forward. Safe known row subtotals
    # remain visible, annotated with the global gate reason codes.
    for key in all_bucket_keys:
        if blocking_global:
            buckets[key].reasons.update(blocking_global)

    for assessment in assessments:
        item = assessment.item
        if assessment.status == "included_in_known_subtotal" and assessment.week_index is not None:
            if item.cash_flow_type == "receipt":
                direction: FlowDirection = "receipt"
            else:
                assert item.cash_flow_type == "disbursement"
                direction = "disbursement"
            if item.basis == "actual":
                basis: Literal["actual", "forecast"] = "actual"
            else:
                assert item.basis == "forecast"
                basis = "forecast"
            assert assessment.amount is not None
            bucket_key: BucketKey = (assessment.week_index, direction, basis)
            buckets[bucket_key].amounts.append(assessment.amount)
            buckets[bucket_key].source_rows.add(item.source_row_number)
            continue
        if assessment.status in {
            "historical_actual_excluded",
            "out_of_horizon_excluded",
            "superseded_actual_excluded",
        }:
            continue
        possible = _possible_buckets(item, snapshot)
        if assessment.status == "overdue_forecast_unresolved":
            # A past-due forecast item may move into any of the 13 future buckets; do not invent
            # a revised date or silently drop it.
            directions: tuple[FlowDirection, ...]
            if item.cash_flow_type == "receipt":
                directions = ("receipt",)
            elif item.cash_flow_type == "disbursement":
                directions = ("disbursement",)
            else:
                directions = ("receipt", "disbursement")
            possible = {
                key for key in all_bucket_keys if key[1] in directions and key[2] == "forecast"
            }
        for key in possible:
            buckets[key].unresolved_rows.add(item.source_row_number)
            buckets[key].reasons.update(assessment.reason_codes)

    if invalid_supersession_rows:
        for item in snapshot.movements:
            if item.source_row_number not in invalid_supersession_rows:
                continue
            for key in _possible_buckets(item, snapshot):
                buckets[key].unresolved_rows.add(item.source_row_number)
                buckets[key].reasons.add("supersession_unverifiable")

    opening_status: Literal["known", "missing_or_invalid"] = (
        "known" if opening_evidence_valid else "missing_or_invalid"
    )
    opening_row_number = opening_item.source_row_number if opening_item is not None else None

    week_lines: list[CashForecastWeek] = []
    dispositions: list[CashItemDisposition] = []
    for assessment in assessments:
        dispositions.append(_make_disposition(assessment))
    dispositions.sort(key=lambda item: (item.source_row_number, item.input_id))

    previous_closing: Decimal | None = None
    for week_index in range(DIRECT_CASH_FORECAST_HORIZON_WEEKS):
        period_start = snapshot.as_of_date + timedelta(
            days=week_index * DIRECT_CASH_FORECAST_WEEK_DAYS
        )
        period_end = period_start + timedelta(days=DIRECT_CASH_FORECAST_WEEK_DAYS - 1)
        if week_index == 0:
            week_opening = opening_amount if opening_evidence_valid else None
            opening_cash_status: Literal[
                "known", "missing_or_invalid", "previous_week_closing_unknown"
            ] = "known" if week_opening is not None else "missing_or_invalid"
        else:
            week_opening = previous_closing
            opening_cash_status = (
                "known" if week_opening is not None else "previous_week_closing_unknown"
            )

        subtotals: dict[tuple[FlowDirection, Literal["actual", "forecast"]], CashFlowSubtotal] = {}
        for direction in all_directions:
            for basis in all_bases:
                key = (week_index, direction, basis)
                subtotals[(direction, basis)] = _subtotal(buckets[key])
                if subtotals[(direction, basis)].status == "unknown":
                    reasons.add("cash_category_has_no_explicit_evidence")
                elif subtotals[(direction, basis)].status == "partial":
                    reasons.add("cash_category_has_unresolved_evidence")

        actual_receipts = subtotals[("receipt", "actual")]
        forecast_receipts = subtotals[("receipt", "forecast")]
        actual_disbursements = subtotals[("disbursement", "actual")]
        forecast_disbursements = subtotals[("disbursement", "forecast")]
        receipts_value, receipts_status, receipts_precision_failure = _combined_known_subtotal(
            actual_receipts, forecast_receipts
        )
        disbursements_value, disbursements_status, disbursements_precision_failure = (
            _combined_known_subtotal(actual_disbursements, forecast_disbursements)
        )
        if receipts_precision_failure or disbursements_precision_failure:
            reasons.add("decimal_precision_unavailable")

        closing: Decimal | None = None
        closing_text: str | None = None
        closing_inputs = (
            actual_receipts,
            forecast_receipts,
            actual_disbursements,
            forecast_disbursements,
        )
        if (
            week_opening is not None
            and all(
                item.status == "known_subtotal" and item.known_subtotal is not None
                for item in closing_inputs
            )
            and not blocking_global
        ):
            try:
                receipt_total = exact_decimal_sum(
                    [
                        _required_subtotal_decimal(actual_receipts),
                        _required_subtotal_decimal(forecast_receipts),
                    ]
                )
                disbursement_total = exact_decimal_sum(
                    [
                        _required_subtotal_decimal(actual_disbursements),
                        _required_subtotal_decimal(forecast_disbursements),
                    ]
                )
                closing = exact_decimal_sum([week_opening, receipt_total, -disbursement_total])
                closing_text = decimal_text(closing)
            except ValueError:
                reasons.add("decimal_precision_unavailable")
                closing = None
                closing_text = None

        closing_status: Literal["known_item_rollforward", "unknown"] = (
            "known_item_rollforward" if closing_text is not None else "unknown"
        )
        week_reasons = set()
        if closing_text is None:
            week_reasons.add("weekly_closing_cash_unavailable")
        if week_opening is None:
            week_reasons.add("opening_cash_or_carry_forward_unknown")
        if receipts_precision_failure or disbursements_precision_failure:
            week_reasons.add("decimal_precision_unavailable")
        week_reasons.update(code for item in subtotals.values() for code in item.reason_codes)
        week_line = CashForecastWeek(
            schema_version=1,
            week_number=week_index + 1,
            period_start=period_start,
            period_end=period_end,
            opening_cash=decimal_text(week_opening) if week_opening is not None else None,
            opening_cash_status=opening_cash_status,
            actual_receipts=actual_receipts,
            forecast_receipts=forecast_receipts,
            actual_disbursements=actual_disbursements,
            forecast_disbursements=forecast_disbursements,
            receipts_known_subtotal=receipts_value,
            receipts_status=receipts_status,
            disbursements_known_subtotal=disbursements_value,
            disbursements_status=disbursements_status,
            closing_cash_known_item_rollforward=closing_text,
            closing_cash_status=closing_status,
            reason_codes=tuple(sorted(week_reasons)),
        )
        week_lines.append(week_line)
        previous_closing = closing

    blocking_reason_codes = reasons - {
        "cash_semantics_not_authenticated_by_m14",
        "forecast_completeness_not_established",
        "superseded_actuals_excluded",
        "historical_actuals_before_as_of_excluded",
        "out_of_horizon_items_excluded",
        "cash_item_outside_horizon",
        "historical_actual_before_as_of_excluded",
        "superseded_by_explicit_actual_record",
    }
    calculation_state: Literal["calculated", "incomplete"] = (
        "calculated"
        if not blocking_reason_codes
        and len(week_lines) == DIRECT_CASH_FORECAST_HORIZON_WEEKS
        and all(week.closing_cash_known_item_rollforward is not None for week in week_lines)
        else "incomplete"
    )
    horizon_end = snapshot.as_of_date + timedelta(
        days=DIRECT_CASH_FORECAST_HORIZON_WEEKS * DIRECT_CASH_FORECAST_WEEK_DAYS - 1
    )
    result = DirectCashForecastResultSnapshot(
        schema_version=1,
        calculation_state=calculation_state,
        forecast_completeness_status="not_established_by_m14",
        cash_semantics_origin="caller_asserted_not_authenticated_by_m14",
        as_of_date=snapshot.as_of_date,
        horizon_end_date=horizon_end,
        company_timezone=snapshot.company_timezone,
        currency_code=snapshot.group.currency_code if snapshot.group is not None else None,
        opening_cash_status=opening_status,
        opening_cash_source_row_number=opening_row_number,
        weeks=tuple(week_lines),
        item_dispositions=tuple(dispositions),
        reason_codes=tuple(sorted(reasons)),
        limitations=(
            (
                "M14 verifies the exact resubmitted amount against row-bound evidence but does not "
                "authenticate its cash classification, timing, actual/forecast "
                "basis, or completeness."
            ),
            (
                "Every numeric category value is a subtotal of explicit caller-classified rows; "
                "absent categories remain unknown, not zero."
            ),
            (
                "A known-item closing roll-forward is not an authoritative cash balance or a "
                "complete/accurate forecast."
            ),
            (
                "M14 source identity, source freshness, and caller-supplied reconciliation "
                "controls are not independently authenticated."
            ),
            (
                "Finance-owner, source-authority, correction, retention, cash-policy, and "
                "production approvals are not established."
            ),
        ),
    )
    return result
