from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]


def invoke_downgrade_function(engine: Engine, revision_file: str) -> None:
    """Run one revision's downgrade body directly, bypassing later revision guards."""
    migration_path = ROOT / "migrations" / "versions" / revision_file
    spec = importlib.util.spec_from_file_location(
        f"migration_under_test_{migration_path.stem}", migration_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load migration revision {revision_file!r}")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    downgrade = getattr(migration, "downgrade", None)
    if not callable(downgrade):
        raise RuntimeError(f"Migration revision {revision_file!r} has no downgrade function")

    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        downgrade()
