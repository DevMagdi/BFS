"""Add the guided manual customer-commitment workflow to Company Data Foundation.

Revision ID: 0018_customer_commitments
Revises: 0017_core_financial_transfers
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_customer_commitments"
down_revision = "0017_core_financial_transfers"
branch_labels = None
depends_on = None

MANUAL_CONTRACT_KEY = "businessflow.manual.customer_commitments"
MANUAL_CONTRACT_VERSION = "1.0.0"
MANUAL_CONTRACT_DEFINITION = (
    '{"dataset_type":"customer_commitments","defaults":[],"fields":['
    '{"description":"Customer name as entered by a Company member.","name":"customer_name",'
    '"required":true},{"description":"Optional customer-provided order or reference number.",'
    '"name":"customer_reference","required":false},{"description":"Date the customer '
    'commitment is recorded as made.","name":"order_date","required":true},'
    '{"description":"Optional requested fulfillment date; must not precede order date.",'
    '"name":"requested_delivery_date","required":false},{"description":"Short description '
    'of the customer commitment or requested work.","name":"summary","required":true},'
    '{"description":"Optional additional context; not an accounting or legal record.",'
    '"name":"notes","required":false}],"mutually_exclusive":[],"required":['
    '"customer_name","order_date","summary"],"required_one_of":[],"source_kind":"manual_entry"}'
)
MANUAL_CONTRACT_HASH = "60bc2004be7cc886db524158a46eb83680735d0209fc67271dcc6da89bf967b6"


def _require_migrator() -> None:
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
    _require_migrator()

    op.drop_constraint("ck_company_data_contracts_dataset", "company_data_contracts", type_="check")
    op.create_check_constraint(
        "ck_company_data_contracts_dataset",
        "company_data_contracts",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments', 'customer_commitments')",
    )
    op.drop_constraint("ck_company_data_sources_dataset", "company_data_sources", type_="check")
    op.create_check_constraint(
        "ck_company_data_sources_dataset",
        "company_data_sources",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments', 'customer_commitments')",
    )

    op.execute(
        sa.text(
            "INSERT INTO company_data_contracts "
            "(contract_key, version, dataset_type, definition, definition_hash) "
            "VALUES (:key, :version, 'customer_commitments', "
            "CAST(:definition AS jsonb), :definition_hash)"
        ).bindparams(
            sa.bindparam("key", value=MANUAL_CONTRACT_KEY, type_=sa.String(length=128)),
            sa.bindparam("version", value=MANUAL_CONTRACT_VERSION, type_=sa.String(length=32)),
            sa.bindparam("definition", value=MANUAL_CONTRACT_DEFINITION, type_=sa.Text()),
            sa.bindparam("definition_hash", value=MANUAL_CONTRACT_HASH, type_=sa.String(length=64)),
        )
    )

    op.create_table(
        "manual_customer_commitments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=True),
        sa.Column("customer_reference", sa.String(length=128), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.String(length=1000), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'changes_requested', 'accepted', 'rejected')",
            name="ck_manual_customer_commitments_status",
        ),
        sa.CheckConstraint("current_revision >= 1", name="ck_manual_customer_commitments_revision"),
        sa.CheckConstraint(
            "customer_name IS NULL OR char_length(btrim(customer_name)) BETWEEN 1 AND 200",
            name="ck_manual_customer_commitments_customer_name",
        ),
        sa.CheckConstraint(
            "customer_reference IS NULL OR char_length(btrim(customer_reference)) "
            "BETWEEN 1 AND 128",
            name="ck_manual_customer_commitments_customer_reference",
        ),
        sa.CheckConstraint(
            "summary IS NULL OR char_length(btrim(summary)) BETWEEN 1 AND 1000",
            name="ck_manual_customer_commitments_summary",
        ),
        sa.CheckConstraint(
            "notes IS NULL OR char_length(notes) <= 4000",
            name="ck_manual_customer_commitments_notes",
        ),
        sa.CheckConstraint(
            "review_comment IS NULL OR char_length(btrim(review_comment)) BETWEEN 1 AND 2000",
            name="ck_manual_customer_commitments_review_comment",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'changes_requested') OR requested_delivery_date IS NULL "
            "OR order_date IS NULL OR requested_delivery_date >= order_date",
            name="ck_manual_customer_commitments_delivery_date",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND submitted_by_user_id IS NULL AND submitted_at IS NULL "
            "AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL "
            "AND review_comment IS NULL) OR "
            "(status = 'submitted' AND submitted_by_user_id IS NOT NULL "
            "AND submitted_at IS NOT NULL AND reviewed_by_user_id IS NULL "
            "AND reviewed_at IS NULL AND review_comment IS NULL) OR "
            "(status = 'changes_requested' AND submitted_by_user_id IS NOT NULL "
            "AND submitted_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND review_comment IS NOT NULL) OR "
            "(status = 'accepted' AND submitted_by_user_id IS NOT NULL "
            "AND submitted_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL) OR "
            "(status = 'rejected' AND submitted_by_user_id IS NOT NULL "
            "AND submitted_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND review_comment IS NOT NULL)",
            name="ck_manual_customer_commitments_lifecycle_fields",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "contract_key", "contract_version"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
                "company_data_sources.contract_key",
                "company_data_sources.contract_version",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version"],
            ["company_data_contracts.contract_key", "company_data_contracts.version"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submitted_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manual_customer_commitments"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_manual_customer_commitments_scope_id"
        ),
    )
    op.create_index(
        "ix_manual_customer_commitments_scope_status_created",
        "manual_customer_commitments",
        ["tenant_id", "company_id", "status", "created_at"],
    )
    op.create_index(
        "ix_manual_customer_commitments_scope_customer",
        "manual_customer_commitments",
        ["tenant_id", "company_id", "customer_name"],
    )

    op.create_table(
        "manual_customer_commitment_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("commitment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status_before", sa.String(length=24), nullable=True),
        sa.Column("status_after", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_event_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event_type IN ('draft_created', 'draft_saved', 'submitted', 'correction_requested', "
            "'corrected', 'evidence_added', 'accepted', 'rejected')",
            name="ck_manual_customer_commitment_events_type",
        ),
        sa.CheckConstraint(
            "current_revision >= 1", name="ck_manual_customer_commitment_events_revision"
        ),
        sa.CheckConstraint(
            "status_before IS NULL OR status_before IN "
            "('draft', 'submitted', 'changes_requested', 'accepted', 'rejected')",
            name="ck_manual_customer_commitment_events_status_before",
        ),
        sa.CheckConstraint(
            "status_after IN ('draft', 'submitted', 'changes_requested', 'accepted', 'rejected')",
            name="ck_manual_customer_commitment_events_status_after",
        ),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(btrim(comment)) BETWEEN 1 AND 2000",
            name="ck_manual_customer_commitment_events_comment",
        ),
        sa.CheckConstraint(
            "snapshot_hash ~ '^[0-9a-f]{64}$' AND event_hash ~ '^[0-9a-f]{64}$' "
            "AND (previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$')",
            name="ck_manual_customer_commitment_events_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_manual_customer_commitment_events_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "commitment_id"],
            [
                "manual_customer_commitments.tenant_id",
                "manual_customer_commitments.company_id",
                "manual_customer_commitments.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manual_customer_commitment_events"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "commitment_id",
            "current_revision",
            name="uq_manual_customer_commitment_events_revision",
        ),
    )
    op.create_index(
        "ix_manual_customer_commitment_events_history",
        "manual_customer_commitment_events",
        ["tenant_id", "company_id", "commitment_id", "current_revision"],
    )

    op.create_table(
        "manual_customer_commitment_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("commitment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "position('/' in file_name) = 0 AND position(chr(92) in file_name) = 0 "
            "AND char_length(file_name) BETWEEN 1 AND 255",
            name="ck_manual_customer_commitment_evidence_filename",
        ),
        sa.CheckConstraint(
            "media_type IN ('application/pdf', 'image/jpeg', 'image/png', 'text/plain')",
            name="ck_manual_customer_commitment_evidence_media_type",
        ),
        sa.CheckConstraint(
            "file_size_bytes BETWEEN 1 AND 10485760 "
            "AND octet_length(content_bytes) = file_size_bytes",
            name="ck_manual_customer_commitment_evidence_size",
        ),
        sa.CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$'",
            name="ck_manual_customer_commitment_evidence_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "commitment_id"],
            [
                "manual_customer_commitments.tenant_id",
                "manual_customer_commitments.company_id",
                "manual_customer_commitments.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manual_customer_commitment_evidence"),
    )
    op.create_index(
        "ix_manual_customer_commitment_evidence_record",
        "manual_customer_commitment_evidence",
        ["tenant_id", "company_id", "commitment_id", "created_at"],
    )

    for table in (
        "manual_customer_commitments",
        "manual_customer_commitment_events",
        "manual_customer_commitment_evidence",
    ):
        _add_company_context_rls(table)

    op.execute(
        """
        CREATE FUNCTION businessflow_manual_customer_commitment_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'manual customer commitment evidence and history are append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'manual_customer_commitment_append_only';
        END;
        $$;

        CREATE FUNCTION businessflow_manual_customer_commitment_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
          source_dataset varchar(32);
          source_key varchar(64);
          source_contract_key varchar(128);
          source_contract_version varchar(32);
          source_contract_hash varchar(64);
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'draft' OR NEW.current_revision <> 1
               OR NEW.created_by_user_id IS DISTINCT FROM actor_id
               OR NEW.updated_by_user_id IS DISTINCT FROM actor_id
               OR NEW.submitted_by_user_id IS NOT NULL OR NEW.submitted_at IS NOT NULL
               OR NEW.reviewed_by_user_id IS NOT NULL OR NEW.reviewed_at IS NOT NULL THEN
              RAISE EXCEPTION 'manual customer commitments must begin as an attributed draft'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_initial_state';
            END IF;
            SELECT s.dataset_type, s.source_key, s.contract_key, s.contract_version,
                   c.definition_hash
              INTO source_dataset, source_key, source_contract_key,
                   source_contract_version, source_contract_hash
              FROM company_data_sources AS s
              JOIN company_data_contracts AS c
                ON c.contract_key = s.contract_key AND c.version = s.contract_version
             WHERE s.tenant_id = NEW.tenant_id AND s.company_id = NEW.company_id
               AND s.id = NEW.source_id;
            IF NOT FOUND OR source_dataset IS DISTINCT FROM 'customer_commitments'
               OR source_key IS DISTINCT FROM 'manual.customer-commitments'
               OR source_contract_key IS DISTINCT FROM 'businessflow.manual.customer_commitments'
               OR source_contract_version IS DISTINCT FROM '1.0.0'
               OR source_contract_hash IS DISTINCT FROM
                 '60bc2004be7cc886db524158a46eb83680735d0209fc67271dcc6da89bf967b6'
               OR NEW.contract_key IS DISTINCT FROM source_contract_key
               OR NEW.contract_version IS DISTINCT FROM source_contract_version THEN
              RAISE EXCEPTION 'commitment must bind its registered manual source contract'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_source_guard';
            END IF;
            RETURN NEW;
          END IF;

          IF OLD.status IN ('accepted', 'rejected') THEN
            RAISE EXCEPTION 'accepted and rejected customer commitments are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'manual_customer_commitment_terminal';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.contract_key IS DISTINCT FROM OLD.contract_key
             OR NEW.contract_version IS DISTINCT FROM OLD.contract_version
             OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'manual customer commitment provenance is immutable'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_provenance';
          END IF;
          IF NEW.current_revision <> OLD.current_revision + 1
             OR NEW.updated_by_user_id IS DISTINCT FROM actor_id THEN
            RAISE EXCEPTION 'manual customer commitment updates require an attributed next revision'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_revision_guard';
          END IF;
          IF NEW.status IN ('submitted', 'accepted', 'rejected')
             AND (NEW.customer_name IS NULL OR char_length(btrim(NEW.customer_name)) = 0
               OR NEW.order_date IS NULL OR NEW.summary IS NULL
               OR char_length(btrim(NEW.summary)) = 0
               OR (NEW.requested_delivery_date IS NOT NULL AND NEW.order_date IS NOT NULL
                   AND NEW.requested_delivery_date < NEW.order_date)) THEN
            RAISE EXCEPTION 'submitted customer commitments must satisfy the required intake fields'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_required_fields';
          END IF;
          IF (OLD.status = 'draft' AND NEW.status NOT IN ('draft', 'submitted'))
             OR (OLD.status = 'changes_requested'
                 AND NEW.status NOT IN ('changes_requested', 'submitted'))
             OR (OLD.status = 'submitted'
                 AND NEW.status NOT IN ('accepted', 'rejected', 'changes_requested'))
             OR OLD.status IN ('accepted', 'rejected') THEN
            RAISE EXCEPTION 'invalid manual customer commitment lifecycle transition'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_transition';
          END IF;

          IF NEW.status = 'submitted' THEN
            IF NEW.submitted_by_user_id IS DISTINCT FROM actor_id OR NEW.submitted_at IS NULL
               OR NEW.reviewed_by_user_id IS NOT NULL OR NEW.reviewed_at IS NOT NULL
               OR NEW.review_comment IS NOT NULL THEN
              RAISE EXCEPTION 'submission attribution is invalid'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_submission_guard';
            END IF;
          ELSIF NEW.status IN ('accepted', 'rejected')
             OR (NEW.status = 'changes_requested' AND OLD.status = 'submitted') THEN
            IF OLD.status <> 'submitted'
               OR NEW.reviewed_by_user_id IS DISTINCT FROM actor_id
               OR NEW.reviewed_at IS NULL
               OR NEW.submitted_by_user_id = actor_id
               OR (NEW.status IN ('rejected', 'changes_requested')
                   AND NEW.review_comment IS NULL) THEN
              RAISE EXCEPTION 'review requires an independent reviewer and an attributed outcome'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_review_guard';
            END IF;
          ELSIF NEW.status = OLD.status THEN
            IF OLD.status NOT IN ('draft', 'changes_requested')
               OR NEW.submitted_by_user_id IS DISTINCT FROM OLD.submitted_by_user_id
               OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
               OR NEW.reviewed_by_user_id IS DISTINCT FROM OLD.reviewed_by_user_id
               OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
               OR NEW.review_comment IS DISTINCT FROM OLD.review_comment THEN
              RAISE EXCEPTION 'only editable customer commitment drafts may be saved'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_edit_guard';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_manual_customer_commitment_revision_event_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM manual_customer_commitment_events AS e
             WHERE e.tenant_id = NEW.tenant_id AND e.company_id = NEW.company_id
               AND e.commitment_id = NEW.id
               AND e.current_revision = NEW.current_revision
               AND e.status_after = NEW.status
          ) THEN
            RAISE EXCEPTION 'every customer commitment revision requires a matching history event'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_revision_event';
          END IF;
          RETURN NULL;
        END;
        $$;

        CREATE FUNCTION businessflow_manual_customer_commitment_event_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
          prior_hash varchar(64);
          prior_status varchar(24);
          record_revision integer;
          record_status varchar(24);
        BEGIN
          IF NEW.actor_user_id IS DISTINCT FROM actor_id THEN
            RAISE EXCEPTION 'customer commitment history actor must match the verified user context'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_event_actor';
          END IF;
          IF NEW.event_type = 'accepted'
             AND COALESCE(
               (NEW.snapshot->'action_context'->>'process_criteria_confirmed')::boolean,
               false
             ) IS NOT TRUE THEN
            RAISE EXCEPTION 'acceptance history must confirm process criteria'
              USING ERRCODE = '23514',
                    CONSTRAINT = 'manual_customer_commitment_acceptance_criteria';
          END IF;
          IF NEW.current_revision = 1 THEN
            IF NEW.event_type <> 'draft_created' OR NEW.previous_event_hash IS NOT NULL
               OR NEW.status_before IS NOT NULL OR NEW.status_after <> 'draft' THEN
              RAISE EXCEPTION 'customer commitment history must start with its draft creation'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_event_initial';
            END IF;
          ELSE
            SELECT e.event_hash, e.status_after INTO prior_hash, prior_status
              FROM manual_customer_commitment_events AS e
             WHERE e.tenant_id = NEW.tenant_id AND e.company_id = NEW.company_id
               AND e.commitment_id = NEW.commitment_id
               AND e.current_revision = NEW.current_revision - 1;
            IF NOT FOUND OR NEW.previous_event_hash IS DISTINCT FROM prior_hash
               OR NEW.status_before IS DISTINCT FROM prior_status THEN
              RAISE EXCEPTION 'customer commitment history chain is incomplete or out of order'
                USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_event_chain';
            END IF;
          END IF;
          SELECT c.current_revision, c.status INTO record_revision, record_status
            FROM manual_customer_commitments AS c
           WHERE c.tenant_id = NEW.tenant_id AND c.company_id = NEW.company_id
             AND c.id = NEW.commitment_id;
          IF NOT FOUND OR NEW.current_revision > record_revision
             OR (NEW.current_revision = record_revision AND NEW.status_after <> record_status) THEN
            RAISE EXCEPTION 'customer commitment history does not match its current state'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_event_state';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_manual_customer_commitment_evidence_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
          record_status varchar(24);
          evidence_count integer;
        BEGIN
          IF NEW.uploaded_by_user_id IS DISTINCT FROM actor_id THEN
            RAISE EXCEPTION 'evidence uploader must match the verified user context'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_evidence_actor';
          END IF;
          SELECT c.status INTO record_status
            FROM manual_customer_commitments AS c
           WHERE c.tenant_id = NEW.tenant_id AND c.company_id = NEW.company_id
             AND c.id = NEW.commitment_id
           FOR UPDATE;
          IF NOT FOUND OR record_status NOT IN ('draft', 'changes_requested') THEN
            RAISE EXCEPTION 'evidence can only be added to an editable customer commitment'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_evidence_state';
          END IF;
          SELECT count(*) INTO evidence_count FROM manual_customer_commitment_evidence AS e
           WHERE e.tenant_id = NEW.tenant_id AND e.company_id = NEW.company_id
             AND e.commitment_id = NEW.commitment_id;
          IF evidence_count >= 10 THEN
            RAISE EXCEPTION 'a customer commitment may contain at most ten evidence files'
              USING ERRCODE = '23514', CONSTRAINT = 'manual_customer_commitment_evidence_limit';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_company_data_m4_ingestion_dataset_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          source_dataset varchar(32);
        BEGIN
          SELECT s.dataset_type INTO source_dataset
            FROM company_data_sources AS s
           WHERE s.tenant_id = NEW.tenant_id AND s.company_id = NEW.company_id
             AND s.id = NEW.source_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'M4 ingestion source is not registered in this Company'
              USING ERRCODE = '23514', CONSTRAINT = 'company_data_ingestion_source_scope';
          END IF;
          IF source_dataset = 'customer_commitments' THEN
            RAISE EXCEPTION 'manual customer commitments cannot be recorded as M4 ingestions'
              USING ERRCODE = '23514',
                    CONSTRAINT = 'company_data_manual_ingestion_not_supported';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_company_data_ingestions_m4_dataset_guard
          BEFORE INSERT ON company_data_ingestions
          FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_m4_ingestion_dataset_guard();

        CREATE TRIGGER trg_manual_customer_commitments_guard
          BEFORE INSERT OR UPDATE ON manual_customer_commitments
          FOR EACH ROW EXECUTE FUNCTION businessflow_manual_customer_commitment_guard();
        CREATE CONSTRAINT TRIGGER trg_manual_customer_commitments_revision_event_guard
          AFTER INSERT OR UPDATE ON manual_customer_commitments
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION businessflow_manual_customer_commitment_revision_event_guard();
        CREATE TRIGGER trg_manual_customer_commitment_events_guard
          BEFORE INSERT ON manual_customer_commitment_events
          FOR EACH ROW EXECUTE FUNCTION businessflow_manual_customer_commitment_event_guard();
        CREATE TRIGGER trg_manual_customer_commitment_events_append_only
          BEFORE UPDATE OR DELETE ON manual_customer_commitment_events
          FOR EACH ROW EXECUTE FUNCTION businessflow_manual_customer_commitment_append_only();
        CREATE TRIGGER trg_manual_customer_commitment_evidence_guard
          BEFORE INSERT ON manual_customer_commitment_evidence
          FOR EACH ROW EXECUTE FUNCTION businessflow_manual_customer_commitment_evidence_guard();
        CREATE TRIGGER trg_manual_customer_commitment_evidence_append_only
          BEFORE UPDATE OR DELETE ON manual_customer_commitment_evidence
          FOR EACH ROW EXECUTE FUNCTION businessflow_manual_customer_commitment_append_only();

        REVOKE ALL ON manual_customer_commitments, manual_customer_commitment_events,
          manual_customer_commitment_evidence FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT, UPDATE ON manual_customer_commitments TO bf_runtime;
        GRANT SELECT, INSERT ON manual_customer_commitment_events,
          manual_customer_commitment_evidence TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON manual_customer_commitments,
          manual_customer_commitment_events, manual_customer_commitment_evidence FROM bf_runtime;
        """
    )


def downgrade() -> None:
    _require_migrator()

    op.execute("ALTER TABLE company_data_sources NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE manual_customer_commitments NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM company_data_sources WHERE dataset_type = 'customer_commitments'
          ) OR EXISTS (SELECT 1 FROM manual_customer_commitments) THEN
            RAISE EXCEPTION
              'cannot downgrade while manual customer-commitment source or record history exists';
          END IF;
        END;
        $$;
        """
    )
    op.execute("ALTER TABLE company_data_sources FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE manual_customer_commitments FORCE ROW LEVEL SECURITY")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_ingestions_m4_dataset_guard "
        "ON company_data_ingestions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_manual_customer_commitments_revision_event_guard "
        "ON manual_customer_commitments"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_manual_customer_commitments_guard "
        "ON manual_customer_commitments"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_manual_customer_commitment_events_guard "
        "ON manual_customer_commitment_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_manual_customer_commitment_events_append_only "
        "ON manual_customer_commitment_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_manual_customer_commitment_evidence_guard "
        "ON manual_customer_commitment_evidence"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_manual_customer_commitment_evidence_append_only "
        "ON manual_customer_commitment_evidence"
    )
    op.drop_table("manual_customer_commitment_evidence")
    op.drop_table("manual_customer_commitment_events")
    op.drop_table("manual_customer_commitments")
    op.execute("DROP FUNCTION IF EXISTS businessflow_manual_customer_commitment_guard()")
    op.execute(
        "DROP FUNCTION IF EXISTS businessflow_manual_customer_commitment_revision_event_guard()"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_manual_customer_commitment_event_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_manual_customer_commitment_evidence_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_manual_customer_commitment_append_only()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_company_data_m4_ingestion_dataset_guard()")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_contracts_append_only ON company_data_contracts"
    )
    op.execute(
        sa.text(
            "DELETE FROM company_data_contracts WHERE contract_key = :key AND version = :version"
        ).bindparams(
            sa.bindparam("key", value=MANUAL_CONTRACT_KEY, type_=sa.String(length=128)),
            sa.bindparam("version", value=MANUAL_CONTRACT_VERSION, type_=sa.String(length=32)),
        )
    )
    op.execute(
        "CREATE TRIGGER trg_company_data_contracts_append_only "
        "BEFORE UPDATE OR DELETE ON company_data_contracts "
        "FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_append_only()"
    )

    op.drop_constraint("ck_company_data_sources_dataset", "company_data_sources", type_="check")
    op.create_check_constraint(
        "ck_company_data_sources_dataset",
        "company_data_sources",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments')",
    )
    op.drop_constraint("ck_company_data_contracts_dataset", "company_data_contracts", type_="check")
    op.create_check_constraint(
        "ck_company_data_contracts_dataset",
        "company_data_contracts",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments')",
    )
