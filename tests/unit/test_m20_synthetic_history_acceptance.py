from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict, dataclass
from typing import Literal

import pytest

RelationKind = Literal["conflicts_with", "supersedes"]


@dataclass(frozen=True)
class SyntheticScope:
    tenant_id: str
    company_id: str


@dataclass(frozen=True)
class SyntheticRelation:
    kind: RelationKind
    target_version_id: str


@dataclass(frozen=True)
class SyntheticVersion:
    history_id: str
    scope: SyntheticScope
    version_id: str
    sequence: int
    actor_id: str
    previous_version_id: str | None
    context_value: str
    original_source_fact: tuple[tuple[str, str], ...]
    evidence_references: tuple[tuple[str, str], ...]
    relations: tuple[SyntheticRelation, ...]


class SyntheticHistoryStore:
    """Tiny in-memory acceptance fixture; deliberately not application storage code."""

    def __init__(self) -> None:
        self._histories: dict[tuple[SyntheticScope, str], tuple[SyntheticVersion, ...]] = {}

    def append(
        self,
        *,
        scope: SyntheticScope,
        history_id: str,
        version_id: str,
        actor_id: str,
        context_value: str,
        original_source_fact: tuple[tuple[str, str], ...],
        evidence_references: tuple[tuple[str, str], ...],
        relations: tuple[SyntheticRelation, ...] = (),
    ) -> SyntheticVersion:
        key = (scope, history_id)
        previous_versions = self._histories.get(key, ())
        known_ids = {item.version_id for item in previous_versions}
        if version_id in known_ids:
            raise ValueError("synthetic version IDs must be unique within a history")
        if any(item.kind not in {"conflicts_with", "supersedes"} for item in relations):
            raise ValueError("unsupported synthetic history relation")
        if any(item.target_version_id not in known_ids for item in relations):
            raise ValueError("synthetic relations must reference a visible prior version")

        source_fact = tuple(original_source_fact)
        references = tuple(evidence_references)
        previous = previous_versions[-1] if previous_versions else None
        if previous is not None and (
            source_fact != previous.original_source_fact
            or references != previous.evidence_references
        ):
            raise ValueError(
                "corrections must preserve original source facts and evidence references"
            )

        version = SyntheticVersion(
            history_id=history_id,
            scope=scope,
            version_id=version_id,
            sequence=len(previous_versions) + 1,
            actor_id=actor_id,
            previous_version_id=previous.version_id if previous is not None else None,
            context_value=context_value,
            original_source_fact=source_fact,
            evidence_references=references,
            relations=tuple(relations),
        )
        self._histories[key] = (*previous_versions, version)
        return version

    def history(self, scope: SyntheticScope, history_id: str) -> tuple[SyntheticVersion, ...]:
        return self._histories.get((scope, history_id), ())


AccessCheck = Callable[[str, SyntheticScope], bool]


def read_authorized_history(
    store: SyntheticHistoryStore,
    *,
    actor_id: str,
    scope: SyntheticScope,
    history_id: str,
    authorize: AccessCheck,
) -> tuple[SyntheticVersion, ...]:
    """The fixture contract is authorize first, then retrieve from the scoped store."""
    if not authorize(actor_id, scope):
        raise PermissionError("synthetic history access denied")
    return store.history(scope, history_id)


def _seed_history(
    store: SyntheticHistoryStore, scope: SyntheticScope, namespace: str
) -> tuple[SyntheticVersion, SyntheticVersion, SyntheticVersion]:
    history_id = "synthetic-history"
    source_fact = (("fact", f"synthetic-source-fact:{namespace}"),)
    evidence_references = (("reference", f"synthetic-evidence-reference:{namespace}"),)
    original = store.append(
        scope=scope,
        history_id=history_id,
        version_id=f"{namespace}-version-1",
        actor_id=f"synthetic-actor-{namespace}-1",
        context_value=f"synthetic-context-{namespace}-original",
        original_source_fact=source_fact,
        evidence_references=evidence_references,
    )
    correction = store.append(
        scope=scope,
        history_id=history_id,
        version_id=f"{namespace}-version-2",
        actor_id=f"synthetic-actor-{namespace}-2",
        context_value=f"synthetic-context-{namespace}-corrected",
        original_source_fact=source_fact,
        evidence_references=evidence_references,
        relations=(SyntheticRelation("supersedes", original.version_id),),
    )
    conflicting = store.append(
        scope=scope,
        history_id=history_id,
        version_id=f"{namespace}-version-3",
        actor_id=f"synthetic-actor-{namespace}-3",
        context_value=f"synthetic-context-{namespace}-alternative",
        original_source_fact=source_fact,
        evidence_references=evidence_references,
        relations=(SyntheticRelation("conflicts_with", correction.version_id),),
    )
    return original, correction, conflicting


