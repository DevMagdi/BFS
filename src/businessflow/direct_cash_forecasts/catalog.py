from __future__ import annotations

from businessflow.metrics.snapshot import content_hash

DIRECT_CASH_FORECAST_CONTRACT_KEY = "businessflow.finance.direct-cash-forecast"
DIRECT_CASH_FORECAST_CONTRACT_VERSION = "1.0.0"
DIRECT_CASH_FORECAST_ENGINE_VERSION = "businessflow.direct-cash-forecast.v1"
DIRECT_CASH_FORECAST_CATALOG_VERSION = "1.0.0"
DIRECT_CASH_FORECAST_FORMULA_ID = "businessflow.finance.direct-cash-forecast"
DIRECT_CASH_FORECAST_FORMULA_VERSION = "1.0.0"
DIRECT_CASH_FORECAST_HORIZON_WEEKS = 13
DIRECT_CASH_FORECAST_WEEK_DAYS = 7
DIRECT_CASH_FORECAST_MAX_GROUP_ROWS = 10_000

# M14 remains semantic-light. The only M14-backed financial value used by M16 is a
# caller-resubmitted amount that verifies against its existing row-bound HMAC commitment,
# together with the exact source group currency and opaque row/group hashes. M16 cash roles,
# event dates, actual/forecast labels and supersession links are separate caller assertions.
DIRECT_CASH_FORECAST_CONTRACT_DEFINITION: dict[str, object] = {
    "schema_version": 1,
    "contract_key": DIRECT_CASH_FORECAST_CONTRACT_KEY,
    "contract_version": DIRECT_CASH_FORECAST_CONTRACT_VERSION,
    "formula": {
        "formula_id": DIRECT_CASH_FORECAST_FORMULA_ID,
        "formula_version": DIRECT_CASH_FORECAST_FORMULA_VERSION,
        "expression": "closing_cash = opening_cash + receipts - disbursements",
        "carry_forward": "week_n_opening_cash = week_(n-1)_closing_cash; unknown stays unknown",
        "arithmetic": "M11 bounded exact Decimal arithmetic; no rounding or quantization",
    },
    "horizon": {
        "weeks": DIRECT_CASH_FORECAST_HORIZON_WEEKS,
        "week_length_calendar_days": DIRECT_CASH_FORECAST_WEEK_DAYS,
        "start": "as_of_date is inclusive and is the first date of week 1",
        "boundaries": (
            "each week includes its start and end dates; the next starts the following date"
        ),
        "end": "as_of_date plus 90 calendar days, inclusive",
        "timezone": "company IANA timezone; source cash dates are local calendar dates",
    },
    "opening_cash": (
        "Exactly one explicitly designated M14 row is required for a numeric opening balance. "
        "Its caller-supplied cash meaning is an opening balance at 00:00 local time on as_of_date. "
        "M14 verifies only the exact amount commitment and row/group evidence, not that meaning."
    ),
    "movement_inputs": {
        "types": ["receipt", "disbursement"],
        "basis": ["actual", "forecast"],
        "cash_date": (
            "explicit caller-supplied date; no M14 period date is reinterpreted as cash timing"
        ),
        "amount": (
            "Receipt and disbursement amounts are non-negative magnitudes. A negative value is "
            "invalid; no absolute-value/sign inference is applied. An explicit zero row is allowed."
        ),
        "actual_forecast": "explicit caller label; never inferred from the date or current time",
    },
    "source_binding": {
        "source": "one exact M14 intake and one exact M14 reconciliation-group hash",
        "currency": "the group's one source-reported ISO 4217 currency; no FX or mixing",
        "amount": "caller resubmits each amount; server verifies its existing M14 keyed commitment",
        "cash_semantics": (
            "flow type, actual/forecast basis, cash date and supersession are explicit M16 caller "
            "assertions and are not authenticated by the generic M14 contract"
        ),
        "raw_source_retention": (
            "no raw row values or submitted amount strings in M16 input snapshots"
        ),
    },
    "supersession": {
        "scope": "explicit links between actual movements in the same M14 intake/group only",
        "behavior": (
            "validated replacement chains exclude each explicitly superseded row once; duplicate "
            "targets, cycles, cross-type links and unresolved links remain incomplete"
        ),
        "authority": (
            "caller assertion only; M14 has no cross-intake correction/supersession contract"
        ),
    },
    "missing_and_boundary_behavior": {
        "absent_category": (
            "unknown, never zero-filled; a source-bound explicit zero row is required"
        ),
        "missing_date": "unknown and excluded from arithmetic; result remains incomplete",
        "overdue_forecast": "a forecast-dated item before as_of_date is unresolved and excluded",
        "historical_actual": "an actual dated before as_of_date is retained as excluded context",
        "out_of_horizon": "an item after the inclusive horizon end is retained and excluded",
        "unknown_or_unsupported": "visible, not discarded as a valid complete result",
        "invalid_or_duplicate": "visible, not summed, and prevents a calculated schedule",
    },
    "quality_and_governance": {
        "forecast_completeness": "not_established; this prototype cannot prove population coverage",
        "source_authority": "not_established",
        "finance_owner_approval": "not_established",
        "cash_semantics_approval": "not_established",
        "forecast_accuracy": "not_established",
        "production_readiness": "not_established",
        "customer_or_production_data_authorization": "not_authorized",
        "control_interpretation": (
            "M14's caller-supplied generic group comparison is not independent cash reconciliation"
        ),
    },
    "limitations": [
        "This is a deterministic arithmetic schedule, not a source-authoritative cash forecast.",
        "M14 period_start and period_end are not cash dates and are not used as such.",
        (
            "M14 does not authenticate opening-cash, receipt/disbursement, date, actual/forecast, "
            "or completeness semantics."
        ),
        (
            "An exact computed item subtotal or roll-forward is not evidence of forecast "
            "completeness or accuracy."
        ),
        (
            "No accrual conversion, probability, statistical model, AI arithmetic, FX, payment "
            "execution, or cash facility draw is performed."
        ),
        (
            "Historical runs are immutable; replay requires the same M14 evidence, exact request "
            "and compatible engine/formula version."
        ),
    ],
}

DIRECT_CASH_FORECAST_FORMULA_HASH = content_hash(DIRECT_CASH_FORECAST_CONTRACT_DEFINITION)
