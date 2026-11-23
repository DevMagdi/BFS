"""Add the bounded M14 finance source contract, mapping and reconciliation evidence.

Revision ID: 0011_finance_source_intake
Revises: 0010_metric_insight_runs
Create Date: 2026-11-21
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_finance_source_intake"
down_revision = "0010_metric_insight_runs"
branch_labels = None
depends_on = None

CONTRACT_KEY = "businessflow.finance.source-observations"
CONTRACT_VERSION = "1.0.0"
CONTRACT_HASH = "b16e6e87f4b1a25d0313b895cf5304de284caff4d82f1069158d4a60d09b53b3"
CONTRACT_DEFINITION_OBJECT: dict[str, object] = {
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
CONTRACT_DEFINITION = json.dumps(
    CONTRACT_DEFINITION_OBJECT,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
)


def _require_runtime_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF current_user <> 'bf_migrator' THEN
            RAISE EXCEPTION 'migrations must run as the dedicated bf_migrator role';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'required non-privileged role bf_runtime does not exist';
          END IF;
          IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'bf_runtime must not be a superuser or bypass RLS';
          END IF;
          IF pg_has_role('bf_runtime', 'bf_migrator', 'MEMBER') THEN
            RAISE EXCEPTION 'bf_runtime must not inherit or assume the migration role';
          END IF;
        END;
        $$;
        """
    )


