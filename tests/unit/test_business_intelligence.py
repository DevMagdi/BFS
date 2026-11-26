from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from businessflow.business_intelligence.analysis_service import (
    MAX_BI_SOURCE_RECORDS,
    _count_source_records,
    _definition_requires_finance_read,
    _readiness_response,
    _source_authority_requirement_failure,
    _system_metric_reference_for_calculation,
)
from businessflow.business_intelligence.engine import calculate_saved_analysis
from businessflow.business_intelligence.registry import (
    SYSTEM_METRIC_BY_KEY,
    system_metric_definition_hash,
)
from businessflow.business_intelligence.registry_service import (
    _validate_formula_dependency_shape,
)
from businessflow.business_intelligence.schemas import (
    FormulaExpression,
    MetricDefinitionCreateRequest,
    MetricEvidenceReference,
    MetricFilter,
    MetricReadinessCheck,
)
from businessflow.errors import ApiError


def _record(
    record_id: str,
    *,
    amount: str,
    status: str,
    transaction_date: str = "2025-01-06",
    currency_code: str = "USD",
) -> dict[str, object]:
    return {
        "source_id": "source-1",
        "record_id": record_id,
        "revision": 1,
        "entity_type": "sales_transaction",
        "attributes": {
            "transaction_reference": record_id,
            "transaction_date": transaction_date,
            "currency_code": currency_code,
            "amount": amount,
            "status": status,
        },
    }


def _universal_definition(
    *,
    calculation_key: str = "universal_record_count",
    calculation: dict[str, object] | None = None,
    policy: dict[str, object] | None = None,
    unit: str = "records",
    currency_code: str | None = None,
    precision_scale: int = 0,
) -> dict[str, object]:
    return {
        "metric_key": "sales.transaction_count",
        "version": 1,
        "definition_hash": "a" * 64,
        "calculation_key": calculation_key,
        "calculation": calculation or {"kind": calculation_key, "entity_type": "sales_transaction"},
        "filters": [],
        "exclusions": [],
        "dimensions": [],
        "time_granularity": "none",
        "period_boundary": "calendar_period",
        "policy": policy or {"currency_behavior": "not_applicable"},
        "unit": unit,
        "currency_code": currency_code,
        "precision_scale": precision_scale,
        "rounding_mode": "half_even",
        "limitations": ["Source-scoped observations only."],
    }


def _universal_input(
    records: list[dict[str, object]],
    *,
    dimensions: list[str] | None = None,
    filters: list[dict[str, object]] | None = None,
    evidence: list[dict[str, object]] | None = None,
    semantic_relationships: list[dict[str, object]] | None = None,
    external_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "period": {"start": None, "end": None},
        "dimensions": dimensions or [],
        "analysis_filters": filters or [],
        "sensitivity_scope": "company_context",
        "calculation_inputs": {
            "records": records,
            "evidence": evidence or [],
            "semantic_relationships": semantic_relationships or [],
            "external_evidence": external_evidence or [],
            "blockers": [],
            "warnings": [],
        },
    }


def _dependency(
    metric_key: str,
    *,
    value: str | None,
    unit: str = "USD",
    currency_code: str | None = "USD",
    status: str = "complete",
) -> dict[str, object]:
    return {
        "metric_key": metric_key,
        "result_snapshot": {
            "status": status,
            "groups": [
                {
                    "dimensions": {},
                    "value": value,
                    "unit": unit,
                    "currency_code": currency_code,
                }
            ],
        },
    }


def _formula_definition(
    *,
    expression: dict[str, object],
    unit: str,
    currency_code: str | None = None,
) -> dict[str, object]:
    return {
        "metric_key": "finance.test_formula",
        "version": 1,
        "definition_hash": "b" * 64,
        "calculation_key": "custom_formula",
        "calculation": {"kind": "custom_formula"},
        "formula": expression,
        "numerator": None,
        "denominator": None,
        "unit": unit,
        "currency_code": currency_code,
        "precision_scale": 2,
        "rounding_mode": "half_even",
        "limitations": ["Formula inputs are saved analyses."],
    }


