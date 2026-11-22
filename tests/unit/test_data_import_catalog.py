from __future__ import annotations

import pytest

from businessflow.data_imports.catalog import DATASETS, field_definitions, suggest_mapping


@pytest.mark.parametrize("dataset_type", DATASETS)
def test_dataset_catalog_has_unique_fields_and_truthful_required_flags(dataset_type: str) -> None:
    spec = DATASETS[dataset_type]
    definitions = field_definitions(dataset_type)  # type: ignore[arg-type]
    names = {definition.name for definition in definitions}

    assert len(names) == len(definitions)
    assert spec.required <= names
    assert {definition.name for definition in definitions if definition.required} == set(
        spec.required
    )
    for alternative_group in spec.required_one_of:
        assert alternative_group <= names
        assert all(
            not next(item for item in definitions if item.name == name).required
            for name in alternative_group
            if name not in spec.required
        )


def test_mapping_suggestions_are_unique_and_leave_ambiguous_headers_for_review() -> None:
    suggested = suggest_mapping(
        ["Employee Code", "employee_code", "Full Name", "Job Title"], "employees"
    )

    assert "employee_code" not in suggested
    assert suggested["full_name"] == "Full Name"
    assert suggested["job_title"] == "Job Title"
    assert len(set(suggested.values())) == len(suggested)
