from __future__ import annotations

import importlib.util
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from businessflow.company_data.contracts import contract_payload, validate_import_mapping
from businessflow.company_data.quality import (
    assess_quality,
    evaluate_freshness,
    m10_warning_codes_are_compatible,
)

EXPECTED_CONTRACT_HASHES = {
    "employees": "87f2d047afcf9eab7e6cd0b59ef7bd4df8e513702fb0ff6bed98d63dc6039e3e",
    "skills": "8c3957e93d1d079f35ff0c3c060160f5a7d6f5cb5415688ddd200b171ec6b148",
    "employee_skills": "e574e14d4da9a933a2c99acfc9587f03efa063af7564e67a62f09fdf2cf53c8b",
    "weekly_capacity": "06a4f990a9dac01318771d46fde3b6b09e16cc036de36e79a16ccd3a873846d7",
    "projects": "c900d9eed5fd0c71c1983f9c0668778e13491e943abd338c96388b7a25961ad3",
    "resource_assignments": "fe005b970834ca1066df209c82a31bc2ce8248c45139bba5d204d379a2b740cc",
}


def test_contract_definitions_are_versioned_and_require_an_explicit_version_bump_on_drift():
    actual = {
        dataset: contract_payload(dataset)["definition_hash"]
        for dataset in EXPECTED_CONTRACT_HASHES
    }
    assert actual == EXPECTED_CONTRACT_HASHES
    assert all(contract_payload(dataset)["version"] == "1.0.0" for dataset in actual)


def test_migration_seed_is_identical_to_the_versioned_m4_contract_catalog():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0009_company_data_intelligence.py"
    )
    spec = importlib.util.spec_from_file_location("m12_company_data_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    seed_by_dataset = {
        dataset: (json.loads(encoded_definition), definition_hash)
        for _, _, dataset, encoded_definition, definition_hash in migration.CONTRACT_SEEDS
    }
    assert set(seed_by_dataset) == set(EXPECTED_CONTRACT_HASHES)
    for dataset in EXPECTED_CONTRACT_HASHES:
        payload = contract_payload(dataset)
        assert seed_by_dataset[dataset] == (payload["definition"], payload["definition_hash"])


def test_mapping_contract_accepts_complete_mapping_and_reports_required_field_gaps():
    definition = contract_payload("employees")["definition"]
    import_record = SimpleNamespace(
        dataset_type="employees",
        field_mapping={
            "employee_code": "employee_code",
            "full_name": "full_name",
            "job_title": "job_title",
            "standard_weekly_capacity_hours": "standard_weekly_capacity_hours",
        },
        selected_worksheet="CSV",
        inspection={
            "worksheets": [
                {
                    "name": "CSV",
                    "headers": [
                        "employee_code",
                        "full_name",
                        "job_title",
                        "standard_weekly_capacity_hours",
                    ],
                }
            ]
        },
        ignored_columns=[],
    )
    assert validate_import_mapping(import_record, definition) == []

    import_record.field_mapping = {"employee_code": "employee_code"}
    findings = validate_import_mapping(import_record, definition)
    assert {item["code"] for item in findings} == {"contract_required_fields_missing"}
    assert findings[0]["fields"] == [
        "full_name",
        "job_title",
        "standard_weekly_capacity_hours",
    ]


def test_freshness_requires_explicit_policy_and_source_as_of_and_uses_inclusive_boundary():
    now = datetime(2026, 11, 21, 12, tzinfo=UTC)
    assert evaluate_freshness(max_age_hours=None, source_as_of_at=now, evaluated_at=now) == (
        "policy_missing"
    )
    assert evaluate_freshness(max_age_hours=24, source_as_of_at=None, evaluated_at=now) == (
        "source_as_of_missing"
    )
    assert (
        evaluate_freshness(
            max_age_hours=1, source_as_of_at=now - timedelta(hours=1), evaluated_at=now
        )
        == "fresh"
    )
    assert (
        evaluate_freshness(
            max_age_hours=1,
            source_as_of_at=now - timedelta(hours=1, microseconds=1),
            evaluated_at=now,
        )
        == "stale"
    )
    assert (
        evaluate_freshness(
            max_age_hours=24, source_as_of_at=now + timedelta(seconds=1), evaluated_at=now
        )
        == "future_as_of"
    )


def test_quality_state_fails_closed_and_only_m10_roster_duplicate_warnings_are_compatible():
    clean = assess_quality(
        import_status="applied",
        invalid_rows=0,
        warning_rows=0,
        duplicate_rows=0,
        skipped_rows=0,
        mapping_version=1,
        validation_hash="a" * 64,
        lineage_count=1,
        mapping_findings=[],
        issue_counts=Counter(),
    )
    assert clean.status == "passed"
    assert clean.ingestion_status == "succeeded"

    duplicate_warning = assess_quality(
        import_status="applied_with_warnings",
        invalid_rows=0,
        warning_rows=1,
        duplicate_rows=1,
        skipped_rows=1,
        mapping_version=1,
        validation_hash="b" * 64,
        lineage_count=1,
        mapping_findings=[],
        issue_counts=Counter({("warning", "duplicate_existing"): 1}),
    )
    assert duplicate_warning.status == "warning"
    assert duplicate_warning.ingestion_status == "succeeded_with_warnings"
    assert m10_warning_codes_are_compatible("employees", list(duplicate_warning.findings))
    assert m10_warning_codes_are_compatible("projects", list(duplicate_warning.findings))
    assert not m10_warning_codes_are_compatible("weekly_capacity", list(duplicate_warning.findings))
    assert not m10_warning_codes_are_compatible(
        "employees",
        [*duplicate_warning.findings, {"severity": "warning", "code": "other"}],
    )

    partial = assess_quality(
        import_status="applied_with_warnings",
        invalid_rows=1,
        warning_rows=0,
        duplicate_rows=0,
        skipped_rows=1,
        mapping_version=1,
        validation_hash="c" * 64,
        lineage_count=1,
        mapping_findings=[],
        issue_counts=Counter({("error", "invalid_value"): 1}),
    )
    assert partial.status == "failed"
    assert partial.ingestion_status == "blocked"

    no_lineage = assess_quality(
        import_status="applied",
        invalid_rows=0,
        warning_rows=0,
        duplicate_rows=0,
        skipped_rows=0,
        mapping_version=1,
        validation_hash="d" * 64,
        lineage_count=0,
        mapping_findings=[],
        issue_counts=Counter(),
    )
    assert no_lineage.status == "failed"