def _formula_input(dependencies: list[dict[str, object]]) -> dict[str, object]:
    return {
        "period": {"start": None, "end": None},
        "dimensions": [],
        "dependency_analyses": dependencies,
        "sensitivity_scope": "finance",
        "calculation_inputs": {
            "evidence": [],
            "semantic_relationships": [],
            "external_evidence": [
                {
                    "evidence_kind": "dependency_analysis",
                    "analysis_id": f"analysis-{index}",
                    "metric_key": item["metric_key"],
                    "input_hash": f"{index:064x}",
                    "result_hash": f"{index + 1:064x}",
                    "definition_hash": "c" * 64,
                }
                for index, item in enumerate(dependencies, start=1)
            ],
        },
    }


def test_metric_schema_rejects_identity_distinct_values_and_non_scalar_m5_grain():
    base = {
        "expected_version": 0,
        "metric_key": "customers.distinct_codes",
        "name": "Distinct source customer codes",
        "description": "Counts source customer codes without resolving identity.",
        "domain": "customers",
        "category": "customer_volume",
        "business_meaning": "A source-specific count only.",
        "calculation": {
            "kind": "universal_distinct_source_values",
            "entity_type": "customer",
            "field": "customer_code",
        },
        "aggregation_method": "distinct_count",
        "unit": "source values",
        "required_source_datasets": [{"dataset_type": "universal_records"}],
        "limitations": ["Source codes are not customer identities."],
    }
    with pytest.raises(ValidationError, match="identity or personal fields"):
        MetricDefinitionCreateRequest.model_validate(base)

    m5_body = {
        **base,
        "metric_key": "operations.capacity",
        "domain": "operations",
        "calculation": {"kind": "m5_metric", "metric_id": "available_capacity_hours"},
        "aggregation_method": "source_defined",
        "unit": "hours per employee-week",
        "required_source_datasets": [{"dataset_type": "m5"}],
        "dimensions": [],
        "time_granularity": "none",
    }
    with pytest.raises(ValidationError, match="saved weekly grain"):
        MetricDefinitionCreateRequest.model_validate(m5_body)
    with pytest.raises(ValidationError, match="M5 metric units must match"):
        MetricDefinitionCreateRequest.model_validate(
            {
                **m5_body,
                "unit": "arbitrary units",
                "dimensions": ["week"],
                "time_granularity": "week",
            }
        )
    with pytest.raises(ValidationError, match="source-currency unit"):
        MetricDefinitionCreateRequest.model_validate(
            {
                "expected_version": 0,
                "metric_key": "finance.revenue_in_hours",
                "name": "Mislabelled revenue",
                "description": "Incorrect unit labels should not change a saved run.",
                "domain": "finance",
                "category": "finance_amount",
                "business_meaning": "A finance source result.",
                "calculation": {"kind": "core_financial_measure", "measure": "revenue"},
                "aggregation_method": "source_defined",
                "unit": "hours",
                "required_source_datasets": [{"dataset_type": "core_financials"}],
                "policy": {"currency_behavior": "single_currency_only"},
                "limitations": ["Core Financials remains illustrative."],
            }
        )


