"""Bind Core Financials source bytes and execution idempotency to immutable evidence.

Revision ID: 0023_core_financial_guardrails
Revises: 0022_bi_config_analysis
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_core_financial_guardrails"
down_revision = "0022_bi_config_analysis"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    _require_migrator()

    # Existing imports cannot have their source bytes reconstructed from mapped rows. Preserve
    # them as explicit legacy evidence rather than fabricating or silently backfilling originals.
    op.add_column(
        "core_financial_imports",
        sa.Column(
            "source_archive_status",
            sa.String(length=24),
            server_default="legacy_unavailable",
            nullable=False,
        ),
    )
    op.add_column(
        "core_financial_imports",
        sa.Column("original_content", postgresql.BYTEA(), nullable=True),
    )
    op.create_check_constraint(
        "ck_core_financial_imports_source_archive",
        "core_financial_imports",
        "(source_archive_status = 'retained' AND original_content IS NOT NULL "
        "AND octet_length(original_content) = file_size_bytes) OR "
        "(source_archive_status = 'legacy_unavailable' AND original_content IS NULL)",
    )

    # Old immutable runs remain verifiable with NULL idempotency fields. Every new run binds a
    # one-way key hash and a canonical requester/request fingerprint.
    op.add_column(
        "core_financial_runs",
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "core_financial_runs", sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_check_constraint(
        "ck_core_financial_runs_idempotency",
        "core_financial_runs",
        "(idempotency_key_hash IS NULL AND request_fingerprint IS NULL) OR "
        "(idempotency_key_hash IS NOT NULL AND request_fingerprint IS NOT NULL AND "
        "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND "
        "request_fingerprint ~ '^[0-9a-f]{64}$')",
    )
    op.create_index(
        "uq_core_financial_runs_idempotency",
        "core_financial_runs",
        ["tenant_id", "company_id", "idempotency_key_hash"],
        unique=True,
        postgresql_where=sa.text("idempotency_key_hash IS NOT NULL"),
    )
    op.execute(
        "COMMENT ON COLUMN core_financial_imports.original_content IS "
        "'Exact uploaded CSV bytes; immutable and protected by the import row tenant/company RLS.'"
    )
    op.execute(
        "COMMENT ON COLUMN core_financial_imports.source_archive_status IS "
        "'retained for new imports; legacy_unavailable only for pre-archive imports.'"
    )
    op.execute(
        "COMMENT ON COLUMN core_financial_runs.idempotency_key_hash IS "
        "'SHA-256 of the request Idempotency-Key; plaintext keys are never persisted.'"
    )


def downgrade() -> None:
    _require_migrator()

    # Migration owner queries must see all Companies even though the two tables FORCE RLS.
    # PostgreSQL rolls this transaction back, including NO FORCE, if the retention guard raises.
    op.execute("ALTER TABLE core_financial_imports NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core_financial_runs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM core_financial_imports
                WHERE source_archive_status = 'retained'
              ) OR EXISTS (
                SELECT 1 FROM core_financial_runs
                WHERE idempotency_key_hash IS NOT NULL
              ) THEN
                RAISE EXCEPTION
                  'Cannot downgrade while archived source bytes or idempotent runs remain';
              END IF;
            END;
            $$;
            """
        )
    )
    op.execute("ALTER TABLE core_financial_imports FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core_financial_runs FORCE ROW LEVEL SECURITY")

    op.drop_index("uq_core_financial_runs_idempotency", table_name="core_financial_runs")
    op.drop_constraint("ck_core_financial_runs_idempotency", "core_financial_runs", type_="check")
    op.drop_column("core_financial_runs", "request_fingerprint")
    op.drop_column("core_financial_runs", "idempotency_key_hash")
    op.drop_constraint(
        "ck_core_financial_imports_source_archive", "core_financial_imports", type_="check"
    )
    op.drop_column("core_financial_imports", "original_content")
    op.drop_column("core_financial_imports", "source_archive_status")
