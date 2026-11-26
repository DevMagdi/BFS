from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from businessflow.api.finance_source_limits import multipart_upload_request_body_limit
from businessflow.api.finance_source_upload_limit import _upload_body_limit
from businessflow.company_data.commitment_schemas import (
    CustomerCommitmentCreateRequest,
    CustomerCommitmentReviewRequest,
    CustomerCommitmentUpdateRequest,
)
from businessflow.company_data.contracts import (
    MANUAL_COMMITMENT_CONTRACT_DEFINITION,
    MANUAL_COMMITMENT_CONTRACT_HASH,
    MANUAL_COMMITMENT_CONTRACT_KEY,
    MANUAL_COMMITMENT_CONTRACT_VERSION,
    MANUAL_COMMITMENT_DATASET,
)
from businessflow.company_data.customer_commitments import (
    MAX_EVIDENCE_BYTES,
    _detect_evidence_type,
    _safe_file_name,
    _validation,
)
from businessflow.errors import ApiError
from businessflow.metrics.snapshot import content_hash


def test_manual_contract_is_stable_and_not_an_m4_contract() -> None:
    assert MANUAL_COMMITMENT_DATASET == "customer_commitments"
    assert MANUAL_COMMITMENT_CONTRACT_KEY == "businessflow.manual.customer_commitments"
    assert MANUAL_COMMITMENT_CONTRACT_VERSION == "1.0.0"
    assert content_hash(MANUAL_COMMITMENT_CONTRACT_DEFINITION) == MANUAL_COMMITMENT_CONTRACT_HASH
    assert MANUAL_COMMITMENT_CONTRACT_DEFINITION["source_kind"] == "manual_entry"
    assert MANUAL_COMMITMENT_CONTRACT_DEFINITION["required"] == [
        "customer_name",
        "order_date",
        "summary",
    ]

    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0018_customer_commitments.py"
    )
    spec = importlib.util.spec_from_file_location("customer_commitments_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.MANUAL_CONTRACT_KEY == MANUAL_COMMITMENT_CONTRACT_KEY
    assert migration.MANUAL_CONTRACT_VERSION == MANUAL_COMMITMENT_CONTRACT_VERSION
    assert migration.MANUAL_CONTRACT_HASH == MANUAL_COMMITMENT_CONTRACT_HASH
    assert (
        content_hash(json.loads(migration.MANUAL_CONTRACT_DEFINITION))
        == MANUAL_COMMITMENT_CONTRACT_HASH
    )


def test_draft_schema_normalizes_blank_optional_values_and_rejects_unknown_fields() -> None:
    body = CustomerCommitmentCreateRequest(
        customer_name="  Nile Logistics  ",
        customer_reference="   ",
        summary="  Deliver onboarding  ",
        notes="  ",
    )
    assert body.customer_name == "Nile Logistics"
    assert body.customer_reference is None
    assert body.summary == "Deliver onboarding"
    assert body.notes is None
    with pytest.raises(ValidationError):
        CustomerCommitmentCreateRequest(customer_name="Nile Logistics", unexpected="value")


def test_revision_and_review_schemas_require_positive_revisions_and_trim_comments() -> None:
    update = CustomerCommitmentUpdateRequest(expected_revision=2, summary="Updated")
    assert update.expected_revision == 2
    with pytest.raises(ValidationError):
        CustomerCommitmentUpdateRequest(expected_revision=0)

    review = CustomerCommitmentReviewRequest(
        expected_revision=3,
        decision="request_correction",
        comment="  Please identify the customer reference.  ",
    )
    assert review.comment == "Please identify the customer reference."
    with pytest.raises(ValidationError):
        CustomerCommitmentReviewRequest(expected_revision=3, decision="approve")


def test_submission_validation_separates_required_errors_from_optional_warnings() -> None:
    today = date.today()
    draft = SimpleNamespace(
        customer_name=" ",
        customer_reference=None,
        order_date=today,
        requested_delivery_date=today - timedelta(days=1),
        summary=None,
    )
    validation = _validation(draft, [])
    assert {finding.code for finding in validation.errors} == {
        "required_customer_name",
        "required_summary",
        "delivery_date_before_order_date",
    }
    assert {finding.code for finding in validation.warnings} == {
        "missing_customer_reference",
        "no_supporting_evidence",
    }

    complete = SimpleNamespace(
        customer_name="Nile Logistics",
        customer_reference="NL-42",
        order_date=today,
        requested_delivery_date=today + timedelta(days=1),
        summary="Deliver onboarding",
    )
    valid = _validation(complete, [object()])
    assert valid.errors == []
    assert valid.warnings == []


def test_evidence_filename_and_file_signature_validation_fail_closed() -> None:
    assert _safe_file_name(r"customer\orders\confirmation.pdf") == "confirmation.pdf"
    assert _safe_file_name("customer-confirmation.txt") == "customer-confirmation.txt"
    with pytest.raises(ApiError, match="file name is invalid"):
        _safe_file_name(". ")
    with pytest.raises(ApiError, match="file name is invalid"):
        _safe_file_name("x" * 256)

    pdf = b"%PDF-1.7\nexample"
    assert _detect_evidence_type("confirmation.pdf", "application/pdf", pdf) == "application/pdf"
    assert _detect_evidence_type("notes.txt", "text/plain", b"customer note") == "text/plain"
    with pytest.raises(ApiError, match="Attach a valid PDF"):
        _detect_evidence_type("confirmation.pdf", "application/pdf", b"not a pdf")
    with pytest.raises(ApiError, match="does not match"):
        _detect_evidence_type("confirmation.pdf", "image/png", pdf)

    limit = _upload_body_limit(
        {
            "method": "POST",
            "path": "/api/v1/tenants/tenant-1/company-data/customer-commitments/record-1/evidence",
            "root_path": "",
        },
        25 * 1024 * 1024,
    )
    assert limit is not None
    assert limit.max_bytes == multipart_upload_request_body_limit(MAX_EVIDENCE_BYTES)
    assert "10 MiB" in limit.message
