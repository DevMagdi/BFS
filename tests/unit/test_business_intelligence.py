from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from businessflow.business_intelligence.analysis_service import (
    MAX_BI_SOURCE_RECORDS,
    _count_source_records,
    _definition_requires_finance_read,
    _effective_dimensions,
    _project_universal_records,
    _readiness_response,
    _source_authority_requirement_failure,
    _system_definition,
    _system_metric_reference_for_calculation,
    _universal_required_fields,
    _universal_source_contract_issues,
)
from businessflow.business_intelligence.engine import calculate_saved_analysis
from businessflow.business_intelligence.governance import (
    UNRESOLVED_GOVERNANCE_BLOCKERS,
    requires_customer_data_processing_authorization,
    requires_universal_data_read,
    requires_workforce_read,
)
from businessflow.business_intelligence.models import BusinessMetricDefinitionVersion
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
from businessflow.metrics.snapshot import content_hash
from businessflow.universal_data.catalog import (
    canonical_contract_definition,
    entity_for_contract_version,
)


def test_optional_metric_formula_jsonb_fields_use_sql_null() -> None:
    for column_name in ("formula", "numerator", "denominator"):
        column = BusinessMetricDefinitionVersion.__table__.c[column_name]
        assert column.nullable
        assert column.type.none_as_null is True


def test_effective_dimensions_include_required_and_time_grain_without_duplicates() -> None:
    requested = ["week", "currency_code"]
    definition = {
        "required_dimensions": ["currency_code", "customer"],
        "time_granularity": "week",
    }

    assert _effective_dimensions(definition, requested) == [
        "week",
        "currency_code",
        "customer",
    ]


def test_versioned_universal_sales_contract_and_migration_payload_stay_in_sync() -> None:
    legacy_sales = entity_for_contract_version("sales_transaction", "1.0.0")
    current_sales = entity_for_contract_version("sales_transaction", "1.1.0")

    assert legacy_sales is not None
    assert current_sales is not None
    assert "product_code" not in legacy_sales.field_by_name
    assert current_sales.field_by_name["product_code"].required is False
    assert entity_for_contract_version("sales_transaction", "2.0.0") is None
    assert content_hash(canonical_contract_definition("1.0.0")) == (
        "5f5fef068312aaa79ef0f863d7fb4a982626053fb9f06b322224d2e71aa5bd1e"
    )

    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0025_domain_intelligence_expansion.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0025_domain_intelligence", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert json.loads(migration.UNIVERSAL_CONTRACT_DEFINITION) == canonical_contract_definition(
        "1.1.0"
    )
    assert content_hash(canonical_contract_definition("1.1.0")) == migration.UNIVERSAL_CONTRACT_HASH


def test_universal_source_contract_validation_blocks_fields_absent_from_legacy_sources():
    legacy_definition = canonical_contract_definition("1.0.0")
    legacy_hash = content_hash(legacy_definition)
    source = SimpleNamespace(
        dataset_type="universal_records",
        contract_key="businessflow.universal.business_records",
        contract_version="1.0.0",
    )
    contract = SimpleNamespace(
        dataset_type="universal_records",
        definition=legacy_definition,
        definition_hash=legacy_hash,
    )
    session = Mock()
    session.get.return_value = contract

    assert (
        _universal_source_contract_issues(
            session, source, entity_type="sales_transaction", required_fields={"amount"}
        )
        == []
    )
    assert _universal_source_contract_issues(
        session,
        source,
        entity_type="sales_transaction",
        required_fields={"product_code"},
    ) == ["required_field_not_supported_by_source_contract"]

    contract.definition_hash = "0" * 64
    assert _universal_source_contract_issues(
        session, source, entity_type="sales_transaction", required_fields=set()
    ) == ["source_contract_integrity_invalid"]
    session.get.assert_called_with(
        ANY,
        ("businessflow.universal.business_records", "1.0.0"),
    )


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


