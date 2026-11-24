from __future__ import annotations

import hashlib
import hmac
from typing import Literal
from uuid import UUID

from businessflow.metrics.snapshot import canonical_json_bytes

DIRECT_CASH_REQUEST_COMMITMENT_KEY_VERSION: Literal["app-secret.v1"] = "app-secret.v1"


def direct_cash_amount_input_fingerprint(
    *,
    secret: bytes,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    snapshot_id: UUID,
    input_id: str,
    source_row_number: int,
    amount_text: str | None,
) -> str:
    """Bind exact submitted text without persisting it or exposing an unkeyed amount hash.

    M14's separate amount commitment authenticates canonical numeric value against the row. This
    M16 fingerprint also binds the exact wire text (including invalid/missing submissions) to the
    immutable run snapshot, so replay must reproduce the same request without storing the text.
    """
    payload = {
        "schema_version": 1,
        "key_version": DIRECT_CASH_REQUEST_COMMITMENT_KEY_VERSION,
        "purpose": "businessflow.direct-cash.amount-input.v1",
        "tenant_id": str(tenant_id),
        "company_id": str(company_id),
        "intake_id": str(intake_id),
        "snapshot_id": str(snapshot_id),
        "input_id": input_id,
        "source_row_number": source_row_number,
        "amount_text": amount_text,
    }
    return hmac.new(secret, canonical_json_bytes(payload), hashlib.sha256).hexdigest()


def fingerprint_matches(expected: str, actual: str) -> bool:
    return hmac.compare_digest(expected, actual)
