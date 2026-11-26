from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from businessflow.data_imports.catalog import DATASETS
from businessflow.data_imports.schemas import ImportDataset
from businessflow.metrics.snapshot import content_hash

CONTRACT_VERSION = "1.0.0"
CONTRACT_KEY_PREFIX = "businessflow.m4"
MANUAL_COMMITMENT_DATASET = "customer_commitments"
MANUAL_COMMITMENT_CONTRACT_KEY = "businessflow.manual.customer_commitments"
MANUAL_COMMITMENT_CONTRACT_VERSION = "1.0.0"
MANUAL_COMMITMENT_CONTRACT_DEFINITION: dict[str, object] = {
    "dataset_type": MANUAL_COMMITMENT_DATASET,
    "source_kind": "manual_entry",
    "fields": [
        {
            "name": "customer_name",
            "required": True,
            "description": "Customer name as entered by a Company member.",
        },
        {
            "name": "customer_reference",
            "required": False,
            "description": "Optional customer-provided order or reference number.",
        },
        {
            "name": "order_date",
            "required": True,
            "description": "Date the customer commitment is recorded as made.",
        },
        {
            "name": "requested_delivery_date",
            "required": False,
            "description": "Optional requested fulfillment date; must not precede order date.",
        },
        {
            "name": "summary",
            "required": True,
            "description": "Short description of the customer commitment or requested work.",
        },
        {
            "name": "notes",
            "required": False,
            "description": "Optional additional context; not an accounting or legal record.",
        },
    ],
    "required": ["customer_name", "order_date", "summary"],
    "required_one_of": [],
    "mutually_exclusive": [],
    "defaults": [],
}
MANUAL_COMMITMENT_CONTRACT_HASH = content_hash(MANUAL_COMMITMENT_CONTRACT_DEFINITION)


def contract_key(dataset_type: ImportDataset | str) -> str:
    if dataset_type not in DATASETS:
        raise ValueError("unsupported M4 dataset type")
    return f"{CONTRACT_KEY_PREFIX}.{dataset_type}"


def contract_definition(dataset_type: ImportDataset | str) -> dict[str, object]:
    """Project the current M4 mapping contract into a stable, JSON-compatible definition."""
    if dataset_type not in DATASETS:
        raise ValueError("unsupported M4 dataset type")
    spec = DATASETS[dataset_type]
    return {
        "dataset_type": dataset_type,
        "fields": [
            {
                "name": field.name,
                "required": field.name in spec.required,
                "description": field.description,
                "aliases": list(field.aliases),
            }
            for field in spec.fields
        ],
        "required": sorted(spec.required),
        "required_one_of": [sorted(group) for group in spec.required_one_of],
        "mutually_exclusive": [sorted(group) for group in spec.mutually_exclusive],
        "defaults": list(spec.defaults),
    }


def contract_payload(dataset_type: ImportDataset | str) -> dict[str, object]:
    definition = contract_definition(dataset_type)
    return {
        "contract_key": contract_key(dataset_type),
        "version": CONTRACT_VERSION,
        "dataset_type": dataset_type,
        "definition": definition,
        "definition_hash": content_hash(definition),
    }


def validate_import_mapping(
    import_record: object, definition: Mapping[str, object]
) -> list[dict[str, object]]:
    """Check that an applied M4 import still conforms to the referenced mapping contract."""
    findings: list[dict[str, object]] = []
    dataset_type = getattr(import_record, "dataset_type", None)
    if dataset_type != definition.get("dataset_type"):
        return [
            {
                "code": "contract_dataset_mismatch",
                "severity": "error",
                "message": "The M4 import dataset does not match the registered contract.",
            }
        ]

    fields_value = definition.get("fields")
    fields = {
        str(item.get("name"))
        for item in cast(list[object], fields_value or [])
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    mapping_value = getattr(import_record, "field_mapping", None)
    mapping = mapping_value if isinstance(mapping_value, Mapping) else {}
    mapped_fields = {str(name) for name in mapping}

    unexpected = sorted(mapped_fields - fields)
    if unexpected:
        findings.append(
            {
                "code": "contract_unknown_fields",
                "severity": "error",
                "fields": unexpected,
                "message": "The saved M4 mapping includes fields outside this contract.",
            }
        )

    required_value = definition.get("required")
    required = {str(value) for value in cast(list[object], required_value or [])}
    missing = sorted(required - mapped_fields)
    if missing:
        findings.append(
            {
                "code": "contract_required_fields_missing",
                "severity": "error",
                "fields": missing,
                "message": "The saved M4 mapping omits required contract fields.",
            }
        )

    for group in cast(list[object], definition.get("required_one_of") or []):
        choices = {str(value) for value in cast(list[object], group)}
        if choices.isdisjoint(mapped_fields):
            findings.append(
                {
                    "code": "contract_required_choice_missing",
                    "severity": "error",
                    "fields": sorted(choices),
                    "message": "The saved M4 mapping omits every field in a required choice group.",
                }
            )

    for group in cast(list[object], definition.get("mutually_exclusive") or []):
        choices = {str(value) for value in cast(list[object], group)}
        selected = sorted(choices & mapped_fields)
        if len(selected) > 1:
            findings.append(
                {
                    "code": "contract_mutually_exclusive_fields",
                    "severity": "error",
                    "fields": selected,
                    "message": "The saved M4 mapping selects mutually exclusive contract fields.",
                }
            )

    inspection = getattr(import_record, "inspection", None)
    worksheets = inspection.get("worksheets") if isinstance(inspection, Mapping) else None
    selected_worksheet = getattr(import_record, "selected_worksheet", None)
    sheet = next(
        (
            item
            for item in cast(list[object], worksheets or [])
            if isinstance(item, Mapping) and item.get("name") == selected_worksheet
        ),
        None,
    )
    headers_value = sheet.get("headers") if isinstance(sheet, Mapping) else None
    headers = {str(value) for value in cast(list[object], headers_value or [])}
    if sheet is None:
        findings.append(
            {
                "code": "contract_worksheet_missing",
                "severity": "error",
                "message": "The selected M4 worksheet is absent from its saved inspection.",
            }
        )
    else:
        mapped_headers = [str(value) for value in mapping.values() if isinstance(value, str)]
        missing_headers = sorted(set(mapped_headers) - headers)
        if missing_headers:
            findings.append(
                {
                    "code": "contract_source_headers_missing",
                    "severity": "error",
                    "headers": missing_headers,
                    "message": "The saved M4 mapping refers to headers absent from its inspection.",
                }
            )
        if len(mapped_headers) != len(set(mapped_headers)):
            findings.append(
                {
                    "code": "contract_source_header_reused",
                    "severity": "error",
                    "message": "A source header is mapped to more than one contract field.",
                }
            )
        ignored_value = getattr(import_record, "ignored_columns", [])
        ignored = {str(value) for value in cast(list[object], ignored_value or [])}
        if ignored & set(mapped_headers):
            findings.append(
                {
                    "code": "contract_ignored_header_mapped",
                    "severity": "error",
                    "headers": sorted(ignored & set(mapped_headers)),
                    "message": "A source header cannot be both mapped and explicitly ignored.",
                }
            )
    return findings
