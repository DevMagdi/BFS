from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from businessflow.config import Settings
from businessflow.data_imports.parsers import ParsedRow, ParsedSheet
from businessflow.errors import ApiError
from businessflow.finance_sources.contracts import (
    FINANCE_SOURCE_CONTRACT_DEFINITION,
    FINANCE_SOURCE_CONTRACT_HASH,
    FINANCE_SOURCE_CONTRACT_KEY,
    FINANCE_SOURCE_CONTRACT_VERSION,
    FINANCE_SOURCE_GOVERNANCE_STATUS,
    FINANCE_SOURCE_RECONCILIATION_METHOD,
)
from businessflow.finance_sources.schemas import FinanceSourceIntakeMetadata
from businessflow.finance_sources.service import (
    _build_reconciliation_results,
    _canonical_mapping,
    _mapping_hash,
    _profile_columns,
    _validate_mapping,
    _validate_rows,
    _verify_mapping_record,
    _verify_snapshot,
    ensure_finance_source_intake_enabled,
)
from businessflow.metrics.snapshot import content_hash

TENANT_ID = uuid4()
COMPANY_ID = uuid4()
FIELD_MAPPING = {
    "source_record_id": "record",
    "reconciliation_key": "bucket",
    "period_start": "from",
    "period_end": "to",
    "amount": "amount",
    "currency": "currency",
}


def test_finance_source_activation_defaults_closed_and_fails_closed_on_bad_config(monkeypatch):
    monkeypatch.delenv("FINANCE_SOURCE_INTAKE_ENABLED", raising=False)
    settings_args = {
        "_env_file": None,
        "app_secret_key": "test-only-key-" + "x" * 48,
        "app_env": "test",
        "database_url": "postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
    }
    disabled = Settings(**settings_args)
    assert disabled.finance_source_intake_enabled is False
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_finance_source_intake_enabled(disabled)
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_finance_source_intake_enabled(None)

    monkeypatch.setenv("FINANCE_SOURCE_INTAKE_ENABLED", "true")
    enabled = Settings(**settings_args)
    assert enabled.finance_source_intake_enabled is True
    ensure_finance_source_intake_enabled(enabled)

    monkeypatch.setenv("FINANCE_SOURCE_INTAKE_ENABLED", "not-a-boolean")
    with pytest.raises(ValidationError):
        Settings(**settings_args)


def test_finance_source_intake_is_disabled_in_production_even_when_flag_is_true():
    settings = Settings(
        _env_file=None,
        app_secret_key="production-only-key-" + "x" * 48,
        app_env="production",
        database_url="postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
        finance_source_intake_enabled=True,
    )
    assert settings.finance_source_intake_enabled is True
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_finance_source_intake_enabled(settings)


def _metadata(
    *,
    expected_amount: str = "10.12",
    source_hash: str = "f" * 64,
    ignored_columns: list[dict[str, str]] | None = None,
    source_key: str = "sample-source",
) -> FinanceSourceIntakeMetadata:
    payload = {
        "source_key": source_key,
        "source_as_of_at": "2026-11-21T12:00:00+00:00",
        "freshness_max_age_hours": 24,
        "field_mapping": FIELD_MAPPING,
        "ignored_columns": ignored_columns or [],
        "controls": [
            {
                "control_id": "control-001",
                "reconciliation_key": "bucket-a",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "currency": "USD",
                "expected_amount": expected_amount,
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "independent-control",
                    "source_hash": source_hash,
                    "record_locator": "control:row:1",
                },
            }
        ],
    }
    return FinanceSourceIntakeMetadata.model_validate_json(json.dumps(payload))


def _sheet(
    rows: list[list[str | None]],
    *,
    headers: list[str] | None = None,
) -> ParsedSheet:
    return ParsedSheet(
        name="CSV",
        headers=headers or ["record", "bucket", "from", "to", "amount", "currency"],
        rows=[ParsedRow(row_number=index + 2, values=values) for index, values in enumerate(rows)],
        blank_rows_ignored=0,
    )


