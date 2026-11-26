from __future__ import annotations

import pytest
from pydantic import ValidationError

from businessflow.business_semantic.catalog import (
    SEMANTIC_ENTITIES,
    SEMANTIC_RELATIONSHIPS,
    entity_catalog_payload,
)
from businessflow.business_semantic.schemas import (
    SemanticCatalogResponse,
    SemanticEntityCreateRequest,
    SemanticMappingCreateRequest,
    SemanticRelationshipCreateRequest,
)
from businessflow.universal_data.catalog import CANONICAL_ENTITIES
from businessflow.universal_data.normalization import normalize_field


def test_semantic_catalog_is_bounded_and_discloses_identity_limits():
    catalog = SemanticCatalogResponse.model_validate(entity_catalog_payload())

    assert catalog.catalog_version == "businessflow.semantic.v1"
    assert {item["entity_type"] for item in catalog.entities} == set(SEMANTIC_ENTITIES)
    assert {item["relationship_type"] for item in catalog.relationships} == set(
        SEMANTIC_RELATIONSHIPS
    )
    assert (
        next(item for item in catalog.entities if item["entity_type"] == "company")[
            "user_creatable"
        ]
        is False
    )
    assert "No name, email, or tax identifier is automatically merged" in catalog.note


def test_semantic_requests_normalize_review_text_and_require_source_evidence():
    entity = SemanticEntityCreateRequest(
        entity_type="customer",
        entity_key="customer-1",
        display_name="  Acme  ",
    )
    mapping = SemanticMappingCreateRequest(
        reference_kind="employee",
        reference_id="00000000-0000-0000-0000-000000000001",
        semantic_entity_id="00000000-0000-0000-0000-000000000002",
        expected_version=0,
        review_note="  Human reviewed the employee profile  ",
    )

    assert entity.display_name == "Acme"
    assert mapping.review_note == "Human reviewed the employee profile"
    with pytest.raises(ValidationError):
        SemanticEntityCreateRequest(
            entity_type="customer",
            entity_key="customer-blank",
            display_name="   ",
        )
    with pytest.raises(ValidationError):
        SemanticRelationshipCreateRequest(
            subject_entity_id="00000000-0000-0000-0000-000000000001",
            relationship_type="employee_belongs_to_department",
            object_entity_id="00000000-0000-0000-0000-000000000002",
            expected_version=0,
            evidence_kind="source_verified",
            evidence_note="The source mapping is required",
        )


def test_sales_relationship_source_fields_normalize_with_explicit_decimal_policy():
    sales_entity = CANONICAL_ENTITIES["sales_transaction"]
    date_value, date_findings = normalize_field(
        "2025-01-15", sales_entity.field_by_name["transaction_date"], {}
    )
    amount_value, amount_findings = normalize_field(
        "120.00",
        sales_entity.field_by_name["amount"],
        {"decimal_separator": "."},
    )

    assert date_value == "2025-01-15"
    assert amount_value == "120.00"
    assert date_findings == amount_findings == []