def _add_company_context_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_company_context ON {table_name}
              USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
              WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
            """
        )
    )


def upgrade() -> None:
    _require_runtime_role()

    op.create_table(
        "finance_source_contracts",
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(contract_key) BETWEEN 1 AND 128", name="ck_finance_source_contracts_key"
        ),
        sa.CheckConstraint(
            "length(version) BETWEEN 1 AND 32", name="ck_finance_source_contracts_version"
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_finance_source_contracts_hash"
        ),
        sa.UniqueConstraint(
            "contract_key",
            "version",
            "definition_hash",
            name="uq_finance_source_contracts_hash_ref",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_finance_source_contracts_definition",
        ),
        sa.PrimaryKeyConstraint("contract_key", "version", name="pk_finance_source_contracts"),
    )
    contract_statement = sa.text(
        "INSERT INTO finance_source_contracts "
        "(contract_key, version, definition, definition_hash) "
        "VALUES (:key, :version, CAST(:definition AS jsonb), :definition_hash)"
    ).bindparams(
        sa.bindparam("key", value=CONTRACT_KEY, type_=sa.String(length=128)),
        sa.bindparam("version", value=CONTRACT_VERSION, type_=sa.String(length=32)),
        sa.bindparam("definition", value=CONTRACT_DEFINITION, type_=sa.Text()),
        sa.bindparam("definition_hash", value=CONTRACT_HASH, type_=sa.String(length=64)),
    )
    op.execute(contract_statement)

    op.create_table(
        "finance_source_mappings",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ignored_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_finance_source_mappings_key",
        ),
        sa.CheckConstraint("mapping_version >= 1", name="ck_finance_source_mappings_version"),
        sa.CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_mappings_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_mapping) = 'object'",
            name="ck_finance_source_mappings_field_mapping",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'",
            name="ck_finance_source_mappings_ignored_columns",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_finance_source_mappings_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_finance_source_mappings_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_finance_source_mappings_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_finance_source_mappings_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finance_source_mappings"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            name="uq_finance_source_mappings_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_key",
            "mapping_version",
            name="uq_finance_source_mappings_scope_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_key",
            "mapping_version",
            "mapping_hash",
            name="uq_finance_source_mappings_exact_version",
        ),
    )
    op.create_table(
        "finance_source_intakes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_as_of_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_max_age_hours", sa.Integer(), nullable=True),
        sa.Column("freshness_status", sa.String(length=24), nullable=False),
        sa.Column("quality_status", sa.String(length=16), nullable=False),
        sa.Column("reconciliation_status", sa.String(length=16), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("valid_rows", sa.Integer(), nullable=False),
        sa.Column("invalid_rows", sa.Integer(), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_finance_source_intakes_key",
        ),
        sa.CheckConstraint("file_format = 'csv'", name="ck_finance_source_intakes_file_format"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_finance_source_intakes_file_size"),
        sa.CheckConstraint(
            "freshness_status IN ('fresh', 'stale', 'policy_missing', "
            "'source_as_of_missing', 'future_as_of')",
            name="ck_finance_source_intakes_freshness",
        ),
        sa.CheckConstraint(
            "quality_status IN ('passed', 'warning', 'failed')",
            name="ck_finance_source_intakes_quality",
        ),
        sa.CheckConstraint(
            "reconciliation_status IN ('passed', 'failed', 'not_verifiable')",
            name="ck_finance_source_intakes_reconciliation",
        ),
        sa.CheckConstraint(
            "freshness_max_age_hours IS NULL OR freshness_max_age_hours > 0",
            name="ck_finance_source_intakes_freshness_age",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND invalid_rows >= 0 AND duplicate_rows >= 0 "
            "AND total_rows = valid_rows + invalid_rows + duplicate_rows",
            name="ck_finance_source_intakes_counts",
        ),
        sa.CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_intakes_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_finance_source_intakes_input_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_finance_source_intakes_result_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_finance_source_intakes_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_finance_source_intakes_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_finance_source_intakes_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_key", "mapping_version", "mapping_hash"],
            [
                "finance_source_mappings.tenant_id",
                "finance_source_mappings.company_id",
                "finance_source_mappings.source_key",
                "finance_source_mappings.mapping_version",
                "finance_source_mappings.mapping_hash",
            ],
            name="fk_finance_source_intakes_mapping",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_finance_source_intakes_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finance_source_intakes"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_finance_source_intakes_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_finance_source_intakes_idempotency",
        ),
    )
    op.create_index(
        "ix_finance_source_intakes_company_created",
        "finance_source_intakes",
        ["tenant_id", "company_id", "created_at"],
    )
    op.create_index(
        "ix_finance_source_intakes_source_created",
        "finance_source_intakes",
        ["tenant_id", "company_id", "source_key", "created_at"],
    )
    op.create_index(
        "ix_finance_source_intakes_states",
        "finance_source_intakes",
        ["tenant_id", "company_id", "quality_status", "freshness_status", "reconciliation_status"],
    )

    op.create_table(
        "finance_source_row_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_hash", sa.String(length=64), nullable=False),
        sa.Column("source_record_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("reconciliation_group_hash", sa.String(length=64), nullable=True),
        sa.Column("row_status", sa.String(length=16), nullable=False),
        sa.Column(
            "issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source_row_number >= 2", name="ck_finance_source_row_evidence_number"),
        sa.CheckConstraint(
            "source_row_hash ~ '^[0-9a-f]{64}$' AND source_record_identity_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_row_evidence_hashes",
        ),
        sa.CheckConstraint(
            "reconciliation_group_hash IS NULL OR reconciliation_group_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_row_evidence_group_hash",
        ),
        sa.CheckConstraint(
            "row_status IN ('valid', 'duplicate', 'invalid')",
            name="ck_finance_source_row_evidence_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(issues) = 'array'", name="ck_finance_source_row_evidence_issues"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "intake_id"],
            [
                "finance_source_intakes.tenant_id",
                "finance_source_intakes.company_id",
                "finance_source_intakes.id",
            ],
            name="fk_finance_source_row_evidence_intake",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finance_source_row_evidence"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "intake_id",
            "source_row_number",
            name="uq_finance_source_row_evidence_row",
        ),
    )
    for table_name in (
        "finance_source_mappings",
        "finance_source_intakes",
        "finance_source_row_evidence",
    ):
        _add_company_context_rls(table_name)

    op.execute(
        """
        CREATE FUNCTION businessflow_finance_source_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'finance source evidence is immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_finance_source_append_only';
        END;
        $$;
        CREATE TRIGGER trg_finance_source_contracts_append_only
          BEFORE UPDATE OR DELETE ON finance_source_contracts
          FOR EACH ROW EXECUTE FUNCTION businessflow_finance_source_append_only();
        CREATE TRIGGER trg_finance_source_mappings_append_only
          BEFORE UPDATE OR DELETE ON finance_source_mappings
          FOR EACH ROW EXECUTE FUNCTION businessflow_finance_source_append_only();
        CREATE TRIGGER trg_finance_source_intakes_append_only
          BEFORE UPDATE OR DELETE ON finance_source_intakes
          FOR EACH ROW EXECUTE FUNCTION businessflow_finance_source_append_only();
        CREATE TRIGGER trg_finance_source_row_evidence_append_only
          BEFORE UPDATE OR DELETE ON finance_source_row_evidence
          FOR EACH ROW EXECUTE FUNCTION businessflow_finance_source_append_only();
        REVOKE ALL ON finance_source_contracts FROM PUBLIC, bf_runtime;
        GRANT SELECT ON finance_source_contracts TO bf_runtime;
        REVOKE ALL ON finance_source_mappings, finance_source_intakes,
          finance_source_row_evidence FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON finance_source_mappings, finance_source_intakes,
          finance_source_row_evidence TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON finance_source_contracts, finance_source_mappings,
          finance_source_intakes, finance_source_row_evidence FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_finance_source_row_evidence_append_only
          ON finance_source_row_evidence;
        DROP TRIGGER IF EXISTS trg_finance_source_intakes_append_only ON finance_source_intakes;
        DROP TRIGGER IF EXISTS trg_finance_source_mappings_append_only ON finance_source_mappings;
        DROP TRIGGER IF EXISTS trg_finance_source_contracts_append_only ON finance_source_contracts;
        DROP FUNCTION IF EXISTS businessflow_finance_source_append_only();
        """
    )
    op.drop_table("finance_source_row_evidence")
    op.drop_table("finance_source_intakes")
    op.drop_table("finance_source_mappings")
    op.drop_table("finance_source_contracts")
