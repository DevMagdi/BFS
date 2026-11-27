from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SemanticDomain = Literal["organization", "sales", "procurement", "operations"]


@dataclass(frozen=True, slots=True)
class SemanticEntityDefinition:
    entity_type: str
    label: str
    domain: SemanticDomain
    description: str
    reference_kinds: tuple[str, ...]
    evidence_boundary: str


@dataclass(frozen=True, slots=True)
class SemanticRelationshipDefinition:
    relationship_type: str
    label: str
    subject_type: str
    object_type: str
    evidence_rule: str
    analytic_use: bool


SEMANTIC_CATALOG_VERSION = "businessflow.semantic.v2"

# These concepts are backed by the existing Company Context and Universal Data models.
# Invoice, payment, account, service, and other concepts are intentionally not advertised
# as independent entities until a supported source contract exists.
SEMANTIC_ENTITIES: dict[str, SemanticEntityDefinition] = {
    "company": SemanticEntityDefinition(
        "company",
        "Company",
        "organization",
        "The Company selected from the authenticated tenant context.",
        (),
        "Company is the scope boundary and is not a user-created semantic identity.",
    ),
    "business_unit": SemanticEntityDefinition(
        "business_unit",
        "Business unit",
        "organization",
        "An existing Company Context business unit.",
        ("business_unit",),
        "A Company Context reference; it does not imply consolidated reporting authority.",
    ),
    "department": SemanticEntityDefinition(
        "department",
        "Department",
        "organization",
        "An existing Company Context department.",
        ("department",),
        "A Company Context reference; it does not imply cost-center or reporting policy.",
    ),
    "employee": SemanticEntityDefinition(
        "employee",
        "Employee",
        "organization",
        "An existing Company Context employee.",
        ("employee",),
        "An employee profile is not evidence of actual work, productivity, or utilization.",
    ),
    "project": SemanticEntityDefinition(
        "project",
        "Project",
        "operations",
        "An existing Company Context project.",
        ("project",),
        "Project status and assignments do not establish delivery or financial outcomes.",
    ),
    "customer": SemanticEntityDefinition(
        "customer",
        "Customer reference",
        "sales",
        "A source-scoped Universal Data customer reference.",
        ("universal_record",),
        "A semantic identity can group source references only after explicit human review.",
    ),
    "supplier": SemanticEntityDefinition(
        "supplier",
        "Supplier reference",
        "procurement",
        "A source-scoped Universal Data supplier reference.",
        ("universal_record",),
        "A semantic identity can group source references only after explicit human review.",
    ),
    "product": SemanticEntityDefinition(
        "product",
        "Product or service reference",
        "sales",
        "A source-scoped Universal Data product or service reference.",
        ("universal_record",),
        "The source reference does not establish price, margin, or sales attribution.",
    ),
    "sales_transaction": SemanticEntityDefinition(
        "sales_transaction",
        "Sales transaction reference",
        "sales",
        "A source-scoped Universal Data transaction reference.",
        ("universal_record",),
        "A source amount is not an approved revenue measure or accounting fact.",
    ),
    "expense": SemanticEntityDefinition(
        "expense",
        "Expense reference",
        "procurement",
        "A source-scoped Universal Data expense reference.",
        ("universal_record",),
        "A source amount is not an accounting expense or approved financial measure.",
    ),
    "business_activity": SemanticEntityDefinition(
        "business_activity",
        "Business activity reference",
        "operations",
        "A source-scoped Universal Data activity reference.",
        ("universal_record",),
        "A source activity is not proof of completed work or service quality.",
    ),
    "operational_record": SemanticEntityDefinition(
        "operational_record",
        "Operational record",
        "operations",
        "A source-scoped Universal Data operational record.",
        ("universal_record",),
        "A source record has no additional business interpretation by default.",
    ),
}

# Only source fields and Company Context relationships that can be checked against
# existing stored records are eligible for source_verified status. All other links must
# remain explicit manual assertions and are not analytically usable.
SEMANTIC_RELATIONSHIPS: dict[str, SemanticRelationshipDefinition] = {
    "customer_has_sales_transaction": SemanticRelationshipDefinition(
        "customer_has_sales_transaction",
        "Customer has sales transaction",
        "customer",
        "sales_transaction",
        "Same-source customer_code equals the transaction customer_code.",
        True,
    ),
    "product_has_sales_transaction": SemanticRelationshipDefinition(
        "product_has_sales_transaction",
        "Product or service has a sales transaction",
        "product",
        "sales_transaction",
        "Same-source product_code equals the transaction product_code.",
        True,
    ),
    "supplier_has_expense": SemanticRelationshipDefinition(
        "supplier_has_expense",
        "Supplier has expense",
        "supplier",
        "expense",
        "Same-source supplier_code equals the expense supplier_code.",
        True,
    ),
    "employee_belongs_to_department": SemanticRelationshipDefinition(
        "employee_belongs_to_department",
        "Employee belongs to department",
        "employee",
        "department",
        "Employee.department_id references the Company Context department.",
        True,
    ),
    "department_in_business_unit": SemanticRelationshipDefinition(
        "department_in_business_unit",
        "Department belongs to business unit",
        "department",
        "business_unit",
        "Department.business_unit_id references the Company Context business unit.",
        True,
    ),
    "project_assigned_to_employee": SemanticRelationshipDefinition(
        "project_assigned_to_employee",
        "Project has an assigned employee",
        "project",
        "employee",
        "A committed or completed Company Context resource assignment exists; "
        "proposed assignments do not verify an allocation.",
        True,
    ),
    "project_managed_by_employee": SemanticRelationshipDefinition(
        "project_managed_by_employee",
        "Project is managed by employee",
        "project",
        "employee",
        "Project.project_manager_employee_id references the Company Context employee.",
        True,
    ),
}


def entity_catalog_payload() -> dict[str, object]:
    return {
        "catalog_version": SEMANTIC_CATALOG_VERSION,
        "entities": [
            {
                "entity_type": item.entity_type,
                "label": item.label,
                "domain": item.domain,
                "description": item.description,
                "reference_kinds": list(item.reference_kinds),
                "evidence_boundary": item.evidence_boundary,
                "user_creatable": item.entity_type != "company",
            }
            for item in SEMANTIC_ENTITIES.values()
        ],
        "relationships": [
            {
                "relationship_type": item.relationship_type,
                "label": item.label,
                "subject_type": item.subject_type,
                "object_type": item.object_type,
                "evidence_rule": item.evidence_rule,
                "source_verification_supported": True,
                "analytic_use_requires_source_verification": item.analytic_use,
            }
            for item in SEMANTIC_RELATIONSHIPS.values()
        ],
        "note": (
            "Semantic identities and links are Company-scoped. Source-backed links are verified "
            "only against the cited stored relationship; manual assertions are not analytical "
            "evidence. No name, email, or tax identifier is automatically merged."
        ),
    }
