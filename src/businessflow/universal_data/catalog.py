from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

CanonicalFieldType = Literal["text", "date", "decimal"]


@dataclass(frozen=True, slots=True)
class CanonicalField:
    name: str
    field_type: CanonicalFieldType
    required: bool
    description: str


@dataclass(frozen=True, slots=True)
class CanonicalEntity:
    entity_type: str
    label: str
    key_field: str
    description: str
    fields: tuple[CanonicalField, ...]

    @property
    def field_by_name(self) -> dict[str, CanonicalField]:
        return {field.name: field for field in self.fields}

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.required)


UNIVERSAL_DATASET_TYPE = "universal_records"
UNIVERSAL_CONTRACT_KEY = "businessflow.universal.business_records"
LEGACY_UNIVERSAL_CONTRACT_VERSION = "1.0.0"
UNIVERSAL_CONTRACT_VERSION = "1.1.0"
SUPPORTED_UNIVERSAL_CONTRACT_VERSIONS = frozenset(
    {LEGACY_UNIVERSAL_CONTRACT_VERSION, UNIVERSAL_CONTRACT_VERSION}
)
VALIDATION_RULE_VERSION = "universal-business-records/1.1.0"

CANONICAL_ENTITIES: dict[str, CanonicalEntity] = {
    "customer": CanonicalEntity(
        entity_type="customer",
        label="Customer",
        key_field="customer_code",
        description=(
            "A source-scoped customer reference; not proof of customer identity or authority."
        ),
        fields=(
            CanonicalField("customer_code", "text", True, "Stable code used by this source."),
            CanonicalField("name", "text", True, "Customer name as supplied by the source."),
            CanonicalField(
                "tax_identifier", "text", False, "Optional tax or registration identifier."
            ),
            CanonicalField("email", "text", False, "Optional contact email."),
            CanonicalField("phone", "text", False, "Optional contact phone."),
            CanonicalField("country", "text", False, "Optional country or jurisdiction."),
            CanonicalField("customer_segment", "text", False, "Optional source-defined segment."),
        ),
    ),
    "supplier": CanonicalEntity(
        entity_type="supplier",
        label="Supplier",
        key_field="supplier_code",
        description=(
            "A source-scoped supplier reference; supplier identity is not cross-matched here."
        ),
        fields=(
            CanonicalField("supplier_code", "text", True, "Stable code used by this source."),
            CanonicalField("name", "text", True, "Supplier name as supplied by the source."),
            CanonicalField(
                "tax_identifier", "text", False, "Optional tax or registration identifier."
            ),
            CanonicalField("email", "text", False, "Optional contact email."),
            CanonicalField("phone", "text", False, "Optional contact phone."),
            CanonicalField("country", "text", False, "Optional country or jurisdiction."),
            CanonicalField(
                "payment_terms", "text", False, "Optional source-defined payment terms."
            ),
        ),
    ),
    "product": CanonicalEntity(
        entity_type="product",
        label="Product or service",
        key_field="product_code",
        description=(
            "A source-scoped product or service reference; pricing is not a financial fact."
        ),
        fields=(
            CanonicalField("product_code", "text", True, "Stable code used by this source."),
            CanonicalField("name", "text", True, "Product or service name."),
            CanonicalField("description", "text", False, "Optional source description."),
            CanonicalField("category", "text", False, "Optional source-defined category."),
            CanonicalField("unit_of_measure", "text", False, "Optional unit label."),
        ),
    ),
    "sales_transaction": CanonicalEntity(
        entity_type="sales_transaction",
        label="Sales transaction reference",
        key_field="transaction_reference",
        description=(
            "A source-supplied transaction record, retained outside Core Financials "
            "until separately qualified."
        ),
        fields=(
            CanonicalField(
                "transaction_reference", "text", True, "Stable transaction reference in the source."
            ),
            CanonicalField("transaction_date", "date", True, "Transaction date as supplied."),
            CanonicalField("customer_code", "text", False, "Optional source customer reference."),
            CanonicalField("product_code", "text", False, "Optional source product reference."),
            CanonicalField(
                "currency_code", "text", True, "Currency code supplied with the amount."
            ),
            CanonicalField(
                "amount", "decimal", True, "Source amount; no currency conversion is applied."
            ),
            CanonicalField("status", "text", False, "Optional source-defined transaction status."),
            CanonicalField("description", "text", False, "Optional source description."),
        ),
    ),
    "expense": CanonicalEntity(
        entity_type="expense",
        label="Expense reference",
        key_field="expense_reference",
        description=(
            "A source-supplied expense record, not an accounting entry or Core Financials measure."
        ),
        fields=(
            CanonicalField(
                "expense_reference", "text", True, "Stable expense reference in the source."
            ),
            CanonicalField("expense_date", "date", True, "Expense date as supplied."),
            CanonicalField("category", "text", True, "Source-defined expense category."),
            CanonicalField(
                "currency_code", "text", True, "Currency code supplied with the amount."
            ),
            CanonicalField(
                "amount", "decimal", True, "Source amount; no currency conversion is applied."
            ),
            CanonicalField("supplier_code", "text", False, "Optional source supplier reference."),
            CanonicalField("description", "text", False, "Optional source description."),
        ),
    ),
    "business_activity": CanonicalEntity(
        entity_type="business_activity",
        label="Business activity",
        key_field="activity_reference",
        description="A source-scoped operational activity; it does not assert a financial event.",
        fields=(
            CanonicalField(
                "activity_reference", "text", True, "Stable activity reference in the source."
            ),
            CanonicalField("activity_date", "date", True, "Date associated with the activity."),
            CanonicalField("activity_type", "text", True, "Source-defined activity type."),
            CanonicalField("description", "text", True, "Activity description as supplied."),
            CanonicalField("status", "text", False, "Optional source-defined status."),
        ),
    ),
    "operational_record": CanonicalEntity(
        entity_type="operational_record",
        label="Operational record",
        key_field="record_reference",
        description=(
            "A source-scoped operational reference with no implied business interpretation."
        ),
        fields=(
            CanonicalField(
                "record_reference", "text", True, "Stable record reference in the source."
            ),
            CanonicalField("record_date", "date", True, "Date associated with the record."),
            CanonicalField("record_type", "text", True, "Source-defined record type."),
            CanonicalField("description", "text", True, "Description as supplied by the source."),
            CanonicalField("status", "text", False, "Optional source-defined status."),
        ),
    ),
}