def _valid_rows(
    sheet: ParsedSheet,
    metadata: FinanceSourceIntakeMetadata,
    *,
    intake_id=None,
):
    return _validate_rows(
        sheet,
        metadata,
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        intake_id=intake_id or uuid4(),
        identity_secret=b"unit-test-key-material-that-is-not-used-outside-tests",
    )


def test_contract_is_versioned_and_migration_seed_matches_exactly():
    assert content_hash(FINANCE_SOURCE_CONTRACT_DEFINITION) == FINANCE_SOURCE_CONTRACT_HASH
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0011_finance_source_intake.py"
    )
    spec = importlib.util.spec_from_file_location("m14_finance_source_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.CONTRACT_KEY == FINANCE_SOURCE_CONTRACT_KEY
    assert migration.CONTRACT_VERSION == FINANCE_SOURCE_CONTRACT_VERSION
    assert migration.CONTRACT_HASH == FINANCE_SOURCE_CONTRACT_HASH
    assert json.loads(migration.CONTRACT_DEFINITION) == FINANCE_SOURCE_CONTRACT_DEFINITION
    assert content_hash(migration.CONTRACT_DEFINITION_OBJECT) == FINANCE_SOURCE_CONTRACT_HASH


def test_saved_snapshot_hashes_and_columns_are_bound_to_the_evidence():
    source_as_of_at = datetime(2026, 11, 21, 12, tzinfo=UTC)
    created_at = datetime(2026, 11, 21, 12, 1, tzinfo=UTC)
    intake_id = uuid4()
    requester_id = uuid4()
    ignored_columns = [{"header": "memo", "rationale": "Outside the finance contract."}]
    mapping_hash = _mapping_hash(
        source_key="sample-source",
        field_mapping=FIELD_MAPPING,
        ignored_columns=ignored_columns,
        contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
    )
    input_snapshot: dict[str, object] = {
        "snapshot_schema_version": 1,
        "engine_version": "businessflow.finance-source-intake.v1",
        "intake_id": str(intake_id),
        "tenant_id": str(TENANT_ID),
        "company_id": str(COMPANY_ID),
        "requested_by_user_id": str(requester_id),
        "created_at": created_at.isoformat(timespec="microseconds"),
        "contract": {
            "contract_key": FINANCE_SOURCE_CONTRACT_KEY,
            "version": FINANCE_SOURCE_CONTRACT_VERSION,
            "definition_hash": FINANCE_SOURCE_CONTRACT_HASH,
            "definition": FINANCE_SOURCE_CONTRACT_DEFINITION,
        },
        "mapping": {
            "mapping_version": 1,
            "mapping_hash": mapping_hash,
            "field_mapping": FIELD_MAPPING,
            "ignored_columns": ignored_columns,
        },
        "source": {
            "source_key": "sample-source",
            "source_as_of_at": source_as_of_at.isoformat(timespec="microseconds"),
            "freshness_max_age_hours": 24,
        },
        "file": {
            "file_name": "sample.csv",
            "format": "csv",
            "file_hash": "b" * 64,
            "file_size_bytes": 128,
            "row_count": 1,
        },
        "profile": {
            "total_rows": 1,
            "valid_rows": 1,
            "invalid_rows": 0,
            "duplicate_rows": 0,
        },
    }
    input_hash = content_hash(input_snapshot)
    result_snapshot: dict[str, object] = {
        "snapshot_schema_version": 1,
        "engine_version": "businessflow.finance-source-intake.v1",
        "intake_id": str(intake_id),
        "input_hash": input_hash,
        "reconciliation_method": FINANCE_SOURCE_RECONCILIATION_METHOD,
        "governance_status": FINANCE_SOURCE_GOVERNANCE_STATUS,
        "reconciliation_groups": [],
        "quality_status": "passed",
        "freshness_status": "fresh",
        "freshness_evaluated_at": created_at.isoformat(timespec="microseconds"),
        "reconciliation_status": "passed",
        "counts": {
            "total_rows": 1,
            "valid_rows": 1,
            "invalid_rows": 0,
            "duplicate_rows": 0,
        },
    }
    saved = SimpleNamespace(
        id=intake_id,
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        requested_by_user_id=requester_id,
        created_at=created_at,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        input_hash=input_hash,
        result_hash=content_hash(result_snapshot),
        contract_key=FINANCE_SOURCE_CONTRACT_KEY,
        contract_version=FINANCE_SOURCE_CONTRACT_VERSION,
        contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
        mapping_version=1,
        mapping_hash=mapping_hash,
        source_key="sample-source",
        source_as_of_at=source_as_of_at,
        freshness_max_age_hours=24,
        file_name="sample.csv",
        file_format="csv",
        file_hash="b" * 64,
        file_size_bytes=128,
        total_rows=1,
        valid_rows=1,
        invalid_rows=0,
        duplicate_rows=0,
        quality_status="passed",
        freshness_status="fresh",
        reconciliation_status="passed",
    )
    _verify_snapshot(saved)

    with_control = deepcopy(saved)
    with_control.result_snapshot["reconciliation_groups"] = [
        {
            "control_id": "control-001",
            "state": "passed",
            "comparison_method": FINANCE_SOURCE_RECONCILIATION_METHOD,
            "control_origin": "caller_supplied",
        }
    ]
    with_control.result_hash = content_hash(with_control.result_snapshot)
    _verify_snapshot(with_control)

    stripped_semantics = deepcopy(with_control)
    stripped_semantics.result_snapshot["reconciliation_groups"][0].pop("comparison_method")
    stripped_semantics.result_snapshot["reconciliation_groups"][0].pop("control_origin")
    stripped_semantics.result_hash = content_hash(stripped_semantics.result_snapshot)
    with pytest.raises(ApiError, match="could not be verified"):
        _verify_snapshot(stripped_semantics)

    legacy_snapshot = deepcopy(with_control)
    legacy_snapshot.result_snapshot.pop("reconciliation_method")
    legacy_snapshot.result_snapshot.pop("governance_status")
    legacy_snapshot.result_snapshot["reconciliation_groups"] = [
        {"control_id": "control-001", "state": "passed"}
    ]
    legacy_snapshot.result_hash = content_hash(legacy_snapshot.result_snapshot)
    _verify_snapshot(legacy_snapshot)

    tampered = deepcopy(saved)
    tampered.input_snapshot["mapping"]["ignored_columns"][0]["rationale"] = "Tampered rationale."
    tampered.input_hash = content_hash(tampered.input_snapshot)
    tampered.result_snapshot["input_hash"] = tampered.input_hash
    tampered.result_hash = content_hash(tampered.result_snapshot)
    with pytest.raises(ApiError, match="could not be verified"):
        _verify_snapshot(tampered)

    tampered = deepcopy(saved)
    tampered.result_snapshot["governance_status"]["accounting_correctness"] = "approved"
    tampered.result_hash = content_hash(tampered.result_snapshot)
    with pytest.raises(ApiError, match="could not be verified"):
        _verify_snapshot(tampered)

    tampered = deepcopy(saved)
    tampered.input_snapshot["tenant_id"] = str(uuid4())
    tampered.input_hash = content_hash(tampered.input_snapshot)
    with pytest.raises(ApiError, match="could not be verified"):
        _verify_snapshot(tampered)

    saved.mapping_hash = "c" * 64
    with pytest.raises(ApiError, match="could not be verified"):
        _verify_snapshot(saved)


def test_metadata_requires_complete_explicit_mapping_controls_and_plain_decimal_text():
    data = {
        "source_key": "sample-source",
        "field_mapping": FIELD_MAPPING,
        "controls": [
            {
                "control_id": "control-001",
                "reconciliation_key": "bucket-a",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "currency": "USD",
                "expected_amount": "10.12",
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "independent-control",
                    "source_hash": "f" * 64,
                    "record_locator": "control:row:1",
                },
            }
        ],
    }
    assert (
        FinanceSourceIntakeMetadata.model_validate_json(json.dumps(data)).source_key
        == "sample-source"
    )

    incomplete = {**data, "field_mapping": {"amount": "amount"}}
    with pytest.raises(ValidationError):
        FinanceSourceIntakeMetadata.model_validate_json(json.dumps(incomplete))

    caller_toggle = {**data, "finance_source_intake_enabled": True}
    with pytest.raises(ValidationError):
        FinanceSourceIntakeMetadata.model_validate_json(json.dumps(caller_toggle))

    with_whitespace_amount = json.loads(json.dumps(data))
    with_whitespace_amount["controls"][0]["expected_amount"] = " 10.12 "
    with pytest.raises(ValidationError):
        FinanceSourceIntakeMetadata.model_validate_json(json.dumps(with_whitespace_amount))

    with_exponent = json.loads(json.dumps(data))
    with_exponent["controls"][0]["expected_amount"] = "1e2"
    with pytest.raises(ValidationError):
        FinanceSourceIntakeMetadata.model_validate_json(json.dumps(with_exponent))

    naive_as_of = {**data, "source_as_of_at": "2026-11-21T12:00:00"}
    with pytest.raises(ValidationError):
        FinanceSourceIntakeMetadata.model_validate_json(json.dumps(naive_as_of))


