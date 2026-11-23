"""BFS-owned deterministic Decimal calculation and verification primitives.

The core has no database or finance-policy catalog integration. Callers must provide
versioned formula and evidence contracts; missing evidence produces an abstention.
A formula without an approved policy binding abstains. Caller-supplied approval
metadata is not proof of approver authority and must be externally authorized.
"""

from businessflow.calculation_core.contracts import (
    CalculationInputSnapshot,
    CalculationOutput,
    CalculationTrace,
    FormulaDefinition,
    FormulaPolicyBinding,
    IndependentExpectedResult,
    MechanicalSumFormulaDefinition,
    NumericAssumption,
    NumericObservation,
    RoundingPolicy,
    SourceReference,
    formula_contract_hash,
    formula_hash,
    input_snapshot_hash,
)
from businessflow.calculation_core.decimal import (
    RoundingMode,
    currency_exponent,
    decimal_text,
    exact_decimal_sum,
    parse_decimal_text,
    quantize_decimal,
    round_currency_amount,
)
from businessflow.calculation_core.engine import (
    ENGINE_VERSION,
    CalculationDomainError,
    CalculationError,
    CalculationPrecisionError,
    calculate_formula,
)
from businessflow.calculation_core.verification import (
    ReconciliationReport,
    ReplayVerificationReport,
    reconcile_values,
    verify_independent_expected_result,
    verify_replay,
)

__all__ = [
    "ENGINE_VERSION",
    "CalculationDomainError",
    "CalculationError",
    "CalculationInputSnapshot",
    "CalculationOutput",
    "CalculationPrecisionError",
    "CalculationTrace",
    "FormulaDefinition",
    "FormulaPolicyBinding",
    "IndependentExpectedResult",
    "MechanicalSumFormulaDefinition",
    "NumericAssumption",
    "NumericObservation",
    "ReconciliationReport",
    "ReplayVerificationReport",
    "RoundingMode",
    "RoundingPolicy",
    "SourceReference",
    "calculate_formula",
    "currency_exponent",
    "decimal_text",
    "exact_decimal_sum",
    "formula_contract_hash",
    "formula_hash",
    "input_snapshot_hash",
    "parse_decimal_text",
    "quantize_decimal",
    "reconcile_values",
    "round_currency_amount",
    "verify_independent_expected_result",
    "verify_replay",
]
