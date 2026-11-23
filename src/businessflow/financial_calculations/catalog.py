from __future__ import annotations

from businessflow.financial_calculations.contracts import FormulaCatalogEntry
from businessflow.metrics.snapshot import content_hash

FINANCIAL_CALCULATION_ENGINE_VERSION = "businessflow.financial-calc.v1"
FINANCIAL_CALCULATION_CATALOG_VERSION = "1.0.0"
SOURCE_GROUP_SUBTOTAL_ID = "businessflow.finance.source-group-subtotal"
SOURCE_GROUP_SUBTOTAL_VERSION = "1.0.0"

SOURCE_GROUP_SUBTOTAL = FormulaCatalogEntry(
    formula_id=SOURCE_GROUP_SUBTOTAL_ID,
    formula_version=SOURCE_GROUP_SUBTOTAL_VERSION,
    status="executable_mechanical_only",
    name="M14 source-group exact subtotal",
    formula_expression=(
        "sum(canonical_decimal(amount_text_i) for every M14-valid row i in exactly one "
        "M14 reconciliation group)"
    ),
    meaning=(
        "An exact arithmetic subtotal of caller-resubmitted numeric values that match "
        "M14 keyed amount commitments; it has no revenue, expense, balance, cash, or "
        "accounting classification."
    ),
    input_contract=(
        "One M14 intake with verified intake/result/input hashes, exact contract and mapping "
        "versions, passed row quality, a fresh caller-asserted as-of state, one opaque group hash, "
        "all and only its unique M14-valid source rows, and a resubmitted amount for each row "
        "whose canonical Decimal text verifies against its row-bound HMAC commitment."
    ),
    period_rule=(
        "Use only the exact source-reported M14 period_start and period_end for one opaque group; "
        "the dates must be valid and end on/after start. No fiscal calendar, recognition date, "
        "period normalization, or cross-period aggregation is inferred."
    ),
    currency_rule=(
        "All inputs must match the one ISO 4217 currency in the M14 group. Preserve that code; "
        "no FX, currency netting, or cross-currency arithmetic is performed."
    ),
    precision_rule=(
        "Inputs are bounded plain Decimal strings of at most 64 characters. M11 uses an isolated "
        "256-digit Decimal context, a maximum of 10,000 values, and rejects Rounded/Inexact sums "
        "or output text exceeding 512 characters."
    ),
    rounding_rule=(
        "No rounding or quantization: output is canonical, exact fixed-point Decimal text; "
        "ISO currency minor-unit exponents do not alter the result."
    ),
    missing_data_rule=(
        "Missing, invalid, duplicate, conflicting, mismatched, stale, quality-failed, "
        "uncommitted, incompatible, or incomplete row evidence yields an incomplete trace with "
        "no output; no value is replaced with zero."
    ),
    policy_state="mechanical_only_not_accounting_policy",
    deferral_reason=None,
    required_decisions=(),
)

