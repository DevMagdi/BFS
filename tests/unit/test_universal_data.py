from __future__ import annotations

import pytest
from pydantic import ValidationError

from businessflow.config import Settings
from businessflow.universal_data.connectors import (
    M19_DUPLICATE_FIXTURE_KEY,
    M19_FIXTURE_KEY,
    M19_UNMAPPED_FIXTURE_KEY,
    ConnectorError,
    UniversalFixtureConnector,
)
from businessflow.universal_data.service import _batches


def test_universal_query_batches_bound_large_imports_and_preserve_order():
    values = list(range(100_001))
    batches = list(_batches(values))

    assert [len(batch) for batch in batches] == [5_000] * 20 + [1]
    assert [value for batch in batches for value in batch] == values


def test_m19_fixture_connector_serves_only_the_versioned_synthetic_csv():
    connector = UniversalFixtureConnector()

    fixture = connector.read_file(M19_FIXTURE_KEY)

    assert fixture.file_name == "synthetic-customer-export-v1.csv"
    assert fixture.content_type == "text/csv"
    assert fixture.content == (
        b"customer_code,name,email\n"
        b"CUS-001,Fixture Customer One,one@example.com\n"
        b"CUS-002,Fixture Customer Two,two@example.com\n"
    )

    duplicate_fixture = connector.read_file(M19_DUPLICATE_FIXTURE_KEY)
    unmapped_fixture = connector.read_file(M19_UNMAPPED_FIXTURE_KEY)
    assert b"CUS-101" in duplicate_fixture.content
    assert b"external_note" in unmapped_fixture.content

    with pytest.raises(ConnectorError) as unknown_fixture:
        connector.read_file("../../outside.csv")
    assert unknown_fixture.value.status_code == 404
    assert unknown_fixture.value.code == "connector_fixture_not_found"


def test_m19_connector_fixtures_cannot_be_enabled_in_production():
    with pytest.raises(ValidationError, match="not allowed in production"):
        Settings(
            _env_file=None,
            app_secret_key="prod-only-test-secret-" + "x" * 40,
            app_env="production",
            database_url="postgresql+psycopg://runtime:secret@127.0.0.1/bfs_test",
            universal_connector_fixtures_enabled=True,
        )
