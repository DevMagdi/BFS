from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_operations_intelligence"
down_revision = "0028_universal_data_retention"
branch_labels = None
depends_on = None

TABLES = (
    "operations_event_streams",
    "operations_event_versions",
    "operations_event_current",
    "operations_analysis_runs",
)
RUNTIME_INSERT_TABLES = (
    "operations_event_streams",
    "operations_event_versions",
    "operations_event_current",
    "operations_analysis_runs",
)


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
          IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'bf_migrator') THEN
            RAISE EXCEPTION 'bf_migrator must be a non-superuser role without BYPASSRLS';
          END IF;
          IF pg_has_role('bf_runtime', 'bf_migrator', 'MEMBER') THEN
            RAISE EXCEPTION 'bf_runtime must not inherit or assume the migration role';
          END IF;
        END;
        $$;
        """
    )


def _enable_company_rls(table_name: str) -> None:
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

    op.create_table(
        "operations_event_streams",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_event_key", sa.String(length=160), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(btrim(source_event_key)) BETWEEN 1 AND 160",
            name="ck_operations_event_stream_key",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_operations_event_stream_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_operations_event_stream_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_operations_event_stream_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operations_event_streams"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_operations_event_stream_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "id",
            name="uq_operations_event_stream_source_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "source_event_key",
            name="uq_operations_event_stream_source_key",
        ),
    )
    op.create_index(
        "ix_operations_event_stream_source_created",
        "operations_event_streams",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )

    op.create_table(
        "operations_event_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("previous_event_hash", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("source_profile_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("source_subject_reference", sa.String(length=160), nullable=False),
        sa.Column("process_reference", sa.String(length=160), nullable=True),
        sa.Column("state_before", sa.String(length=128), nullable=True),
        sa.Column("state_after", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("canonical_record_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("canonical_record_revision", sa.Integer(), nullable=True),
        sa.Column("canonical_attributes_hash", sa.String(length=64), nullable=True),
        sa.Column("correction_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_operations_event_version_number"),
        sa.CheckConstraint(
            "evidence_kind IN ('manual_assertion', 'canonical_record_revision')",
            name="ck_operations_event_version_evidence_kind",
        ),
        sa.CheckConstraint(
            "(evidence_kind = 'manual_assertion' AND canonical_record_id IS NULL "
            "AND canonical_record_revision IS NULL AND canonical_attributes_hash IS NULL) OR "
            "(evidence_kind = 'canonical_record_revision' AND canonical_record_id IS NOT NULL "
            "AND canonical_record_revision IS NOT NULL "
            "AND canonical_attributes_hash ~ '^[0-9a-f]{64}$')",
            name="ck_operations_event_version_evidence_reference",
        ),
        sa.CheckConstraint(
            "(version = 1 AND previous_version_id IS NULL AND previous_event_hash IS NULL "
            "AND correction_reason IS NULL) OR "
            "(version > 1 AND previous_version_id IS NOT NULL "
            "AND previous_event_hash ~ '^[0-9a-f]{64}$' "
            "AND length(btrim(correction_reason)) BETWEEN 10 AND 500)",
            name="ck_operations_event_version_correction",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_operations_event_version_hashes",
        ),
        sa.CheckConstraint(
            "length(btrim(event_type)) BETWEEN 1 AND 128",
            name="ck_operations_event_version_type",
        ),
        sa.CheckConstraint(
            "length(btrim(source_subject_reference)) BETWEEN 1 AND 160",
            name="ck_operations_event_version_subject",
        ),
        sa.CheckConstraint(
            "process_reference IS NULL OR length(btrim(process_reference)) BETWEEN 1 AND 160",
            name="ck_operations_event_version_process",
        ),
        sa.CheckConstraint(
            "state_before IS NULL OR length(btrim(state_before)) BETWEEN 1 AND 128",
            name="ck_operations_event_version_state_before",
        ),
        sa.CheckConstraint(
            "state_after IS NULL OR length(btrim(state_after)) BETWEEN 1 AND 128",
            name="ck_operations_event_version_state_after",
        ),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 1000",
            name="ck_operations_event_version_description",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_operations_event_version_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id"],
            [
                "operations_event_streams.tenant_id",
                "operations_event_streams.company_id",
                "operations_event_streams.source_id",
                "operations_event_streams.id",
            ],
            name="fk_operations_event_version_stream",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            name="fk_operations_event_version_source_profile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id", "previous_version_id"],
            [
                "operations_event_versions.tenant_id",
                "operations_event_versions.company_id",
                "operations_event_versions.source_id",
                "operations_event_versions.event_id",
                "operations_event_versions.id",
            ],
            name="fk_operations_event_version_previous",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "company_id",
                "source_id",
                "canonical_record_id",
                "canonical_record_revision",
            ],
            [
                "canonical_business_record_revisions.tenant_id",
                "canonical_business_record_revisions.company_id",
                "canonical_business_record_revisions.source_id",
                "canonical_business_record_revisions.record_id",
                "canonical_business_record_revisions.revision",
            ],
            name="fk_operations_event_version_canonical_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_operations_event_version_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operations_event_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_operations_event_version_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "version",
            name="uq_operations_event_version_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "id",
            name="uq_operations_event_version_event_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_operations_event_version_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "previous_version_id",
            name="uq_operations_event_version_previous",
        ),
    )
    op.create_index(
        "ix_operations_event_version_source_time",
        "operations_event_versions",
        ["tenant_id", "company_id", "source_id", "occurred_at"],
    )
    op.create_index(
        "ix_operations_event_version_stream",
        "operations_event_versions",
        ["tenant_id", "company_id", "source_id", "event_id", "version"],
    )

    op.create_table(
        "operations_event_current",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id"],
            [
                "operations_event_streams.tenant_id",
                "operations_event_streams.company_id",
                "operations_event_streams.source_id",
                "operations_event_streams.id",
            ],
            name="fk_operations_event_current_stream",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id", "event_version_id"],
            [
                "operations_event_versions.tenant_id",
                "operations_event_versions.company_id",
                "operations_event_versions.source_id",
                "operations_event_versions.event_id",
                "operations_event_versions.id",
            ],
            name="fk_operations_event_current_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "company_id", "source_id", "event_id", name="pk_operations_event_current"
        ),
    )
    op.create_index(
        "ix_operations_event_current_source_time",
        "operations_event_current",
        ["tenant_id", "company_id", "source_id", "occurred_at", "event_id"],
    )

    op.create_table(
        "operations_analysis_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("result_snapshot", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("end_at > start_at", name="ck_operations_analysis_run_period"),
        sa.CheckConstraint(
            "input_count BETWEEN 0 AND 500", name="ck_operations_analysis_run_input_count"
        ),
        sa.CheckConstraint(
            "length(calculation_version) BETWEEN 1 AND 64",
            name="ck_operations_analysis_run_calculation_version",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_operations_analysis_run_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_operations_analysis_run_input_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_operations_analysis_run_result_object",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_operations_analysis_run_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_operations_analysis_run_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_operations_analysis_run_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operations_analysis_runs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_operations_analysis_run_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_operations_analysis_run_idempotency",
        ),
    )
    op.create_index(
        "ix_operations_analysis_run_source_created",
        "operations_analysis_runs",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )

    for table_name in TABLES:
        _enable_company_rls(table_name)

    op.execute(
        """CREATE FUNCTION businessflow_operations_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'operations evidence is append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_operations_append_only';
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_operations_stream_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          session_user_id uuid;
          tenant_context uuid;
          company_context uuid;
        BEGIN
          IF session_user <> 'bf_runtime' THEN
            RAISE EXCEPTION 'operations streams must be created by the runtime role'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_stream_runtime';
          END IF;
          session_user_id := NULLIF(current_setting('app.user_id', true), '')::uuid;
          tenant_context := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
          company_context := NULLIF(current_setting('app.company_id', true), '')::uuid;
          IF session_user_id IS DISTINCT FROM NEW.created_by_user_id
             OR tenant_context IS DISTINCT FROM NEW.tenant_id
             OR company_context IS DISTINCT FROM NEW.company_id THEN
            RAISE EXCEPTION 'operations stream actor or scope does not match verified context'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_stream_context';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.company_data_sources AS source
            WHERE source.tenant_id = NEW.tenant_id
              AND source.company_id = NEW.company_id
              AND source.id = NEW.source_id
              AND source.dataset_type = 'universal_records'
          ) THEN
            RAISE EXCEPTION 'operations stream requires a registered Universal Data source'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_stream_source';
          END IF;
          RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_operations_event_version_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          session_user_id uuid;
          tenant_context uuid;
          company_context uuid;
          profile_type text;
          profile_status text;
          profile_authority text;
          latest_profile_id uuid;
          parent_version integer;
          parent_hash text;
          linked_entity_type text;
          linked_attributes_hash text;
        BEGIN
          IF session_user <> 'bf_runtime' THEN
            RAISE EXCEPTION 'operations versions must be created by the runtime role'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_version_runtime';
          END IF;
          session_user_id := NULLIF(current_setting('app.user_id', true), '')::uuid;
          tenant_context := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
          company_context := NULLIF(current_setting('app.company_id', true), '')::uuid;
          IF session_user_id IS DISTINCT FROM NEW.created_by_user_id
             OR tenant_context IS DISTINCT FROM NEW.tenant_id
             OR company_context IS DISTINCT FROM NEW.company_id THEN
            RAISE EXCEPTION 'operations version actor or scope does not match verified context'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_version_context';
          END IF;

          SELECT profile.source_type, profile.source_status,
                 profile.authority_classification
          INTO profile_type, profile_status, profile_authority
          FROM public.company_data_source_profile_versions AS profile
          WHERE profile.tenant_id = NEW.tenant_id
            AND profile.company_id = NEW.company_id
            AND profile.source_id = NEW.source_id
            AND profile.id = NEW.source_profile_version_id;
          IF NOT FOUND OR profile_status <> 'enabled' THEN
            RAISE EXCEPTION 'operations source profile is missing or disabled'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_source_profile';
          END IF;
          SELECT profile.id INTO latest_profile_id
          FROM public.company_data_source_profile_versions AS profile
          WHERE profile.tenant_id = NEW.tenant_id
            AND profile.company_id = NEW.company_id
            AND profile.source_id = NEW.source_id
          ORDER BY profile.version DESC LIMIT 1;
          IF latest_profile_id IS DISTINCT FROM NEW.source_profile_version_id THEN
            RAISE EXCEPTION 'operations event must bind the current source profile version'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_source_profile';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.universal_source_lifecycle_events AS lifecycle
            WHERE lifecycle.tenant_id = NEW.tenant_id
              AND lifecycle.company_id = NEW.company_id
              AND lifecycle.source_id = NEW.source_id
              AND lifecycle.event_type = 'revoked'
          ) THEN
            RAISE EXCEPTION 'revoked sources cannot receive new operations observations'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_source_revoked';
          END IF;

          IF NEW.evidence_kind = 'manual_assertion' THEN
            IF profile_type <> 'manual_entry' OR profile_authority <> 'manual_assertion' THEN
              RAISE EXCEPTION 'manual observations require a manual assertion source profile'
                USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_manual_source';
            END IF;
          ELSIF NEW.evidence_kind = 'canonical_record_revision' THEN
            SELECT record.entity_type, revision.attributes_hash
            INTO linked_entity_type, linked_attributes_hash
            FROM public.canonical_business_records AS record
            JOIN public.canonical_business_record_revisions AS revision
              ON revision.tenant_id = record.tenant_id
             AND revision.company_id = record.company_id
             AND revision.source_id = record.source_id
             AND revision.record_id = record.id
             AND revision.revision = NEW.canonical_record_revision
            WHERE record.tenant_id = NEW.tenant_id
              AND record.company_id = NEW.company_id
              AND record.source_id = NEW.source_id
              AND record.id = NEW.canonical_record_id;
            IF NOT FOUND OR linked_entity_type NOT IN ('business_activity', 'operational_record')
               OR linked_attributes_hash IS DISTINCT FROM NEW.canonical_attributes_hash THEN
              RAISE EXCEPTION 'operations evidence is not an eligible same-source revision'
                USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_canonical_evidence';
            END IF;
          ELSE
            RAISE EXCEPTION 'operations evidence kind is unsupported'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_evidence_kind';
          END IF;

          IF NEW.version = 1 THEN
            IF NEW.previous_version_id IS NOT NULL OR NEW.previous_event_hash IS NOT NULL
               OR NEW.correction_reason IS NOT NULL THEN
              RAISE EXCEPTION 'initial operations event version cannot have a predecessor'
                USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_initial';
            END IF;
          ELSE
            SELECT previous.version, previous.event_hash
            INTO parent_version, parent_hash
            FROM public.operations_event_versions AS previous
            WHERE previous.tenant_id = NEW.tenant_id
              AND previous.company_id = NEW.company_id
              AND previous.source_id = NEW.source_id
              AND previous.event_id = NEW.event_id
              AND previous.id = NEW.previous_version_id;
            IF NOT FOUND OR parent_version <> NEW.version - 1
               OR parent_hash IS DISTINCT FROM NEW.previous_event_hash THEN
              RAISE EXCEPTION 'operations event correction must append to the exact prior version'
                USING ERRCODE = '23514', CONSTRAINT = 'operations_event_version_predecessor';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_operations_event_current_from_version() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
          IF session_user <> 'bf_runtime' THEN
            RAISE EXCEPTION 'operations current pointers are maintained by the runtime role'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_current_runtime';
          END IF;
          INSERT INTO public.operations_event_current (
            tenant_id, company_id, source_id, event_id, event_version_id, occurred_at
          ) VALUES (
            NEW.tenant_id, NEW.company_id, NEW.source_id, NEW.event_id, NEW.id, NEW.occurred_at
          )
          ON CONFLICT (tenant_id, company_id, source_id, event_id) DO UPDATE
          SET event_version_id = EXCLUDED.event_version_id,
              occurred_at = EXCLUDED.occurred_at;
          RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_operations_event_current_guard() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          tenant_context uuid;
          company_context uuid;
          latest_version_id uuid;
          latest_occurred_at timestamptz;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'operations current pointers cannot be deleted directly'
              USING ERRCODE = '55000', CONSTRAINT = 'operations_event_current_delete';
          END IF;
          IF session_user <> 'bf_runtime' THEN
            RAISE EXCEPTION 'operations current pointers require the runtime role'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_current_runtime';
          END IF;
          tenant_context := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
          company_context := NULLIF(current_setting('app.company_id', true), '')::uuid;
          IF tenant_context IS DISTINCT FROM NEW.tenant_id
             OR company_context IS DISTINCT FROM NEW.company_id THEN
            RAISE EXCEPTION 'operations current pointer scope does not match verified context'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_event_current_context';
          END IF;
          IF TG_OP = 'UPDATE'
             AND (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.company_id IS DISTINCT FROM OLD.company_id
               OR NEW.source_id IS DISTINCT FROM OLD.source_id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id) THEN
            RAISE EXCEPTION 'operations current pointer identity cannot be changed'
              USING ERRCODE = '55000', CONSTRAINT = 'operations_event_current_identity';
          END IF;
          SELECT version.id, version.occurred_at
          INTO latest_version_id, latest_occurred_at
          FROM public.operations_event_versions AS version
          WHERE version.tenant_id = NEW.tenant_id
            AND version.company_id = NEW.company_id
            AND version.source_id = NEW.source_id
            AND version.event_id = NEW.event_id
          ORDER BY version.version DESC
          LIMIT 1;
          IF NOT FOUND OR latest_version_id IS DISTINCT FROM NEW.event_version_id
             OR latest_occurred_at IS DISTINCT FROM NEW.occurred_at THEN
            RAISE EXCEPTION 'operations current pointer must reference the latest event version'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_current_latest_version';
          END IF;
          RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_operations_stream_has_initial_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM public.operations_event_versions AS version
            WHERE version.tenant_id = NEW.tenant_id
              AND version.company_id = NEW.company_id
              AND version.source_id = NEW.source_id
              AND version.event_id = NEW.id
              AND version.version = 1
          ) THEN
            RAISE EXCEPTION 'operations event streams require an initial immutable version'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_event_stream_initial_version';
          END IF;
          RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_operations_analysis_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          session_user_id uuid;
          tenant_context uuid;
          company_context uuid;
        BEGIN
          IF session_user <> 'bf_runtime' THEN
            RAISE EXCEPTION 'operations analyses must be created by the runtime role'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_analysis_run_runtime';
          END IF;
          session_user_id := NULLIF(current_setting('app.user_id', true), '')::uuid;
          tenant_context := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
          company_context := NULLIF(current_setting('app.company_id', true), '')::uuid;
          IF session_user_id IS DISTINCT FROM NEW.created_by_user_id
             OR tenant_context IS DISTINCT FROM NEW.tenant_id
             OR company_context IS DISTINCT FROM NEW.company_id THEN
            RAISE EXCEPTION 'operations analysis actor or scope does not match verified context'
              USING ERRCODE = '42501', CONSTRAINT = 'operations_analysis_run_context';
          END IF;
          IF NEW.calculation_version <> 'operations-observations-v1'
             OR NEW.input_snapshot->>'calculation_version' IS DISTINCT FROM NEW.calculation_version
             OR NEW.input_snapshot->'scope'->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
             OR NEW.input_snapshot->'scope'->>'company_id' IS DISTINCT FROM NEW.company_id::text
             OR NEW.input_snapshot->'scope'->>'source_id' IS DISTINCT FROM NEW.source_id::text
             OR NEW.input_snapshot->'period'->>'start_at' IS NULL
             OR NEW.input_snapshot->'period'->>'end_at' IS NULL
             OR (NEW.input_snapshot->'period'->>'start_at')::timestamptz
                IS DISTINCT FROM NEW.start_at
             OR (NEW.input_snapshot->'period'->>'end_at')::timestamptz
                IS DISTINCT FROM NEW.end_at
             OR jsonb_typeof(NEW.input_snapshot->'events') IS DISTINCT FROM 'array'
             OR jsonb_array_length(NEW.input_snapshot->'events') IS DISTINCT FROM NEW.input_count
             OR NEW.result_snapshot->>'observed_event_count' IS DISTINCT FROM NEW.input_count::text
             OR (NEW.input_count = 0 AND NEW.result_snapshot->>'status' IS DISTINCT FROM
                 'no_matching_evidence')
             OR (NEW.input_count > 0 AND NEW.result_snapshot->>'status' IS DISTINCT FROM
                 'observations_available') THEN
            RAISE EXCEPTION 'operations analysis snapshot is inconsistent with its scope or result'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_analysis_run_snapshot';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.universal_source_lifecycle_events AS lifecycle
            WHERE lifecycle.tenant_id = NEW.tenant_id
              AND lifecycle.company_id = NEW.company_id
              AND lifecycle.source_id = NEW.source_id
              AND lifecycle.event_type = 'revoked'
          ) THEN
            RAISE EXCEPTION 'new operations analyses are not permitted for a revoked source'
              USING ERRCODE = '23514', CONSTRAINT = 'operations_analysis_run_source_revoked';
          END IF;
          RETURN NEW;
        END;
        $$"""
    )

    op.execute(
        """CREATE TRIGGER trg_operations_event_stream_insert_guard
        BEFORE INSERT ON operations_event_streams
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_stream_insert_guard()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_event_stream_append_only
        BEFORE UPDATE OR DELETE ON operations_event_streams
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_append_only()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_operations_event_stream_initial_version
        AFTER INSERT ON operations_event_streams DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_stream_has_initial_version()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_event_version_guard
        BEFORE INSERT ON operations_event_versions
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_event_version_guard()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_event_versions_append_only
        BEFORE UPDATE OR DELETE ON operations_event_versions
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_append_only()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_event_current_from_version
        AFTER INSERT ON operations_event_versions
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_event_current_from_version()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_event_current_guard
        BEFORE INSERT OR UPDATE OR DELETE ON operations_event_current
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_event_current_guard()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_analysis_run_guard
        BEFORE INSERT ON operations_analysis_runs
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_analysis_guard()"""
    )
    op.execute(
        """CREATE TRIGGER trg_operations_analysis_runs_append_only
        BEFORE UPDATE OR DELETE ON operations_analysis_runs
        FOR EACH ROW EXECUTE FUNCTION businessflow_operations_append_only()"""
    )

    for table_name in TABLES:
        op.execute(sa.text(f"REVOKE ALL ON {table_name} FROM PUBLIC, bf_runtime"))
        op.execute(sa.text(f"GRANT SELECT ON {table_name} TO bf_runtime"))
        op.execute(
            sa.text(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table_name} FROM bf_runtime")
        )
    for table_name in RUNTIME_INSERT_TABLES:
        op.execute(sa.text(f"GRANT INSERT ON {table_name} TO bf_runtime"))
    op.execute(
        "GRANT UPDATE (event_version_id, occurred_at) ON operations_event_current TO bf_runtime"
    )


def downgrade() -> None:
    _require_migrator()
    # The migration connection has no tenant context. Disable RLS transactionally so
    # forced-RLS cannot hide retained evidence from these safety checks. If evidence
    # is found, the raised exception rolls back both this change and the whole downgrade.
    for table_name in TABLES:
        op.execute(sa.text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY"))
    op.execute(
        """DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.operations_event_streams)
             OR EXISTS (SELECT 1 FROM public.operations_event_versions)
             OR EXISTS (SELECT 1 FROM public.operations_event_current)
             OR EXISTS (SELECT 1 FROM public.operations_analysis_runs) THEN
            RAISE EXCEPTION 'refusing to discard Operations Intelligence evidence during downgrade';
          END IF;
        END;
        $$"""
    )
    op.drop_table("operations_analysis_runs")
    op.drop_table("operations_event_current")
    op.drop_table("operations_event_versions")
    op.drop_table("operations_event_streams")
    op.execute("DROP FUNCTION businessflow_operations_event_current_guard()")
    op.execute("DROP FUNCTION businessflow_operations_event_current_from_version()")
    op.execute("DROP FUNCTION businessflow_operations_analysis_guard()")
    op.execute("DROP FUNCTION businessflow_operations_stream_has_initial_version()")
    op.execute("DROP FUNCTION businessflow_operations_event_version_guard()")
    op.execute("DROP FUNCTION businessflow_operations_stream_insert_guard()")
    op.execute("DROP FUNCTION businessflow_operations_append_only()")