def test_product_sales_metrics_group_only_source_product_codes_without_currency_mixing():
    count_definition = _system_definition("sales.source_transaction_record_count_by_product")
    amount_definition = _system_definition("sales.source_amount_observed_by_product")
    assert count_definition is not None
    assert amount_definition is not None
    assert count_definition["required_fields"] == [
        {"entity_type": "sales_transaction", "field": "product_code"}
    ]
    assert amount_definition["required_fields"] == count_definition["required_fields"]
    assert (
        entity_for_contract_version("sales_transaction", "1.0.0").field_by_name.get("product_code")
        is None
    )

    records = []
    for record_id, product_code, amount, currency_code in (
        ("tx-1", "SKU-A", "12.00", "USD"),
        ("tx-2", "SKU-A", "5.00", "USD"),
        ("tx-3", "SKU-B", "7.50", "EUR"),
    ):
        record = _record(record_id, amount=amount, status="posted", currency_code=currency_code)
        attributes = dict(record["attributes"])
        attributes["product_code"] = product_code
        record["attributes"] = attributes
        records.append(record)
    input_snapshot = {
        "period": {"start": None, "end": None},
        "dimensions": ["product"],
        "calculation_inputs": {
            "records": records,
            "evidence": [],
            "blockers": [],
            "warnings": [],
        },
    }

    count_result = calculate_saved_analysis(count_definition, {}, input_snapshot)
    amount_result = calculate_saved_analysis(amount_definition, {}, input_snapshot)

    assert count_result["status"] == "complete"
    assert [
        (group["dimensions"]["product"], group["value"]) for group in count_result["groups"]
    ] == [("SKU-A", "2"), ("SKU-B", "1")]
    assert amount_result["status"] == "complete"
    assert [
        (group["dimensions"]["product"], group["currency_code"], group["value"])
        for group in amount_result["groups"]
    ] == [("SKU-A", "USD", "17.00"), ("SKU-B", "EUR", "7.50")]


def test_manual_commitment_count_uses_privacy_limited_snapshots_and_explicit_date_grain():
    definition = _system_definition("operations.accepted_customer_commitment_count")
    assert definition is not None
    input_snapshot = {
        "period": {"start": "2025-04-01", "end": "2025-05-31"},
        "dimensions": ["month"],
        "sensitivity_scope": "customer_commitment",
        "calculation_inputs": {
            "records": [
                {
                    "record_id": "commitment-1",
                    "entity_type": "manual_customer_commitment",
                    "attributes": {"analysis_date": "2025-04-15"},
                },
                {
                    "record_id": "commitment-2",
                    "entity_type": "manual_customer_commitment",
                    "attributes": {"analysis_date": "2025-04-20"},
                },
                {
                    "record_id": "commitment-3",
                    "entity_type": "manual_customer_commitment",
                    "attributes": {"analysis_date": "2025-05-02"},
                },
            ],
            "evidence": [
                {
                    "evidence_kind": "manual_customer_commitment",
                    "record_id": "commitment-1",
                }
            ],
            "blockers": [],
            "warnings": [],
        },
    }

    result = calculate_saved_analysis(definition, {}, input_snapshot)

    assert result["status"] == "complete"
    assert result["sensitivity_scope"] == "customer_commitment"
    assert [(group["dimensions"]["month"], group["value"]) for group in result["groups"]] == [
        ("2025-04", "2"),
        ("2025-05", "1"),
    ]
    assert "customer_name" not in str(result)
    assert "requested_delivery_date" not in str(result)


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


