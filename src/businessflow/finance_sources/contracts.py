from __future__ import annotations

from businessflow.metrics.snapshot import content_hash

FINANCE_SOURCE_CONTRACT_KEY = "businessflow.finance.source-observations"
FINANCE_SOURCE_CONTRACT_VERSION = "1.0.0"
FINANCE_SOURCE_SNAPSHOT_SCHEMA_VERSION = 1
FINANCE_SOURCE_ENGINE_VERSION = "businessflow.finance-source-intake.v1"

# This is a deliberately semantic-light transport contract. It does not define an account chart,
# accounting category, recognition rule, fiscal calendar, or canonical finance fact.
FINANCE_SOURCE_CONTRACT_DEFINITION: dict[str, object] = {
    "schema_version": 1,
    "dataset_type": "finance_source_observations",
    "file_format": "UTF-8 CSV",
    "fields": [
        {
            "name": "source_record_id",
            "type": "opaque_source_identifier",
            "required": True,
            "max_length": 256,
            "description": (
                "Exact source-supplied record identity without leading or trailing whitespace, "
                "used only to detect duplicate identities within one intake; it is not a customer, "
                "account, or canonical entity ID."
            ),
        },
        {
            "name": "reconciliation_key",
            "type": "opaque_source_key",
            "required": True,
            "max_length": 256,
            "description": (
                "Exact source-supplied grouping key used only to match one caller-provided "
                "control; no account or accounting meaning is assigned."
            ),
        },
        {
            "name": "period_start",
            "type": "date",
            "required": True,
            "description": (
                "Source-reported period boundary in YYYY-MM-DD form; no fiscal-calendar, "
                "effective-date, or recognition semantics are inferred."
            ),
        },
        {
            "name": "period_end",
            "type": "date",
            "required": True,
            "description": (
                "Source-reported period boundary in YYYY-MM-DD form; no fiscal-calendar, "
                "effective-date, or recognition semantics are inferred."
            ),
        },
        {
            "name": "amount",
            "type": "plain_decimal_text",
            "required": True,
            "max_length": 64,
            "description": (
                "Exact base-10 source text; no rounding, defaulting, currency conversion, or "
                "accounting classification is applied."
            ),
        },
        {
            "name": "currency",
            "type": "iso_4217_code",
            "required": True,
            "description": "Source-reported ISO 4217 currency; currencies are never combined.",
        },
    ],
    "mapping": {
        "mode": "explicit_exact_header_mapping",
        "unmapped_columns": "must_be_ignored_with_a_non_empty_rationale",
        "defaults": "none",
    },
    "duplicate_identity": {
        "key": "exact_source_record_id_within_one_intake",
        "repeated_labels_are_not_record_identity": True,
        "behavior": "retain_hash_evidence_and_do_not_auto_deduplicate_or_supersede",
    },
    "reconciliation": {
        "group_key": ["reconciliation_key", "period_start", "period_end", "currency"],
        "control": "one_explicit_caller-supplied_control_per_exact_group",
        "tolerances": "absolute_and_relative_values_are_required_per_control",
        "missing_values": "remain_unknown_and_are_not_substituted_with_zero",
        "rounding": "none",
        "fx_conversion": "none",
    },
    "evidence": {
        "raw_uploaded_file_retained": False,
        "raw_source_rows_retained": False,
        "retained": [
            "file_hash",
            "row_hashes_and_opaque_identity_hashes",
            "mapping_version_and_rationale",
            "caller-supplied_control_values_and_references",
            "reconciliation_result_and_hashes",
        ],
    },
    "limitations": [
        (
            "A source key and source-as-of timestamp are caller assertions, not authenticated "
            "source identity."
        ),
        "A control reference is caller-asserted and does not prove organizational independence.",
        (
            "No owner-approved source, accounting definition, correction policy, or retention "
            "policy is implied."
        ),
        (
            "The comparison is not accounting correctness, source-authority verification, or a "
            "financial conclusion."
        ),
        (
            "No financial facts are written to M8 or any canonical ledger; no forecast or "
            "optimization is performed."
        ),
    ],
}

FINANCE_SOURCE_CONTRACT_HASH = content_hash(FINANCE_SOURCE_CONTRACT_DEFINITION)
