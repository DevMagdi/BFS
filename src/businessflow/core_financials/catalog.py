from __future__ import annotations

from typing import Final

from businessflow.core_financials.contracts import CoreFinancialPolicyCreateRequest

CORE_FINANCIAL_ENGINE_VERSION: Final = "businessflow.core-financials.v2-revenue-only"
CORE_FINANCIAL_SOURCE_CONTRACT_VERSION: Final = 1
CORE_FINANCIAL_MAX_CONTROL_COUNT: Final = 6
CORE_FINANCIAL_MAX_ROWS: Final = 10_000

ILLUSTRATIVE_POLICY_DISCLAIMER: Final = (
    "Illustrative management-reporting example only. This is not an approved accounting policy, "
    "does not establish GAAP/IFRS treatment, and must not be represented as authoritative or "
    "customer-validated. A finance owner must approve definitions before production use."
)


def illustrative_policy_template() -> CoreFinancialPolicyCreateRequest:
    """Return an explicit editable example; unknown coverage blocks calculations by default."""
    return CoreFinancialPolicyCreateRequest.model_validate(
        {
            "policy_key": "illustrative.core-financials",
            "reporting_framework": "management_reporting",
            "ledger_basis": "accrual",
            "account_map": {
                "4000": {"category": "revenue", "normal_balance": "credit"},
                "4090": {"category": "revenue_reduction", "normal_balance": "debit"},
                "5000": {"category": "cost_of_sales", "normal_balance": "debit"},
                "6000": {"category": "operating_expense", "normal_balance": "debit"},
                "7000": {"category": "non_operating_income", "normal_balance": "credit"},
                "7100": {"category": "non_operating_expense", "normal_balance": "debit"},
                "8000": {"category": "tax_expense", "normal_balance": "debit"},
            },
            "category_coverage": {
                "revenue": "unknown",
                "revenue_reduction": "unknown",
                "cost_of_sales": "unknown",
                "operating_expense": "unknown",
                "non_operating_income": "unknown",
                "non_operating_expense": "unknown",
                "tax_expense": "unknown",
            },
            "metric_formulas": {
                "revenue": {
                    "terms": [
                        {"category": "revenue", "coefficient": 1},
                        {"category": "revenue_reduction", "coefficient": -1},
                    ]
                },
                "expenses": {
                    "terms": [
                        {"category": "cost_of_sales", "coefficient": 1},
                        {"category": "operating_expense", "coefficient": 1},
                        {"category": "non_operating_expense", "coefficient": 1},
                        {"category": "tax_expense", "coefficient": 1},
                    ]
                },
                "gross_profit": {
                    "terms": [
                        {"category": "revenue", "coefficient": 1},
                        {"category": "revenue_reduction", "coefficient": -1},
                        {"category": "cost_of_sales", "coefficient": -1},
                    ]
                },
                "operating_profit": {
                    "terms": [
                        {"category": "revenue", "coefficient": 1},
                        {"category": "revenue_reduction", "coefficient": -1},
                        {"category": "cost_of_sales", "coefficient": -1},
                        {"category": "operating_expense", "coefficient": -1},
                    ]
                },
                "net_profit": {
                    "terms": [
                        {"category": "revenue", "coefficient": 1},
                        {"category": "revenue_reduction", "coefficient": -1},
                        {"category": "cost_of_sales", "coefficient": -1},
                        {"category": "operating_expense", "coefficient": -1},
                        {"category": "non_operating_income", "coefficient": 1},
                        {"category": "non_operating_expense", "coefficient": -1},
                        {"category": "tax_expense", "coefficient": -1},
                    ]
                },
            },
            "rounding": {"target": "none", "mode": "half_even"},
        }
    )