def test_customer_processing_guard_covers_customer_dimensions_fields_and_filters():
    transaction_definition = {
        "domain": "sales",
        "calculation": {"kind": "universal_record_count", "entity_type": "sales_transaction"},
    }
    assert requires_customer_data_processing_authorization(
        transaction_definition, requested_dimensions=["customer"]
    )
    assert requires_customer_data_processing_authorization(
        transaction_definition,
        analysis_filters=[{"field": "customer_code"}],
    )
    assert requires_customer_data_processing_authorization(
        {
            "domain": "sales",
            "calculation": {
                "kind": "universal_distinct_source_values",
                "entity_type": "sales_transaction",
                "field": "customer_code",
            },
        }
    )
    assert requires_customer_data_processing_authorization(
        {"domain": "customers", "calculation": {"kind": "universal_record_count"}}
    )
    assert requires_customer_data_processing_authorization(
        {"required_relationships": ["customer_has_sales_transaction"]}
    )
    assert not requires_customer_data_processing_authorization(transaction_definition)


def test_universal_data_read_guard_covers_calculations_sources_and_relationships():
    for calculation_kind in (
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    ):
        assert requires_universal_data_read({"calculation": {"kind": calculation_kind}})
    assert requires_universal_data_read(
        {"required_source_datasets": [{"dataset_type": "universal_records"}]}
    )
    assert requires_universal_data_read(
        {"required_relationships": ["customer_has_sales_transaction"]}
    )
    assert requires_universal_data_read({"dependencies": [{"sensitivity_scope": "universal_data"}]})
    assert not requires_universal_data_read(
        {"calculation": {"kind": "m5_metric"}, "domain": "people"}
    )
    assert not requires_universal_data_read({"calculation": {"kind": "custom_formula"}})


def test_workforce_guard_covers_m5_people_and_pinned_dependencies():
    assert requires_workforce_read({"domain": "people"})
    assert requires_workforce_read({"dimensions": ["employee"]})
    assert requires_workforce_read({"domain": "operations"}, requested_dimensions=["employee"])
    assert requires_workforce_read({"calculation": {"kind": "m5_metric"}})
    assert requires_workforce_read(
        {"dependencies": [{"metric_key": "m5.available_capacity_hours"}]}
    )
    assert requires_workforce_read({"required_relationships": ["project_managed_by_employee"]})
    assert not requires_workforce_read(
        {"domain": "operations", "calculation": {"kind": "universal_record_count"}}
    )


def test_universal_input_projection_keeps_only_calculation_scope_fields():
    definition = {
        "calculation": {"kind": "universal_source_amount_sum", "entity_type": "sales_transaction"},
        "required_fields": [],
        "filters": [],
        "exclusions": [],
    }
    required_fields = _universal_required_fields(
        definition,
        dimensions=["customer", "month"],
        analysis_filters=[{"field": "status"}],
        period_scoped=True,
    )
    assert required_fields == {
        "amount",
        "currency_code",
        "customer_code",
        "status",
        "transaction_date",
    }

    records = [
        {
            "record_id": "source-record",
            "attributes": {
                "amount": "12.30",
                "currency_code": "USD",
                "customer_code": "source-customer",
                "status": "posted",
                "transaction_date": "2025-01-15",
                "description": "unrequested data",
            },
        }
    ]
    projected = _project_universal_records(records, required_fields)
    assert projected[0]["record_id"] == "source-record"
    assert projected[0]["attributes"] == {
        "amount": "12.30",
        "currency_code": "USD",
        "customer_code": "source-customer",
        "status": "posted",
        "transaction_date": "2025-01-15",
    }


def test_readiness_exposes_warnings_and_returns_conditional_status():
    readiness = _readiness_response(
        tenant_id=uuid4(),
        company_id=uuid4(),
        domain="sales",
        definition={"metric_key": "sales.metric", "limitations": []},
        definition_hash="a" * 64,
        metric_version=1,
        configuration_version=1,
        analysis_id=None,
        period_start=None,
        period_end=None,
        dimensions=["month"],
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
    assert readiness.domain == "sales"
    assert readiness.analysis_id is None
    assert readiness.dimensions == ["month"]
    assert readiness.governance_blockers == list(UNRESOLVED_GOVERNANCE_BLOCKERS)
    assert readiness.retention_status == "unresolved"
    assert readiness.warnings == ["source_authority_unclassified"]