def test_every_header_must_be_mapped_or_ignored_with_a_rationale():
    sheet = _sheet(
        [["record-1", "bucket-a", "2026-11-01", "2026-11-30", "10.12", "USD", "memo"]],
        headers=["record", "bucket", "from", "to", "amount", "currency", "memo"],
    )
    metadata = _metadata(ignored_columns=[{"header": "memo", "rationale": "Outside the contract."}])
    _validate_mapping(sheet, metadata)
    profile = _profile_columns(sheet, metadata)
    ignored = next(item for item in profile if item["header"] == "memo")
    assert ignored == {
        "header": "memo",
        "mapped_field": None,
        "ignored_rationale": "Outside the contract.",
        "nonblank_count": 1,
        "blank_count": 0,
        "distinct_nonblank_count": 1,
    }

    with pytest.raises(ApiError, match="Every source header"):
        _validate_mapping(sheet, _metadata())

    changed_rationale = _metadata(
        ignored_columns=[{"header": "memo", "rationale": "Not defined in this version."}]
    )
    first_mapping, first_ignored = _canonical_mapping(metadata)
    second_mapping, second_ignored = _canonical_mapping(changed_rationale)
    assert _mapping_hash(
        source_key=metadata.source_key,
        field_mapping=first_mapping,
        ignored_columns=first_ignored,
        contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
    ) != _mapping_hash(
        source_key=changed_rationale.source_key,
        field_mapping=second_mapping,
        ignored_columns=second_ignored,
        contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
    )

    saved_mapping = SimpleNamespace(
        source_key=metadata.source_key,
        contract_key=FINANCE_SOURCE_CONTRACT_KEY,
        contract_version=FINANCE_SOURCE_CONTRACT_VERSION,
        contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
        mapping_hash=_mapping_hash(
            source_key=metadata.source_key,
            field_mapping=first_mapping,
            ignored_columns=first_ignored,
            contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
        ),
        field_mapping=first_mapping,
        ignored_columns=first_ignored,
    )
    _verify_mapping_record(saved_mapping)
    saved_mapping.ignored_columns = second_ignored
    with pytest.raises(ApiError, match="could not be verified"):
        _verify_mapping_record(saved_mapping)