_DEFERRED_FORMULAS = (
    FormulaCatalogEntry(
        formula_id="businessflow.finance.gross-profit",
        formula_version=None,
        status="deferred",
        name="Gross profit",
        formula_expression=None,
        meaning="Revenue less cost of goods sold under an approved accounting definition.",
        input_contract=(
            "No M15 input account classification or authoritative finance fact model exists."
        ),
        period_rule="Accounting period and recognition/cut-off rules are not approved.",
        currency_rule="Currency and any translation policy are not approved.",
        precision_rule="No formula-specific precision contract has been approved.",
        rounding_rule="No rounding policy has been approved.",
        missing_data_rule="Remain deferred; missing or conflicting facts cannot be interpreted.",
        policy_state="required_not_approved",
        deferral_reason=(
            "Requires an accountable finance owner, authoritative revenue/COGS definitions, "
            "classification and recognition rules, and independently checked golden values."
        ),
        required_decisions=(
            "finance owner and authoritative source",
            "revenue and cost-of-goods-sold account definitions",
            "period recognition and correction policy",
            "currency and rounding policy",
            "independent golden expected results",
        ),
    ),
    FormulaCatalogEntry(
        formula_id="businessflow.finance.margin-ratios",
        formula_version=None,
        status="deferred",
        name="Gross or operating margin",
        formula_expression=None,
        meaning="A financial ratio with an owner-approved numerator and denominator.",
        input_contract=(
            "No approved revenue, expense, or numerator/denominator fact semantics exist."
        ),
        period_rule="Period alignment and denominator selection are not approved.",
        currency_rule="Numerator/denominator currency compatibility is not approved.",
        precision_rule="Ratio scale and precision are not approved.",
        rounding_rule="Ratio rounding/display rules are not approved.",
        missing_data_rule="Zero, negative, missing, or conflicting denominators remain unresolved.",
        policy_state="required_not_approved",
        deferral_reason=(
            "Requires approved accounting classification, period alignment, zero/negative "
            "denominator behavior, and ratio precision/rounding policy."
        ),
        required_decisions=(
            "numerator and denominator definitions",
            "period and classification rules",
            "zero/negative denominator behavior",
            "ratio precision and rounding",
            "independent golden expected results",
        ),
    ),
    FormulaCatalogEntry(
        formula_id="businessflow.finance.period-rollup",
        formula_version=None,
        status="deferred",
        name="Multi-period or cross-source rollup",
        formula_expression=None,
        meaning="Aggregation across more than one M14 group, period, or source.",
        input_contract=(
            "No source selection, supersession, correction, or cross-source authority "
            "contract exists."
        ),
        period_rule=(
            "Fiscal periods, overlap, cut-off, and period aggregation rules are not approved."
        ),
        currency_rule="Cross-currency compatibility or translation is not approved.",
        precision_rule="No formula-specific precision contract has been approved.",
        rounding_rule="No rounding policy has been approved.",
        missing_data_rule=(
            "Missing, duplicate, overlapping, and superseded rows cannot be resolved."
        ),
        policy_state="required_not_approved",
        deferral_reason=(
            "M14 only identifies caller-asserted source rows and an opaque exact group; it does "
            "not establish cross-source authority, correction, supersession, or accounting period."
        ),
        required_decisions=(
            "source authority and source-selection precedence",
            "correction, duplicate, and supersession policy",
            "fiscal period and cut-off rules",
            "currency conversion policy if required",
            "independent golden expected results",
        ),
    ),
    FormulaCatalogEntry(
        formula_id="businessflow.finance.cash-forecast",
        formula_version=None,
        status="deferred",
        name="Cash forecast or runway",
        formula_expression=None,
        meaning="A forecast of future cash balances or cash runway.",
        input_contract=(
            "No approved opening cash, receipts, disbursements, timing, or probability model "
            "exists."
        ),
        period_rule="Forecast horizon and event timing rules are not approved.",
        currency_rule="Cash currency and translation rules are not approved.",
        precision_rule="Forecast uncertainty and precision rules are not approved.",
        rounding_rule="No rounding policy has been approved.",
        missing_data_rule="Unknown cash flows, dates, or statuses cannot be assigned a default.",
        policy_state="required_not_approved",
        deferral_reason=(
            "Requires finance-owner definitions, authoritative cash sources, timing and "
            "uncertainty policies, and independent back-testing; no prediction is inferred."
        ),
        required_decisions=(
            "cash-flow source and ownership",
            "opening balance and event timing rules",
            "horizon and uncertainty model",
            "currency and rounding policy",
            "independent expected results and evaluation plan",
        ),
    ),
)

FINANCIAL_FORMULA_CATALOG = (SOURCE_GROUP_SUBTOTAL, *_DEFERRED_FORMULAS)
FINANCIAL_FORMULA_CATALOG_HASH = content_hash(
    [item.model_dump(mode="json") for item in FINANCIAL_FORMULA_CATALOG]
)