def entity_for_contract_version(entity_type: str, version: str) -> CanonicalEntity | None:
    if version not in SUPPORTED_UNIVERSAL_CONTRACT_VERSIONS:
        return None
    entity = CANONICAL_ENTITIES.get(entity_type)
    if entity is None:
        return None
    if version == LEGACY_UNIVERSAL_CONTRACT_VERSION and entity_type == "sales_transaction":
        return replace(
            entity, fields=tuple(field for field in entity.fields if field.name != "product_code")
        )
    return entity


def canonical_contract_definition(
    version: str = UNIVERSAL_CONTRACT_VERSION,
) -> dict[str, object]:
    if version not in SUPPORTED_UNIVERSAL_CONTRACT_VERSIONS:
        raise ValueError("the Universal Data contract version is unsupported")
    entities = [
        entity_for_contract_version(entity_type, version) for entity_type in CANONICAL_ENTITIES
    ]
    return {
        "dataset_type": UNIVERSAL_DATASET_TYPE,
        "source_kind": "universal_business_data",
        "version": version,
        "entities": [
            {
                "entity_type": entity.entity_type,
                "label": entity.label,
                "key_field": entity.key_field,
                "description": entity.description,
                "fields": [
                    {
                        "name": field.name,
                        "type": field.field_type,
                        "required": field.required,
                        "description": field.description,
                    }
                    for field in entity.fields
                ],
            }
            for entity in entities
            if entity is not None
        ],
        "promotion_boundary": (
            "Accepted source records are not automatically financial facts or "
            "Core Financials inputs."
        ),
    }


def entity_schema(
    entity_type: str,
    contract_version: str = UNIVERSAL_CONTRACT_VERSION,
) -> dict[str, object] | None:
    entity = entity_for_contract_version(entity_type, contract_version)
    if entity is None:
        return None
    return {
        "entity_type": entity.entity_type,
        "label": entity.label,
        "description": entity.description,
        "key_field": entity.key_field,
        "fields": [
            {
                "name": field.name,
                "type": field.field_type,
                "required": field.required,
                "description": field.description,
            }
            for field in entity.fields
        ],
    }