def test_repeated_group_labels_are_not_duplicates_and_decimal_reconciliation_is_exact():
    sheet = _sheet(
        [
            ["record-1", "bucket-a", "2026-11-01", "2026-11-30", "10.1", "USD"],
            ["record-2", "bucket-a", "2026-11-01", "2026-11-30", "0.02", "USD"],
        ]
    )
    metadata = _metadata()
    intake_id = uuid4()
    rows = _valid_rows(sheet, metadata, intake_id=intake_id)
    assert [row.row_status for row in rows] == ["valid", "valid"]
    assert len({row.source_record_identity_hash for row in rows}) == 2
    assert rows[0].reconciliation_group_hash == rows[1].reconciliation_group_hash

    results, status, unmatched_sources, unmatched_controls, unassigned = (
        _build_reconciliation_results(rows=rows, metadata=metadata, file_hash="a" * 64)
    )
    assert status == "passed"
    assert (unmatched_sources, unmatched_controls, unassigned) == (0, 0, 0)
    assert results[0]["state"] == "passed"
    assert results[0]["actual_amount"] == "10.12"
    assert results[0]["variance"] == "0"
    assert results[0]["control_id"] == "control-001"


def test_duplicate_identity_is_detected_by_source_record_id_not_group_label():
    sheet = _sheet(
        [
            ["record-duplicate", "bucket-a", "2026-11-01", "2026-11-30", "10", "USD"],
            ["record-duplicate", "bucket-a", "2026-11-01", "2026-11-30", "20", "USD"],
        ]
    )
    rows = _valid_rows(sheet, _metadata())
    assert [row.row_status for row in rows] == ["duplicate", "duplicate"]
    assert rows[0].source_record_identity_hash == rows[1].source_record_identity_hash
    assert all(row.issues[0]["code"] == "duplicate_source_record_id" for row in rows)

    result, status, *_ = _build_reconciliation_results(
        rows=rows,
        metadata=_metadata(),
        file_hash="a" * 64,
    )
    assert status == "not_verifiable"
    assert result[0]["state"] == "not_verifiable"
    assert result[0]["actual_amount"] is None
    assert "source_group_contains_invalid_or_duplicate_rows" in result[0]["reason_codes"]