def test_metric_schema_binds_units_and_rejects_unverifiable_authority_claims():
    amount_body = {
        "expected_version": 0,
        "metric_key": "sales.source_amount_wrong_unit",
        "name": "Source amount with wrong unit",
        "description": "A source amount cannot be relabelled as another kind of quantity.",
        "domain": "sales",
        "category": "source_amount",
        "business_meaning": "A source-scoped amount sum only.",
        "calculation": {
            "kind": "universal_source_amount_sum",
            "entity_type": "sales_transaction",
        },
        "aggregation_method": "sum",
        "unit": "hours",
        "required_source_datasets": [{"dataset_type": "universal_records", "required": True}],
        "policy": {"currency_behavior": "single_currency_only"},
        "limitations": ["No currency conversion or accounting recognition is applied."],
    }
    with pytest.raises(ValidationError, match="source amount metric units"):
        MetricDefinitionCreateRequest.model_validate(amount_body)

    count_body = {
        **amount_body,
        "metric_key": "sales.record_count_wrong_unit",
        "calculation": {
            "kind": "universal_record_count",
            "entity_type": "sales_transaction",
        },
        "aggregation_method": "count",
        "unit": "USD",
        "policy": {},
    }
    with pytest.raises(ValidationError, match="record-count metric units"):
        MetricDefinitionCreateRequest.model_validate(count_body)

    count_body["unit"] = "records"
    count_body["policy"] = {"source_authority_requirement": "reviewed_company_policy"}
    with pytest.raises(ValidationError):
        MetricDefinitionCreateRequest.model_validate(count_body)

    failure = _source_authority_requirement_failure(
        "reviewed_company_policy", "owner_asserted_primary", "owner_asserted_primary"
    )
    assert failure is not None
    assert failure[0] == "source_authority_requirement_unsupported"
    assert (
        _source_authority_requirement_failure(
            "owner_asserted_primary", "owner_asserted_primary", "owner_asserted_primary"
        )
        is None
    )
    assert (
        _source_authority_requirement_failure(
            "owner_asserted_primary", "owner_asserted_primary", "unclassified"
        )
        is not None
    )

    assert _system_metric_reference_for_calculation(
        {"kind": "m5_metric", "metric_id": "available_capacity_hours"}
    ) == (
        "m5.available_capacity_hours",
        SYSTEM_METRIC_BY_KEY["m5.available_capacity_hours"].version,
    )
    assert _system_metric_reference_for_calculation(
        {"kind": "core_financial_measure", "measure": "revenue"}
    ) == (
        "core_financials.revenue",
        SYSTEM_METRIC_BY_KEY["core_financials.revenue"].version,
    )
    assert (
        _system_metric_reference_for_calculation(
            {"kind": "m5_metric", "metric_id": "unsupported_metric"}
        )
        is None
    )


def test_metric_schema_validates_typed_filter_values_and_formula_fields():
    with pytest.raises(ValidationError, match="canonical date or decimal"):
        MetricFilter.model_validate(
            {
                "entity_type": "sales_transaction",
                "field": "amount",
                "operation": "greater_than",
                "value": "1e3",
            }
        )
    with pytest.raises(ValidationError, match="canonical date or decimal"):
        MetricFilter.model_validate(
            {
                "entity_type": "sales_transaction",
                "field": "transaction_date",
                "operation": "equals",
                "value": "not-a-date",
            }
        )
    with pytest.raises(ValidationError, match="canonical date or decimal"):
        MetricFilter.model_validate(
            {
                "entity_type": "sales_transaction",
                "field": "transaction_date",
                "operation": "equals",
                "value": "20250106",
            }
        )

    body = {
        "expected_version": 0,
        "metric_key": "sales.count_with_ignored_formula",
        "name": "Count sales transactions",
        "description": "Counts source records.",
        "domain": "sales",
        "category": "sales_volume",
        "business_meaning": "A source-specific record count.",
        "calculation": {
            "kind": "universal_record_count",
            "entity_type": "sales_transaction",
        },
        "formula": {"op": "metric_ref", "metric_key": "sales.other"},
        "aggregation_method": "count",
        "unit": "records",
        "required_source_datasets": [{"dataset_type": "universal_records"}],
        "limitations": ["Counts records, not approved financial events."],
    }
    with pytest.raises(ValidationError, match="only by custom_formula"):
        MetricDefinitionCreateRequest.model_validate(body)
    with pytest.raises(ValidationError):
        MetricDefinitionCreateRequest.model_validate(
            {
                "expected_version": 0,
                "metric_key": "sales.invalid_name_type",
                "name": 123,
                "description": "Counts source records.",
                "domain": "sales",
                "category": "sales_volume",
                "business_meaning": "A source-scoped record count.",
                "calculation": {
                    "kind": "universal_record_count",
                    "entity_type": "sales_transaction",
                },
                "aggregation_method": "count",
                "unit": "records",
                "required_source_datasets": [{"dataset_type": "universal_records"}],
                "limitations": ["Source records are not recognized financial facts."],
            }
        )