def _history_digest(versions: tuple[SyntheticVersion, ...]) -> str:
    canonical = json.dumps(
        [asdict(version) for version in versions], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_synthetic_corrections_are_actor_attributed_append_only_and_preserve_references() -> None:
    scope = SyntheticScope("synthetic-tenant-a", "synthetic-company-a")
    store = SyntheticHistoryStore()
    original = store.append(
        scope=scope,
        history_id="synthetic-history",
        version_id="fixture-version-1",
        actor_id="synthetic-actor-original",
        context_value="synthetic-context-original",
        original_source_fact=(("fact", "synthetic-source-fact-v1"),),
        evidence_references=(("reference", "synthetic-evidence-ref-v1"),),
    )
    earlier_view = store.history(scope, "synthetic-history")
    correction = store.append(
        scope=scope,
        history_id="synthetic-history",
        version_id="fixture-version-2",
        actor_id="synthetic-actor-corrector",
        context_value="synthetic-context-corrected",
        original_source_fact=original.original_source_fact,
        evidence_references=original.evidence_references,
    )

    assert correction.sequence == 2
    assert correction.previous_version_id == original.version_id
    assert correction.actor_id == "synthetic-actor-corrector"
    assert correction.context_value != original.context_value
    assert correction.original_source_fact == original.original_source_fact
    assert correction.evidence_references == original.evidence_references
    assert earlier_view == (original,)
    assert store.history(scope, "synthetic-history") == (original, correction)
    with pytest.raises(FrozenInstanceError):
        original.context_value = "mutated-source-fact"  # type: ignore[misc]

    with pytest.raises(ValueError, match="preserve original source facts"):
        store.append(
            scope=scope,
            history_id="synthetic-history",
            version_id="fixture-version-3-invalid-fact",
            actor_id="synthetic-actor-invalid-fact",
            context_value="synthetic-context-invalid-fact",
            original_source_fact=(("fact", "rewritten-source-fact"),),
            evidence_references=original.evidence_references,
        )
    with pytest.raises(ValueError, match="preserve original source facts"):
        store.append(
            scope=scope,
            history_id="synthetic-history",
            version_id="fixture-version-3-invalid-reference",
            actor_id="synthetic-actor-invalid-reference",
            context_value="synthetic-context-invalid-reference",
            original_source_fact=original.original_source_fact,
            evidence_references=(("reference", "rewritten-evidence-ref"),),
        )
    assert store.history(scope, "synthetic-history") == (original, correction)


def test_synthetic_conflicts_and_supersession_links_remain_visible_without_resolution() -> None:
    scope = SyntheticScope("synthetic-tenant-a", "synthetic-company-a")
    store = SyntheticHistoryStore()
    original, correction, conflicting = _seed_history(store, scope, "visible")
    visible_history = store.history(scope, "synthetic-history")

    assert visible_history == (original, correction, conflicting)
    assert [item.sequence for item in visible_history] == [1, 2, 3]
    assert all(item.actor_id for item in visible_history)
    assert all(
        item.original_source_fact == original.original_source_fact
        and item.evidence_references == original.evidence_references
        for item in visible_history
    )
    assert correction.relations == (SyntheticRelation("supersedes", original.version_id),)
    assert conflicting.relations == (SyntheticRelation("conflicts_with", correction.version_id),)
    assert visible_history[-1].version_id == conflicting.version_id


def test_synthetic_authorization_happens_before_any_history_lookup() -> None:
    scope = SyntheticScope("synthetic-tenant-a", "synthetic-company-a")
    store = _RecordingHistoryStore()
    _seed_history(store, scope, "auth")
    events: list[str] = []

    def deny_before_lookup(_actor_id: str, _scope: SyntheticScope) -> bool:
        events.append("authorize")
        return False

    with pytest.raises(PermissionError, match="access denied"):
        read_authorized_history(
            store,
            actor_id="synthetic-reader",
            scope=scope,
            history_id="synthetic-history",
            authorize=deny_before_lookup,
        )

    assert events == ["authorize"]
    assert store.lookup_count == 0


def test_synthetic_history_isolated_by_both_tenant_and_company_scope() -> None:
    tenant_a_company_a = SyntheticScope("synthetic-tenant-a", "synthetic-company-a")
    tenant_a_company_b = SyntheticScope("synthetic-tenant-a", "synthetic-company-b")
    tenant_b_company_a = SyntheticScope("synthetic-tenant-b", "synthetic-company-a")
    store = SyntheticHistoryStore()
    chain_a = _seed_history(store, tenant_a_company_a, "scope-a")
    chain_b = _seed_history(store, tenant_a_company_b, "scope-b")
    chain_c = _seed_history(store, tenant_b_company_a, "scope-c")

    def allow_synthetic_test_scope(_actor_id: str, _scope: SyntheticScope) -> bool:
        return True

    retrieved_a = read_authorized_history(
        store,
        actor_id="synthetic-reader",
        scope=tenant_a_company_a,
        history_id="synthetic-history",
        authorize=allow_synthetic_test_scope,
    )
    retrieved_b = read_authorized_history(
        store,
        actor_id="synthetic-reader",
        scope=tenant_a_company_b,
        history_id="synthetic-history",
        authorize=allow_synthetic_test_scope,
    )
    retrieved_c = read_authorized_history(
        store,
        actor_id="synthetic-reader",
        scope=tenant_b_company_a,
        history_id="synthetic-history",
        authorize=allow_synthetic_test_scope,
    )

    assert retrieved_a == chain_a
    assert retrieved_b == chain_b
    assert retrieved_c == chain_c
    assert {
        retrieved_a[0].context_value,
        retrieved_b[0].context_value,
        retrieved_c[0].context_value,
    } == {
        "synthetic-context-scope-a-original",
        "synthetic-context-scope-b-original",
        "synthetic-context-scope-c-original",
    }


def test_synthetic_history_digest_is_deterministic_for_identical_fixture_input() -> None:
    scope = SyntheticScope("synthetic-tenant-a", "synthetic-company-a")
    first_store = SyntheticHistoryStore()
    second_store = SyntheticHistoryStore()
    first = _seed_history(first_store, scope, "deterministic")
    second = _seed_history(second_store, scope, "deterministic")

    assert _history_digest(first) == _history_digest(second)
    assert len(_history_digest(first)) == 64


class _RecordingHistoryStore(SyntheticHistoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.lookup_count = 0

    def history(self, scope: SyntheticScope, history_id: str) -> tuple[SyntheticVersion, ...]:
        self.lookup_count += 1
        return super().history(scope, history_id)
