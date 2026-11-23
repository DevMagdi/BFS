from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from businessflow.metrics.snapshot import canonical_json_bytes

AMOUNT_COMMITMENT_KEY_VERSION = "app-secret.v1"


def finance_amount_commitment(
    *,
    secret: bytes,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    source_key: str,
    file_hash: str,
    source_row_number: int,
    source_row_hash: str,
    source_record_identity_hash: str,
    reconciliation_group_hash: str,
    contract_key: str,
    contract_version: str,
    contract_hash: str,
    mapping_version: int,
    mapping_hash: str,
    canonical_amount_text: str,
) -> str:
    """HMAC one canonical Decimal value to its exact M14 row and governance references.

    The value itself is deliberately not persisted. A party holding the stable application
    secret can verify a resubmitted canonical amount, but the commitment does not authenticate
    the original source or establish source authority.
    """
    payload = {
        "schema_version": 1,
        "key_version": AMOUNT_COMMITMENT_KEY_VERSION,
        "tenant_id": str(tenant_id),
        "company_id": str(company_id),
        "intake_id": str(intake_id),
        "source_key": source_key,
        "file_hash": file_hash,
        "source_row_number": source_row_number,
        "source_row_hash": source_row_hash,
        "source_record_identity_hash": source_record_identity_hash,
        "reconciliation_group_hash": reconciliation_group_hash,
        "contract_key": contract_key,
        "contract_version": contract_version,
        "contract_hash": contract_hash,
        "mapping_version": mapping_version,
        "mapping_hash": mapping_hash,
        "canonical_amount_text": canonical_amount_text,
    }
    return hmac.new(secret, canonical_json_bytes(payload), hashlib.sha256).hexdigest()