def test_invalid_amount_missing_control_and_currency_mismatch_never_default_to_zero():
    bad_amount_sheet = _sheet([["record-1", "bucket-a", "2026-11-01", "2026-11-30", "", "USD"]])
    metadata = _metadata(expected_amount="0")
    rows = _valid_rows(bad_amount_sheet, metadata)
    assert rows[0].row_status == "invalid"
    assert rows[0].amount is None
    result, status, *_ = _build_reconciliation_results(
        rows=rows,
        metadata=metadata,
        file_hash="a" * 64,
    )
    assert status == "not_verifiable"
    assert result[0]["actual_amount"] is None

    foreign_currency_sheet = _sheet(
        [["record-1", "bucket-a", "2026-11-01", "2026-11-30", "10.12", "EUR"]]
    )
    foreign_rows = _valid_rows(foreign_currency_sheet, metadata)
    result, status, *_ = _build_reconciliation_results(
        rows=foreign_rows,
        metadata=metadata,
        file_hash="a" * 64,
    )
    assert status == "not_verifiable"
    control_result = next(item for item in result if item["control_id"] == "control-001")
    assert control_result["state"] == "not_verifiable"
    assert control_result["reason_codes"] == ["source_group_missing"]


def test_control_source_that_overlaps_the_uploaded_file_is_not_verifiable():
    metadata = _metadata(source_hash="a" * 64)
    rows = _valid_rows(
        _sheet([["record-1", "bucket-a", "2026-11-01", "2026-11-30", "10.12", "USD"]]),
        metadata,
    )
    results, status, *_ = _build_reconciliation_results(
        rows=rows,
        metadata=metadata,
        file_hash="a" * 64,
    )
    assert status == "not_verifiable"
    assert results[0]["state"] == "not_verifiable"
    assert results[0]["reason_codes"] == ["expected_source_overlaps_input_evidence"]
    assert results[0]["actual_amount"] is None


def test_missing_control_is_not_silently_interpreted_as_zero():
    metadata = _metadata()
    source_group_rows = _valid_rows(
        _sheet([["record-1", "uncontrolled", "2026-11-01", "2026-11-30", "10.12", "USD"]]),
        metadata,
    )
    results, status, unmatched_sources, unmatched_controls, _ = _build_reconciliation_results(
        rows=source_group_rows,
        metadata=metadata,
        file_hash="a" * 64,
    )
    assert status == "not_verifiable"
    assert unmatched_sources == 1
    assert unmatched_controls == 1
    assert len(results) == 2
    assert all(result["state"] == "not_verifiable" for result in results)
    assert all(result["actual_amount"] is None for result in results)


def test_freshness_inputs_are_asserted_and_not_given_a_default_policy():
    metadata = _metadata()
    assert metadata.source_as_of_at == datetime(2026, 11, 21, 12, tzinfo=UTC)
    no_policy = metadata.model_copy(update={"freshness_max_age_hours": None})
    assert no_policy.freshness_max_age_hours is None
    assert date(2026, 11, 1) < date(2026, 11, 30)
