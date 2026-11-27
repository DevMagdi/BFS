from __future__ import annotations

from collections.abc import Iterable, Mapping

# These controls are intentionally unresolved. They describe approval boundaries; no
# source profile, record volume, role, or calculation is treated as an approval.
UNRESOLVED_GOVERNANCE_BLOCKERS: tuple[str, ...] = (
    "source_authority_approval_unresolved",
    "source_completeness_approval_unresolved",
    "independent_control_provenance_unresolved",
    "finance_accounting_policy_approval_unresolved",
    "legal_retention_policy_unresolved",
    "customer_data_processing_authorization_unresolved",
)
RETENTION_STATUS = "unresolved"

_CUSTOMER_DATA_FIELDS = frozenset(
    {
        "customer_code",
        "customer_segment",
        "email",
        "phone",
        "tax_identifier",
        "name",
        "description",
    }
)
_CUSTOMER_DATA_DIMENSIONS = frozenset({"customer", "customer_segment"})
_CUSTOMER_DATA_RELATIONSHIPS = frozenset({"customer_has_sales_transaction"})
_WORKFORCE_RELATIONSHIPS = frozenset(
    {
        "employee_belongs_to_department",
        "project_assigned_to_employee",
        "project_managed_by_employee",
    }
)
_UNIVERSAL_DATA_CALCULATIONS = frozenset(
    {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    }
)
_CUSTOMER_COMMITMENT_CALCULATIONS = frozenset({"manual_customer_commitment_count"})
_UNIVERSAL_DATA_RELATIONSHIPS = frozenset(
    {
        "customer_has_sales_transaction",
        "product_has_sales_transaction",
        "supplier_has_expense",
    }
)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def requires_customer_data_processing_authorization(
    definition: Mapping[str, object],
    *,
    requested_dimensions: Iterable[str] = (),
    analysis_filters: Iterable[Mapping[str, object]] = (),
) -> bool:
    """Return true when an analysis would use customer data without an approval state."""
    if definition.get("domain") == "customers":
        return True
    calculation = _mapping(definition.get("calculation"))
    if (
        calculation.get("entity_type") == "customer"
        or calculation.get("field") in _CUSTOMER_DATA_FIELDS
    ):
        return True
    dimensions = set(requested_dimensions)
    if dimensions & _CUSTOMER_DATA_DIMENSIONS:
        return True
    relationships = {
        item
        for item in _items(definition.get("required_relationships", []))
        if isinstance(item, str)
    }
    if relationships & _CUSTOMER_DATA_RELATIONSHIPS:
        return True

    field_requirements = _items(definition.get("required_fields", []))
    filters = [
        *_items(definition.get("filters", [])),
        *_items(definition.get("exclusions", [])),
        *analysis_filters,
    ]
    for item in field_requirements:
        if _mapping(item).get("field") in _CUSTOMER_DATA_FIELDS:
            return True
    for item in filters:
        if _mapping(item).get("field") in _CUSTOMER_DATA_FIELDS:
            return True

    for dependency in _items(definition.get("dependencies", [])):
        if _mapping(dependency).get("customer_data_processing_required") is True:
            return True
    return False


def requires_customer_commitment_read(definition: Mapping[str, object]) -> bool:
    """Return true when a definition reads manual customer-commitment evidence."""
    calculation = _mapping(definition.get("calculation"))
    calculation_kind = calculation.get("kind", definition.get("calculation_key"))
    if calculation_kind in _CUSTOMER_COMMITMENT_CALCULATIONS:
        return True

    required_sources = _items(definition.get("required_source_datasets", []))
    for item in required_sources:
        if _mapping(item).get("dataset_type") == "customer_commitments":
            return True

    for dependency in _items(definition.get("dependencies", [])):
        if _mapping(dependency).get("customer_commitment_read_required") is True:
            return True
    return False


def requires_universal_data_read(definition: Mapping[str, object]) -> bool:
    """Return true when a definition consumes Universal Data or source-backed party links."""
    calculation = _mapping(definition.get("calculation"))
    calculation_kind = calculation.get("kind", definition.get("calculation_key"))
    if calculation_kind in _UNIVERSAL_DATA_CALCULATIONS:
        return True

    required_sources = _items(definition.get("required_source_datasets", []))
    for item in required_sources:
        if _mapping(item).get("dataset_type") == "universal_records":
            return True

    relationships = {
        item
        for item in _items(definition.get("required_relationships", []))
        if isinstance(item, str)
    }
    if relationships & _UNIVERSAL_DATA_RELATIONSHIPS:
        return True

    for dependency in _items(definition.get("dependencies", [])):
        item = _mapping(dependency)
        if (
            item.get("universal_data_required") is True
            or item.get("sensitivity_scope") == "universal_data"
        ):
            return True
    return False


def requires_workforce_read(
    definition: Mapping[str, object], *, requested_dimensions: Iterable[str] = ()
) -> bool:
    """Protect M5, People-domain, employee-level, and explicitly linked workforce evidence."""
    if definition.get("domain") == "people":
        return True
    dimensions = set(requested_dimensions)
    dimensions.update(
        item for item in _items(definition.get("dimensions", [])) if isinstance(item, str)
    )
    if "employee" in dimensions:
        return True
    calculation = _mapping(definition.get("calculation"))
    if calculation.get("kind") == "m5_metric":
        return True
    relationships = {
        item
        for item in _items(definition.get("required_relationships", []))
        if isinstance(item, str)
    }
    if relationships & _WORKFORCE_RELATIONSHIPS:
        return True
    for dependency in _items(definition.get("dependencies", [])):
        item = _mapping(dependency)
        metric_key = item.get("metric_key")
        if item.get("workforce_sensitive") is True or (
            isinstance(metric_key, str) and metric_key.startswith("m5.")
        ):
            return True
    return False