def test_universal_filters_compare_decimal_values_numerically_and_relationships_are_evidence():
    amount_filter = MetricFilter.model_validate(
        {
            "entity_type": "sales_transaction",
            "field": "amount",
            "operation": "equals",
            "value": "12.0",
        }
    )
    relationship = {
        "relationship_version_id": "relationship-1",
        "relationship_type": "customer_has_sales_transaction",
        "version": 2,
        "status": "active",
        "evidence_kind": "source_verified",
        "subject_entity_id": "customer-entity",
        "object_entity_id": "transaction-entity",
        "subject_mapping_version_id": "customer-mapping",
        "object_mapping_version_id": "transaction-mapping",
    }
    input_snapshot = _universal_input(
        [
            _record("txn-1", amount="12.00", status="posted"),
            _record("txn-2", amount="12.01", status="posted"),
        ],
        filters=[amount_filter.model_dump(mode="json")],
        evidence=[{"record_id": "txn-1"}],
        semantic_relationships=[relationship],
        external_evidence=[{"evidence_kind": "m5_metric_run"}],
    )
    result = calculate_saved_analysis(
        _universal_definition(),
        {"fiscal_year_start_month": 1, "week_start_day": 1},
        input_snapshot,
    )

    assert result["status"] == "complete"
    assert result["schema_version"] == 2
    assert result["groups"][0]["value"] == "1"
    assert result["authority_boundary"] == "descriptive_evidence_only_no_recommendation"
    assert result["calculation_authority"] == "source_observation_only"
    assert any("not a cross-source party count" in item for item in result["limitations"])
    assert result["evidence_count"] == 3
    assert len(result["evidence_hash"]) == 64


def test_formula_calculation_checks_units_and_does_not_upgrade_partial_dependencies():
    expression = {
        "op": "divide",
        "operands": [
            {"op": "metric_ref", "metric_key": "finance.actual"},
            {"op": "metric_ref", "metric_key": "finance.budget"},
        ],
    }
    definition = _formula_definition(expression=expression, unit="ratio")
    input_snapshot = _formula_input(
        [
            _dependency("finance.actual", value="10.00"),
            _dependency("finance.budget", value="4.00", status="partial"),
        ]
    )
    result = calculate_saved_analysis(definition, {}, input_snapshot)

    assert result["status"] == "abstained"
    assert result["groups"][0]["value"] == "2.50"
    assert result["calculation_authority"] == (
        "deterministic_expression_from_saved_analyses_not_independent_evidence"
    )
    assert any(
        "Formula arithmetic does not create independent source evidence" in item
        for item in result["limitations"]
    )
    assert "formula_dependency_analysis_not_complete" in result["groups"][0]["reasons"]
    assert result["evidence_count"] == 2

    mismatched = _formula_input(
        [
            _dependency("finance.actual", value="10.00", currency_code="USD"),
            _dependency("finance.budget", value="4.00", currency_code="EUR"),
        ]
    )
    mismatch_result = calculate_saved_analysis(definition, {}, mismatched)
    assert mismatch_result["status"] == "abstained"
    assert mismatch_result["groups"][0]["value"] is None
    assert "formula_unit_mismatch" in mismatch_result["reasons"]


def test_formula_supports_dimensionless_multipliers_and_rejects_structured_m5_dependencies():
    expression = {
        "op": "multiply",
        "operands": [
            {"op": "metric_ref", "metric_key": "finance.amount"},
            {"op": "metric_ref", "metric_key": "finance.factor"},
        ],
    }
    definition = _formula_definition(expression=expression, unit="USD")
    result = calculate_saved_analysis(
        definition,
        {},
        _formula_input(
            [
                _dependency("finance.amount", value="20.00"),
                _dependency("finance.factor", value="0.5", unit="percentage", currency_code=None),
            ]
        ),
    )
    assert result["status"] == "complete"
    assert result["groups"][0]["value"] == "10.00"
    assert result["groups"][0]["currency_code"] == "USD"

    with pytest.raises(ApiError) as error:
        _validate_formula_dependency_shape(
            calculation_spec={
                "kind": "m5_metric",
                "metric_id": "recorded_skill_inventory_coverage",
            },
            dependency_dimensions=[],
            dependency_time_granularity="none",
            formula_dimensions=[],
        )
    assert error.value.code == "metric_dependency_not_scalar"


