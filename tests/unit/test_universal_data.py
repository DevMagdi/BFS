from __future__ import annotations

from businessflow.universal_data.service import _batches


def test_universal_query_batches_bound_large_imports_and_preserve_order():
    values = list(range(100_001))
    batches = list(_batches(values))

    assert [len(batch) for batch in batches] == [5_000] * 20 + [1]
    assert [value for batch in batches for value in batch] == values
