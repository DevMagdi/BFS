from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_universal_data_retention"
down_revision = "0027_business_reports"
branch_labels = None
depends_on = None


RETENTION_TABLES = (
    "universal_retention_policy_versions",
    "universal_retention_policy_approvals",
    "universal_retention_policy_revocations",
    "universal_source_lifecycle_events",
    "universal_ingestion_purge_tombstones",
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


def _company_rls(table_name: str) -> None:
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
        "universal_retention_policy_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="ck_udf_retention_policy_version"),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 36500", name="ck_udf_retention_policy_days"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_udf_retention_policy_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_retention_policy_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_retention_policy_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_udf_retention_policy_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "version", name="uq_udf_retention_policy_version"
        ),
    )
    op.create_index(
        "ix_udf_retention_policy_latest",
        "universal_retention_policy_versions",
        ["tenant_id", "company_id", "version"],
    )
    _company_rls("universal_retention_policy_versions")

    op.create_table(
        "universal_retention_policy_approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_version_id"],
            [
                "universal_retention_policy_versions.tenant_id",
                "universal_retention_policy_versions.company_id",
                "universal_retention_policy_versions.id",
            ],
            name="fk_udf_retention_approval_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approved_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_retention_approval_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_retention_policy_approvals"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "policy_version_id",
            name="uq_udf_retention_policy_approval",
        ),
    )
    op.create_index(
        "ix_udf_retention_approvals_policy",
        "universal_retention_policy_approvals",
        ["tenant_id", "company_id", "policy_version_id"],
    )
    _company_rls("universal_retention_policy_approvals")

    op.create_table(
        "universal_retention_policy_revocations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revoked_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_version_id"],
            [
                "universal_retention_policy_versions.tenant_id",
                "universal_retention_policy_versions.company_id",
                "universal_retention_policy_versions.id",
            ],
            name="fk_udf_retention_revocation_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revoked_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_retention_revocation_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_retention_policy_revocations"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "policy_version_id",
            name="uq_udf_retention_policy_revocation",
        ),
    )
    _company_rls("universal_retention_policy_revocations")

    op.create_table(
        "universal_source_lifecycle_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event_type IN ('revoked', 'purged')", name="ck_udf_source_lifecycle_type"
        ),
        sa.CheckConstraint(
            "(event_type = 'revoked' AND reason_code IS NOT NULL AND reason_code IN "
            "('owner_request', 'source_replaced', 'contract_ended', 'security_concern', 'other')) "
            "OR (event_type = 'purged' AND reason_code IS NULL)",
            name="ck_udf_source_lifecycle_reason",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_source_lifecycle_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_source_lifecycle_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_source_lifecycle_events"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_udf_source_lifecycle_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_type",
            name="uq_udf_source_lifecycle_event",
        ),
    )
    op.create_index(
        "ix_udf_source_lifecycle_source",
        "universal_source_lifecycle_events",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )
    _company_rls("universal_source_lifecycle_events")

    op.create_table(
        "universal_ingestion_purge_tombstones",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("purged_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "purged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_udf_purge_tombstone_key_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_purge_tombstone_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_version_id"],
            [
                "universal_retention_policy_versions.tenant_id",
                "universal_retention_policy_versions.company_id",
                "universal_retention_policy_versions.id",
            ],
            name="fk_udf_purge_tombstone_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "purged_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_purge_tombstone_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_ingestion_purge_tombstones"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "ingestion_id",
            name="uq_udf_purge_tombstone_ingestion",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_udf_purge_tombstone_idempotency",
        ),
    )
    op.create_index(
        "ix_udf_purge_tombstones_source_purged",
        "universal_ingestion_purge_tombstones",
        ["tenant_id", "company_id", "source_id", "purged_at"],
    )
    _company_rls("universal_ingestion_purge_tombstones")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION businessflow_udf_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE'
             AND current_user = 'bf_migrator'
             AND current_setting('app.universal_data_purge_mode', true) = 'on' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'Universal Data evidence and version rows are append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_append_only';
        END;
        $$;

        CREATE OR REPLACE FUNCTION businessflow_udf_ingestion_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE'
             AND current_user = 'bf_migrator'
             AND current_setting('app.universal_data_purge_mode', true) = 'on' THEN
            RETURN OLD;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Universal Data ingestion executions cannot be deleted'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF OLD.status = 'promoted' THEN
            RAISE EXCEPTION 'Promoted Universal Data executions are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
             OR NEW.source_profile_version_id IS DISTINCT FROM OLD.source_profile_version_id
             OR NEW.initiated_by_user_id IS DISTINCT FROM OLD.initiated_by_user_id
             OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
             OR NEW.file_name IS DISTINCT FROM OLD.file_name
             OR NEW.file_format IS DISTINCT FROM OLD.file_format
             OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
             OR NEW.parse_options IS DISTINCT FROM OLD.parse_options
             OR NEW.inspection IS DISTINCT FROM OLD.inspection
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Universal Data ingestion identity and input are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF NEW.status NOT IN (
            'staged', 'mapped', 'validated', 'review_required', 'blocked', 'failed', 'promoted'
          ) THEN
            RAISE EXCEPTION 'Invalid Universal Data ingestion state'
              USING ERRCODE = '23514', CONSTRAINT = 'ck_udf_ingestion_status';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_udf_retention_policy_version_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE latest_version integer;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'universal-retention:' || NEW.tenant_id::text || ':' || NEW.company_id::text, 0
          ));
          IF NEW.created_by_user_id IS DISTINCT FROM
             NULLIF(current_setting('app.user_id', true), '')::uuid THEN
            RAISE EXCEPTION 'retention policy actor must match the verified request context';
          END IF;
          SELECT COALESCE(MAX(version), 0)
          INTO latest_version
          FROM public.universal_retention_policy_versions
          WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id;
          IF NEW.version <> latest_version + 1 THEN
            RAISE EXCEPTION 'retention policy versions must increase sequentially';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_universal_retention_policy_version_guard
          BEFORE INSERT ON universal_retention_policy_versions
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_retention_policy_version_guard();

        CREATE FUNCTION businessflow_udf_retention_approval_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE policy_creator uuid;
                latest_version integer;
                approval_version integer;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'universal-retention:' || NEW.tenant_id::text || ':' || NEW.company_id::text, 0
          ));
          IF NEW.approved_by_user_id IS DISTINCT FROM
             NULLIF(current_setting('app.user_id', true), '')::uuid THEN
            RAISE EXCEPTION 'retention approval actor must match the verified request context';
          END IF;
          SELECT created_by_user_id, version
          INTO policy_creator, approval_version
          FROM public.universal_retention_policy_versions
          WHERE tenant_id = NEW.tenant_id
            AND company_id = NEW.company_id
            AND id = NEW.policy_version_id;
          IF policy_creator IS NULL THEN
            RAISE EXCEPTION 'retention policy version is unavailable';
          END IF;
          IF NEW.approved_by_user_id = policy_creator THEN
            RAISE EXCEPTION 'retention policy approval requires a different Company member';
          END IF;
          SELECT COALESCE(MAX(version), 0)
          INTO latest_version
          FROM public.universal_retention_policy_versions
          WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id;
          IF approval_version <> latest_version THEN
            RAISE EXCEPTION 'only the latest retention policy version can be approved';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_universal_retention_policy_approval_guard
          BEFORE INSERT ON universal_retention_policy_approvals
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_retention_approval_guard();

        CREATE FUNCTION businessflow_udf_retention_revocation_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'universal-retention:' || NEW.tenant_id::text || ':' || NEW.company_id::text, 0
          ));
          IF NEW.revoked_by_user_id IS DISTINCT FROM
             NULLIF(current_setting('app.user_id', true), '')::uuid THEN
            RAISE EXCEPTION 'retention revocation actor must match the verified request context';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_universal_retention_policy_revocation_guard
          BEFORE INSERT ON universal_retention_policy_revocations
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_retention_revocation_guard();

        CREATE FUNCTION businessflow_udf_purge_tombstone_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF current_user <> 'bf_migrator'
             OR session_user <> 'bf_runtime'
             OR current_setting('app.universal_data_purge_mode', true) IS DISTINCT FROM 'on'
             OR NEW.purged_by_user_id IS DISTINCT FROM
                NULLIF(current_setting('app.user_id', true), '')::uuid THEN
            RAISE EXCEPTION 'purge tombstones may be written only by the controlled purge function';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.universal_ingestions AS ingestion
            WHERE ingestion.tenant_id = NEW.tenant_id
              AND ingestion.company_id = NEW.company_id
              AND ingestion.source_id = NEW.source_id
              AND ingestion.id = NEW.ingestion_id
              AND ingestion.idempotency_key_hash = NEW.idempotency_key_hash
          ) THEN
            RAISE EXCEPTION 'purge tombstone must match a live ingestion';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_universal_ingestion_purge_tombstone_guard
          BEFORE INSERT ON universal_ingestion_purge_tombstones
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_purge_tombstone_guard();

        CREATE FUNCTION businessflow_udf_source_lifecycle_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'universal-source:' || NEW.tenant_id::text || ':' || NEW.company_id::text || ':'
            || NEW.source_id::text, 0
          ));
          IF NEW.actor_user_id IS DISTINCT FROM
             NULLIF(current_setting('app.user_id', true), '')::uuid THEN
            RAISE EXCEPTION 'source lifecycle actor must match the verified request context';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.company_data_sources AS source
            WHERE source.tenant_id = NEW.tenant_id
              AND source.company_id = NEW.company_id
              AND source.id = NEW.source_id
              AND source.dataset_type = 'universal_records'
          ) THEN
            RAISE EXCEPTION 'source lifecycle events are limited to Universal Data sources';
          END IF;
          IF NEW.event_type = 'purged' THEN
            IF current_user <> 'bf_migrator'
               OR current_setting('app.universal_data_purge_mode', true) IS DISTINCT FROM 'on' THEN
              RAISE EXCEPTION 'source purge completion requires the controlled purge function';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM public.universal_source_lifecycle_events
              WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                AND source_id = NEW.source_id AND event_type = 'revoked'
            ) THEN
              RAISE EXCEPTION 'source evidence cannot be finalized before revocation';
            END IF;
            IF EXISTS (
                 SELECT 1 FROM public.universal_ingestions
                 WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                   AND source_id = NEW.source_id
               )
               OR EXISTS (
                 SELECT 1 FROM public.universal_source_snapshots
                 WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                   AND source_id = NEW.source_id
               )
               OR EXISTS (
                 SELECT 1 FROM public.canonical_business_records
                 WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                   AND source_id = NEW.source_id
               ) THEN
              RAISE EXCEPTION 'source evidence cannot be finalized while retained data remains';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_universal_source_lifecycle_guard
          BEFORE INSERT ON universal_source_lifecycle_events
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_source_lifecycle_guard();
        """
    )

    for table in RETENTION_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION businessflow_udf_append_only()"
            )
        )

    op.execute(
        """
        CREATE FUNCTION businessflow_purge_universal_ingestions(
          p_tenant_id uuid,
          p_company_id uuid,
          p_source_id uuid,
          p_policy_version_id uuid,
          p_ingestion_ids uuid[],
          p_actor_user_id uuid
        ) RETURNS TABLE (
          purged_ingestion_count integer,
          purged_row_count integer,
          purged_validation_count integer,
          purged_event_count integer,
          purged_link_count integer,
          purged_snapshot_count integer,
          purged_snapshot_bytes bigint
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = on
        AS $$
        DECLARE policy_days integer;
                cutoff_at timestamptz;
                matching_count integer;
                snapshot_ids uuid[];
                tenant_context uuid;
                company_context uuid;
                actor_context uuid;
        BEGIN
          IF session_user <> 'bf_runtime' THEN
            RAISE EXCEPTION 'purge may only be called by the restricted runtime role';
          END IF;
          tenant_context := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
          company_context := NULLIF(current_setting('app.company_id', true), '')::uuid;
          actor_context := NULLIF(current_setting('app.user_id', true), '')::uuid;
          IF p_tenant_id IS DISTINCT FROM tenant_context
             OR p_company_id IS DISTINCT FROM company_context
             OR p_actor_user_id IS DISTINCT FROM actor_context THEN
            RAISE EXCEPTION 'purge scope and actor must match the verified request context';
          END IF;
          IF p_ingestion_ids IS NULL OR cardinality(p_ingestion_ids) NOT BETWEEN 1 AND 100
             OR array_position(p_ingestion_ids, NULL) IS NOT NULL THEN
            RAISE EXCEPTION 'purge batches must contain between one and 100 ingestion IDs';
          END IF;
          IF (SELECT count(DISTINCT selected.ingestion_id)
              FROM unnest(p_ingestion_ids) AS selected(ingestion_id))
             <> cardinality(p_ingestion_ids) THEN
            RAISE EXCEPTION 'purge batches may not contain duplicate ingestion IDs';
          END IF;

          PERFORM pg_advisory_xact_lock(hashtextextended(
            'universal-retention:' || p_tenant_id::text || ':' || p_company_id::text, 0
          ));
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'universal-source:' || p_tenant_id::text || ':' || p_company_id::text || ':'
            || p_source_id::text, 0
          ));
          IF NOT EXISTS (
            SELECT 1 FROM public.company_data_sources AS source
            WHERE source.tenant_id = p_tenant_id
              AND source.company_id = p_company_id
              AND source.id = p_source_id
              AND source.dataset_type = 'universal_records'
          ) THEN
            RAISE EXCEPTION 'Universal Data source is unavailable in this Company';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.universal_source_lifecycle_events AS lifecycle
            WHERE lifecycle.tenant_id = p_tenant_id
              AND lifecycle.company_id = p_company_id
              AND lifecycle.source_id = p_source_id
              AND lifecycle.event_type = 'revoked'
          ) THEN
            RAISE EXCEPTION 'source must be explicitly revoked before evidence purge';
          END IF;

          SELECT policy.retention_days
          INTO policy_days
          FROM public.universal_retention_policy_versions AS policy
          WHERE policy.id = p_policy_version_id
            AND policy.tenant_id = p_tenant_id
            AND policy.company_id = p_company_id
            AND policy.version = (
              SELECT MAX(latest.version)
              FROM public.universal_retention_policy_versions AS latest
              WHERE latest.tenant_id = p_tenant_id AND latest.company_id = p_company_id
            )
            AND EXISTS (
              SELECT 1 FROM public.universal_retention_policy_approvals AS approval
              WHERE approval.tenant_id = p_tenant_id
                AND approval.company_id = p_company_id
                AND approval.policy_version_id = policy.id
                AND approval.approved_by_user_id <> policy.created_by_user_id
            )
            AND NOT EXISTS (
              SELECT 1 FROM public.universal_retention_policy_revocations AS revocation
              WHERE revocation.tenant_id = p_tenant_id
                AND revocation.company_id = p_company_id
                AND revocation.policy_version_id = policy.id
            );
          IF policy_days IS NULL THEN
            RAISE EXCEPTION 'an explicitly configured and approved latest policy is required';
          END IF;
          cutoff_at := clock_timestamp() - make_interval(days => policy_days);

          PERFORM 1
          FROM public.universal_ingestions AS ingestion
          WHERE ingestion.tenant_id = p_tenant_id
            AND ingestion.company_id = p_company_id
            AND ingestion.source_id = p_source_id
            AND ingestion.id = ANY(p_ingestion_ids)
          ORDER BY ingestion.id
          FOR UPDATE;

          SELECT count(DISTINCT ingestion.id)
          INTO matching_count
          FROM public.universal_ingestions AS ingestion
          WHERE ingestion.tenant_id = p_tenant_id
            AND ingestion.company_id = p_company_id
            AND ingestion.source_id = p_source_id
            AND ingestion.id = ANY(p_ingestion_ids)
            AND ingestion.created_at <= cutoff_at;
          IF matching_count <> cardinality(p_ingestion_ids) THEN
            RAISE EXCEPTION 'purge selection is stale or not past its approved cutoff';
          END IF;
          IF EXISTS (
               SELECT 1 FROM public.canonical_business_record_revisions AS revision
               WHERE revision.tenant_id = p_tenant_id AND revision.company_id = p_company_id
                 AND revision.source_id = p_source_id
                 AND revision.ingestion_id = ANY(p_ingestion_ids)
             )
             OR EXISTS (
               SELECT 1 FROM public.universal_ingestion_record_links AS link
               WHERE link.tenant_id = p_tenant_id AND link.company_id = p_company_id
                 AND link.ingestion_id = ANY(p_ingestion_ids)
                 AND link.canonical_record_id IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'downstream canonical record dependencies block source evidence purge';
          END IF;

          SELECT array_agg(DISTINCT ingestion.snapshot_id)
          INTO snapshot_ids
          FROM public.universal_ingestions AS ingestion
          WHERE ingestion.tenant_id = p_tenant_id
            AND ingestion.company_id = p_company_id
            AND ingestion.source_id = p_source_id
            AND ingestion.id = ANY(p_ingestion_ids);

          PERFORM set_config('app.universal_data_purge_mode', 'on', true);

          INSERT INTO public.universal_ingestion_purge_tombstones (
            id, tenant_id, company_id, source_id, ingestion_id, idempotency_key_hash,
            policy_version_id, purged_by_user_id
          )
          SELECT gen_random_uuid(), ingestion.tenant_id, ingestion.company_id,
                 ingestion.source_id, ingestion.id, ingestion.idempotency_key_hash,
                 p_policy_version_id, p_actor_user_id
          FROM public.universal_ingestions AS ingestion
          WHERE ingestion.tenant_id = p_tenant_id
            AND ingestion.company_id = p_company_id
            AND ingestion.source_id = p_source_id
            AND ingestion.id = ANY(p_ingestion_ids);

          DELETE FROM public.universal_row_validations AS validation
          WHERE validation.tenant_id = p_tenant_id AND validation.company_id = p_company_id
            AND validation.source_id = p_source_id
            AND validation.ingestion_id = ANY(p_ingestion_ids);
          GET DIAGNOSTICS purged_validation_count = ROW_COUNT;

          DELETE FROM public.universal_ingestion_record_links AS link
          WHERE link.tenant_id = p_tenant_id AND link.company_id = p_company_id
            AND link.ingestion_id = ANY(p_ingestion_ids);
          GET DIAGNOSTICS purged_link_count = ROW_COUNT;

          DELETE FROM public.universal_ingestion_events AS event
          WHERE event.tenant_id = p_tenant_id AND event.company_id = p_company_id
            AND event.ingestion_id = ANY(p_ingestion_ids);
          GET DIAGNOSTICS purged_event_count = ROW_COUNT;

          DELETE FROM public.universal_ingestion_rows AS row_data
          WHERE row_data.tenant_id = p_tenant_id AND row_data.company_id = p_company_id
            AND row_data.source_id = p_source_id
            AND row_data.ingestion_id = ANY(p_ingestion_ids);
          GET DIAGNOSTICS purged_row_count = ROW_COUNT;

          DELETE FROM public.universal_ingestions AS ingestion
          WHERE ingestion.tenant_id = p_tenant_id AND ingestion.company_id = p_company_id
            AND ingestion.source_id = p_source_id AND ingestion.id = ANY(p_ingestion_ids);
          GET DIAGNOSTICS purged_ingestion_count = ROW_COUNT;

          WITH deleted_snapshots AS (
            DELETE FROM public.universal_source_snapshots AS snapshot
            WHERE snapshot.tenant_id = p_tenant_id AND snapshot.company_id = p_company_id
              AND snapshot.source_id = p_source_id
              AND snapshot.id = ANY(snapshot_ids)
              AND NOT EXISTS (
                SELECT 1 FROM public.universal_ingestions AS remaining
                WHERE remaining.tenant_id = p_tenant_id AND remaining.company_id = p_company_id
                  AND remaining.source_id = p_source_id AND remaining.snapshot_id = snapshot.id
              )
              AND NOT EXISTS (
                SELECT 1 FROM public.canonical_business_record_revisions AS revision
                WHERE revision.tenant_id = p_tenant_id AND revision.company_id = p_company_id
                  AND revision.source_id = p_source_id AND revision.snapshot_id = snapshot.id
              )
            RETURNING snapshot.file_size_bytes
          )
          SELECT count(*)::integer, COALESCE(sum(file_size_bytes), 0)::bigint
          INTO purged_snapshot_count, purged_snapshot_bytes
          FROM deleted_snapshots;

          IF EXISTS (
            SELECT 1 FROM public.universal_source_lifecycle_events AS lifecycle
            WHERE lifecycle.tenant_id = p_tenant_id AND lifecycle.company_id = p_company_id
              AND lifecycle.source_id = p_source_id AND lifecycle.event_type = 'revoked'
          )
          AND NOT EXISTS (
            SELECT 1 FROM public.universal_ingestions AS ingestion
            WHERE ingestion.tenant_id = p_tenant_id AND ingestion.company_id = p_company_id
              AND ingestion.source_id = p_source_id
          )
          AND NOT EXISTS (
            SELECT 1 FROM public.universal_source_snapshots AS snapshot
            WHERE snapshot.tenant_id = p_tenant_id AND snapshot.company_id = p_company_id
              AND snapshot.source_id = p_source_id
          )
          AND NOT EXISTS (
            SELECT 1 FROM public.canonical_business_records AS record
            WHERE record.tenant_id = p_tenant_id AND record.company_id = p_company_id
              AND record.source_id = p_source_id
          )
          AND NOT EXISTS (
            SELECT 1 FROM public.universal_source_lifecycle_events AS lifecycle
            WHERE lifecycle.tenant_id = p_tenant_id AND lifecycle.company_id = p_company_id
              AND lifecycle.source_id = p_source_id AND lifecycle.event_type = 'purged'
          ) THEN
            INSERT INTO public.universal_source_lifecycle_events (
              id, tenant_id, company_id, source_id, event_type, reason_code, actor_user_id
            ) VALUES (
              gen_random_uuid(), p_tenant_id, p_company_id, p_source_id, 'purged', NULL,
              p_actor_user_id
            );
          END IF;

          RETURN NEXT;
        END;
        $$;
        REVOKE ALL ON FUNCTION businessflow_purge_universal_ingestions(
          uuid, uuid, uuid, uuid, uuid[], uuid
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION businessflow_purge_universal_ingestions(
          uuid, uuid, uuid, uuid, uuid[], uuid
        ) TO bf_runtime;
        """
    )

    op.execute("REVOKE ALL ON " + ", ".join(RETENTION_TABLES) + " FROM PUBLIC, bf_runtime")
    for table in RETENTION_TABLES:
        op.execute(sa.text(f"GRANT SELECT, INSERT ON {table} TO bf_runtime"))
        op.execute(sa.text(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM bf_runtime"))


def downgrade() -> None:
    _require_migrator()
    for table in RETENTION_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM universal_retention_policy_versions)
             OR EXISTS (SELECT 1 FROM universal_retention_policy_approvals)
             OR EXISTS (SELECT 1 FROM universal_retention_policy_revocations)
             OR EXISTS (SELECT 1 FROM universal_source_lifecycle_events)
             OR EXISTS (SELECT 1 FROM universal_ingestion_purge_tombstones) THEN
            RAISE EXCEPTION 'cannot downgrade while retention or lifecycle evidence exists';
          END IF;
        END;
        $$;
        """
    )
    for table in RETENTION_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        "DROP FUNCTION IF EXISTS businessflow_purge_universal_ingestions("
        "uuid, uuid, uuid, uuid, uuid[], uuid)"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_universal_source_lifecycle_guard "
        "ON universal_source_lifecycle_events"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_source_lifecycle_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_universal_retention_policy_approval_guard "
        "ON universal_retention_policy_approvals"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_retention_approval_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_universal_retention_policy_version_guard "
        "ON universal_retention_policy_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_retention_policy_version_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_universal_retention_policy_revocation_guard "
        "ON universal_retention_policy_revocations"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_retention_revocation_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_universal_ingestion_purge_tombstone_guard "
        "ON universal_ingestion_purge_tombstones"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_purge_tombstone_guard()")
    for table in RETENTION_TABLES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}"))
    for table in reversed(RETENTION_TABLES):
        op.drop_table(table)
    # Restore the original M19 append-only enforcement after the controlled purge capability
    # is removed. Downgrade is permitted only when no retention lifecycle data exists.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION businessflow_udf_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Universal Data evidence and version rows are append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_append_only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION businessflow_udf_ingestion_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Universal Data ingestion executions cannot be deleted'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF OLD.status = 'promoted' THEN
            RAISE EXCEPTION 'Promoted Universal Data executions are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
             OR NEW.source_profile_version_id IS DISTINCT FROM OLD.source_profile_version_id
             OR NEW.initiated_by_user_id IS DISTINCT FROM OLD.initiated_by_user_id
             OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
             OR NEW.file_name IS DISTINCT FROM OLD.file_name
             OR NEW.file_format IS DISTINCT FROM OLD.file_format
             OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
             OR NEW.parse_options IS DISTINCT FROM OLD.parse_options
             OR NEW.inspection IS DISTINCT FROM OLD.inspection
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Universal Data ingestion identity and input are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF NEW.status NOT IN (
            'staged', 'mapped', 'validated', 'review_required', 'blocked', 'failed', 'promoted'
          ) THEN
            RAISE EXCEPTION 'Invalid Universal Data ingestion state'
              USING ERRCODE = '23514', CONSTRAINT = 'ck_udf_ingestion_status';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$;
        """
    )
