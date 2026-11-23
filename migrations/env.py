from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from businessflow import models  # noqa: F401 - registers model metadata
from businessflow.company_context import models as company_context_models  # noqa: F401
from businessflow.company_data import models as company_data_models  # noqa: F401
from businessflow.data_imports import models as data_import_models  # noqa: F401
from businessflow.database import Base
from businessflow.finance_sources import models as finance_source_models  # noqa: F401
from businessflow.financial_assessments import models as financial_assessment_models  # noqa: F401
from businessflow.metrics import models as metrics_models  # noqa: F401
from businessflow.position_reviews import models as position_review_models  # noqa: F401
from businessflow.project_assessments import models as project_assessment_models  # noqa: F401
from businessflow.project_decisions import models as project_decision_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

migration_url = os.environ.get("MIGRATION_DATABASE_URL")
if migration_url:
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