def test_system_metric_definition_hash_and_typed_relationship_evidence_are_stable():
    metric = SYSTEM_METRIC_BY_KEY["m5.available_capacity_hours"]
    original_hash = system_metric_definition_hash(metric)
    assert original_hash == system_metric_definition_hash(metric)
    assert original_hash != system_metric_definition_hash(replace(metric, unit="different unit"))
    assert original_hash != system_metric_definition_hash(replace(metric, domain="finance"))
    assert original_hash != system_metric_definition_hash(
        replace(metric, description="Changed text")
    )

    evidence = MetricEvidenceReference.model_validate(
        {
            "evidence_index": 0,
            "evidence_kind": "semantic_relationship",
            "relationship_version_id": "00000000-0000-0000-0000-000000000001",
            "relationship_type": "customer_has_sales_transaction",
            "relationship_version": 1,
            "relationship_status": "active",
            "relationship_evidence_kind": "source_verified",
            "relationship_subject_entity_id": "00000000-0000-0000-0000-000000000002",
            "relationship_object_entity_id": "00000000-0000-0000-0000-000000000003",
            "subject_mapping_version_id": "00000000-0000-0000-0000-000000000004",
            "object_mapping_version_id": "00000000-0000-0000-0000-000000000005",
        }
    )
    assert evidence.evidence_kind == "semantic_relationship"
    with pytest.raises(ValidationError, match="required by their evidence_kind"):
        MetricEvidenceReference.model_validate(
            {"evidence_index": 0, "evidence_kind": "semantic_relationship"}
        )


def test_formula_expression_bounds_and_structure_remain_explicit():
    expression = FormulaExpression.model_validate(
        {
            "op": "add",
            "operands": [
                {"op": "metric_ref", "metric_key": "sales.one"},
                {"op": "constant", "value": "2.00"},
            ],
        }
    )
    assert expression.op == "add"
    with pytest.raises(ValidationError, match="exactly two operands"):
        FormulaExpression.model_validate({"op": "add", "operands": []})


@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ({"domain": "finance", "calculation": {"kind": "universal_record_count"}}, True),
        ({"domain": "sales", "calculation": {"kind": "universal_source_amount_sum"}}, True),
        ({"domain": "finance", "calculation": {"kind": "core_financial_measure"}}, True),
        ({"domain": "operations", "calculation": {"kind": "m5_metric"}}, False),
        ({"domain": "sales", "calculation": {"kind": "custom_formula"}}, False),
        (
            {
                "domain": "sales",
                "calculation": {"kind": "custom_formula"},
                "dependencies": [{"sensitivity_scope": "finance"}],
            },
            True,
        ),
    ],
)
def test_finance_sensitivity_covers_source_amounts_and_domain(
    definition: dict[str, object], expected: bool
):
    assert _definition_requires_finance_read(definition) is expected


def test_source_candidate_record_count_query_is_hard_bounded():
    session = Mock()
    session.scalar.return_value = MAX_BI_SOURCE_RECORDS + 1

    record_count = _count_source_records(
        session,
        tenant_id=uuid4(),
        company_id=uuid4(),
        source_id=uuid4(),
        entity_type="sales_transaction",
        period_start="2025-01-01",
        period_end="2025-12-31",
    )

    query = session.scalar.call_args.args[0]
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert f"LIMIT {MAX_BI_SOURCE_RECORDS + 1}" in sql
    assert "transaction_date" in sql
    assert record_count == MAX_BI_SOURCE_RECORDS + 1


def test_readiness_exposes_warnings_and_returns_conditional_status():
    readiness = _readiness_response(
        definition={"metric_key": "sales.metric", "limitations": []},
        definition_hash="a" * 64,
        metric_version=1,
        configuration_version=1,
        calculation_supported=True,
        checks=[
            MetricReadinessCheck(
                check_key="source_authority_unclassified",
                status="warning",
                message="Source authority remains unknown.",
            )
        ],
        blockers=[],
        warnings=["source_authority_unclassified"],
    )

    assert readiness.readiness_status == "conditional"
    assert readiness.warnings == ["source_authority_unclassified"]
