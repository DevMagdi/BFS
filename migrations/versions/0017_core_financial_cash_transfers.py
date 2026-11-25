"""Support explicitly marked bank transfers in Core Financials imports.

Revision ID: 0017_core_financial_cash_transfers
Revises: 0016_core_financials
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_core_financial_cash_transfers"
down_revision = "0016_core_financials"
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
    op.drop_constraint(
        "ck_core_financial_records_event_type",
        "core_financial_records",
        type_="check",
    )
    op.create_check_constraint(
        "ck_core_financial_records_event_type",
        "core_financial_records",
        "event_type IS NULL OR event_type IN "
        "('normal', 'refund', 'reversal', 'adjustment', 'transfer')",
    )


def downgrade() -> None:
    _require_migrator()
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM core_financial_records WHERE event_type = 'transfer'
              ) THEN
                RAISE EXCEPTION
                  'cannot downgrade Core Financials while bank transfer evidence exists';
              END IF;
            END;
            $$;
            """
        )
    )
    op.drop_constraint(
        "ck_core_financial_records_event_type",
        "core_financial_records",
        type_="check",
    )
    op.create_check_constraint(
        "ck_core_financial_records_event_type",
        "core_financial_records",
        "event_type IS NULL OR event_type IN ('normal', 'refund', 'reversal', 'adjustment')",
    )
