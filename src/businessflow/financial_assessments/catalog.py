from __future__ import annotations

from typing import Final

from businessflow.financial_assessments.schemas import FinancialVerdict

FINANCIAL_POLICY_CATALOG_VERSION: Final = "m8-financial-policy-catalog-v1"
FINANCIAL_POLICY_CATALOG_STATUS: Final = "no_approved_policy"

# Deliberately empty until an accountable finance owner supplies and approves an exact
# formula and decision-policy definition. Candidate formulas are not executable policy.
APPROVED_FINANCIAL_POLICIES: Final[tuple[dict[str, object], ...]] = ()
APPROVED_FORMULA_DEFINITIONS: Final[tuple[dict[str, object], ...]] = ()

SUPPORTED_FINANCIAL_VERDICTS: Final[tuple[FinancialVerdict, ...]] = (
    "financially_feasible",
    "financially_feasible_with_conditions",
    "financially_not_feasible",
    "incomplete",
)


def policy_catalog_payload() -> dict[str, object]:
    return {
        "catalog_version": FINANCIAL_POLICY_CATALOG_VERSION,
        "status": FINANCIAL_POLICY_CATALOG_STATUS,
        "approved_policies": [],
        "formula_definitions": [],
        "supported_outcomes": list(SUPPORTED_FINANCIAL_VERDICTS),
        "emitted_outcome_without_approval": "incomplete",
        "currency_handling": (
            "Preserve each ISO 4217 source currency; no FX or cross-currency arithmetic."
        ),
        "decimal_handling": (
            "Accept decimal strings and preserve them exactly; no rounding is performed."
        ),
        "policy_approval": None,
        "policy_effective_dates": None,
        "administration_available": False,
    }
