from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_data.models import CompanyDataSource
from businessflow.errors import ApiError
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.operations_intelligence.engine import (
    CALCULATION_VERSION,
    MAX_ANALYSIS_EVENTS,
    SNAPSHOT_SCHEMA_VERSION,
    SnapshotValidationError,
    calculate_operations_observations,
)
from businessflow.operations_intelligence.models import (
    OperationsAnalysisRun,
    OperationsEventCurrent,
    OperationsEventStream,
    OperationsEventVersion,
)
from businessflow.operations_intelligence.schemas import (
    OperationsAnalysisCreateRequest,
    OperationsAnalysisEvidenceItem,
    OperationsAnalysisEvidencePage,
    OperationsAnalysisResponse,
    OperationsAnalysisResult,
    OperationsEventCorrectionRequest,
    OperationsEventCreateRequest,
    OperationsEventFields,
    OperationsEventHistoryPage,
    OperationsEventPage,
    OperationsEventSummary,
    OperationsEventVersionSummary,
)
from businessflow.services.company_context_access import (
    CompanyContextAccess,
    require_company_context_access,
)
from businessflow.universal_data.models import (
    CanonicalBusinessRecord,
    CanonicalBusinessRecordRevision,
    CompanyDataSourceProfileVersion,
    UniversalSourceLifecycleEvent,
)

_ALLOWED_OPERATIONAL_RECORD_TYPES = ("business_activity", "operational_record")


def _error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _require_company_access(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    permission: str,
    request_id: UUID | None = None,
) -> CompanyContextAccess:
    return require_company_context_access(
        session, user, tenant_id, permission, request_id=request_id
    )


def _source_advisory_lock(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> None:
    """Share Universal Data's source lock so profile changes/revocation cannot race writes."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"universal-source:{tenant_id}:{company_id}:{source_id}"},
    )


def _scoped_source(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> CompanyDataSource:
    source = session.scalar(
        select(CompanyDataSource).where(
            CompanyDataSource.tenant_id == tenant_id,
            CompanyDataSource.company_id == company_id,
            CompanyDataSource.id == source_id,
            CompanyDataSource.dataset_type == "universal_records",
        )
    )
    if source is None:
        raise _error(404, "operations_source_not_found", "The Universal Data source was not found.")
    return source


def _latest_source_profile(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> CompanyDataSourceProfileVersion:
    profile = session.scalar(
        select(CompanyDataSourceProfileVersion)
        .where(
            CompanyDataSourceProfileVersion.tenant_id == tenant_id,
            CompanyDataSourceProfileVersion.company_id == company_id,
            CompanyDataSourceProfileVersion.source_id == source_id,
        )
        .order_by(CompanyDataSourceProfileVersion.version.desc())
        .limit(1)
    )
    if profile is None:
        raise _error(
            409,
            "operations_source_profile_missing",
            "The registered source has no usable profile version.",
        )
    return profile


def _source_is_revoked(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> bool:
    revoked = session.scalar(
        select(UniversalSourceLifecycleEvent.id).where(
            UniversalSourceLifecycleEvent.tenant_id == tenant_id,
            UniversalSourceLifecycleEvent.company_id == company_id,
            UniversalSourceLifecycleEvent.source_id == source_id,
            UniversalSourceLifecycleEvent.event_type == "revoked",
        )
    )
    return revoked is not None


def _require_source_for_new_event(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> tuple[CompanyDataSource, CompanyDataSourceProfileVersion]:
    source = _scoped_source(session, tenant_id, company_id, source_id)
    profile = _latest_source_profile(session, tenant_id, company_id, source_id)
    if profile.source_status != "enabled":
        raise _error(
            409,
            "operations_source_disabled",
            "A disabled source cannot receive new operational observations.",
        )
    if _source_is_revoked(session, tenant_id, company_id, source_id):
        raise _error(
            409,
            "operations_source_revoked",
            "A revoked source cannot receive new operational observations.",
        )
    return source, profile


def _require_source_for_analysis(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> tuple[CompanyDataSource, CompanyDataSourceProfileVersion]:
    source = _scoped_source(session, tenant_id, company_id, source_id)
    profile = _latest_source_profile(session, tenant_id, company_id, source_id)
    if _source_is_revoked(session, tenant_id, company_id, source_id):
        raise _error(
            409,
            "operations_source_revoked",
            "New analyses cannot be created from a revoked source.",
        )
    # A disabled profile blocks new intake, not inspection of already retained evidence.
    return source, profile


def _validate_idempotency_key(value: str) -> str:
    if not 16 <= len(value) <= 128 or any(ord(character) < 32 for character in value):
        raise _error(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must contain 16 to 128 printable characters.",
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _error(422, "timezone_required", "Timestamps must include a timezone.")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _normalized_event_content(body: OperationsEventFields) -> dict[str, object]:
    return {
        "occurred_at": _utc(body.occurred_at),
        "event_type": body.event_type,
        "source_subject_reference": body.source_subject_reference,
        "process_reference": body.process_reference,
        "state_before": body.state_before,
        "state_after": body.state_after,
        "description": body.description,
        "evidence_kind": body.evidence_kind,
        "canonical_record_id": body.canonical_record_id,
        "canonical_record_revision": body.canonical_record_revision,
    }


def _event_request_content_json(content: dict[str, object]) -> dict[str, object]:
    return {
        key: (
            str(value)
            if isinstance(value, UUID)
            else _iso(value)
            if isinstance(value, datetime)
            else value
        )
        for key, value in content.items()
    }


def _request_hash(payload: dict[str, object]) -> str:
    return content_hash(payload)


def _resolve_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    profile: CompanyDataSourceProfileVersion,
    body: OperationsEventFields,
) -> tuple[UUID | None, int | None, str | None]:
    if body.evidence_kind == "manual_assertion":
        if (
            profile.source_type != "manual_entry"
            or profile.authority_classification != "manual_assertion"
        ):
            raise _error(
                422,
                "manual_assertion_source_required",
                "Manual observations require an enabled manual-entry source explicitly classified "
                "as a manual assertion.",
            )
        return None, None, None

    if body.canonical_record_id is None or body.canonical_record_revision is None:
        raise _error(
            422,
            "canonical_revision_required",
            "A linked observation requires an exact Universal Data record revision.",
        )
    row = session.execute(
        select(CanonicalBusinessRecord, CanonicalBusinessRecordRevision)
        .join(
            CanonicalBusinessRecordRevision,
            and_(
                CanonicalBusinessRecordRevision.tenant_id == CanonicalBusinessRecord.tenant_id,
                CanonicalBusinessRecordRevision.company_id == CanonicalBusinessRecord.company_id,
                CanonicalBusinessRecordRevision.source_id == CanonicalBusinessRecord.source_id,
                CanonicalBusinessRecordRevision.record_id == CanonicalBusinessRecord.id,
                CanonicalBusinessRecordRevision.revision == body.canonical_record_revision,
            ),
        )
        .where(
            CanonicalBusinessRecord.tenant_id == tenant_id,
            CanonicalBusinessRecord.company_id == company_id,
            CanonicalBusinessRecord.source_id == source_id,
            CanonicalBusinessRecord.id == body.canonical_record_id,
            CanonicalBusinessRecord.entity_type.in_(_ALLOWED_OPERATIONAL_RECORD_TYPES),
        )
    ).one_or_none()
    if row is None:
        raise _error(
            422,
            "canonical_revision_not_eligible",
            "The linked record revision must be an exact same-source business activity or "
            "operational record revision.",
        )
    record, revision = row
    # The exact record revision remains evidence, not a verified interpretation of its fields.
    if record.source_id != source_id or revision.created_by_user_id is None:
        raise _error(
            409,
            "canonical_revision_unverifiable",
            "The linked Universal Data revision could not be verified in this source scope.",
        )
    return record.id, revision.revision, revision.attributes_hash


def _version_hash_payload(
    stream: OperationsEventStream, event: OperationsEventVersion
) -> dict[str, object]:
    return {
        "schema": "operations-event-version-v1",
        "tenant_id": str(event.tenant_id),
        "company_id": str(event.company_id),
        "source_id": str(event.source_id),
        "event_id": str(event.event_id),
        "source_event_key": stream.source_event_key,
        "version_id": str(event.id),
        "version": event.version,
        "previous_version_id": str(event.previous_version_id)
        if event.previous_version_id is not None
        else None,
        "previous_event_hash": event.previous_event_hash,
        "idempotency_key_hash": event.idempotency_key_hash,
        "request_hash": event.request_hash,
        "source_profile_version_id": str(event.source_profile_version_id),
        "occurred_at": _iso(event.occurred_at),
        "event_type": event.event_type,
        "source_subject_reference": event.source_subject_reference,
        "process_reference": event.process_reference,
        "state_before": event.state_before,
        "state_after": event.state_after,
        "description": event.description,
        "evidence_kind": event.evidence_kind,
        "canonical_record_id": str(event.canonical_record_id)
        if event.canonical_record_id is not None
        else None,
        "canonical_record_revision": event.canonical_record_revision,
        "canonical_attributes_hash": event.canonical_attributes_hash,
        "correction_reason": event.correction_reason,
        "created_by_user_id": str(event.created_by_user_id),
        "created_at": _iso(event.created_at),
    }


def _verify_event_versions(
    session: Session,
    event_rows: Sequence[tuple[OperationsEventStream, OperationsEventVersion]],
) -> None:
    """Verify complete correction chains for selected events in one bounded query.

    Loading all selected event histories together avoids a predecessor lookup per event while
    still recomputing each version hash and checking every link back to version one.
    """
    if not event_rows:
        return

    first_stream = event_rows[0][0]
    scope = (first_stream.tenant_id, first_stream.company_id)
    streams_by_id: dict[UUID, OperationsEventStream] = {}
    for stream, event in event_rows:
        if (
            (stream.tenant_id, stream.company_id) != scope
            or event.tenant_id != stream.tenant_id
            or event.company_id != stream.company_id
            or event.source_id != stream.source_id
            or event.event_id != stream.id
        ):
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "An operational event version is outside its immutable stream scope.",
            )
        streams_by_id[stream.id] = stream

    histories: dict[UUID, list[OperationsEventVersion]] = {
        event_id: [] for event_id in streams_by_id
    }
    stream_keys = [(stream.source_id, stream.id) for stream in streams_by_id.values()]
    verified_ids: set[tuple[UUID, UUID]] = set()
    versions = session.scalars(
        select(OperationsEventVersion)
        .where(
            OperationsEventVersion.tenant_id == scope[0],
            OperationsEventVersion.company_id == scope[1],
            tuple_(OperationsEventVersion.source_id, OperationsEventVersion.event_id).in_(
                stream_keys
            ),
        )
        .order_by(OperationsEventVersion.event_id, OperationsEventVersion.version)
        .execution_options(populate_existing=True)
    )
    for version in versions:
        current_stream = streams_by_id.get(version.event_id)
        if (
            current_stream is None
            or version.tenant_id != current_stream.tenant_id
            or version.company_id != current_stream.company_id
            or version.source_id != current_stream.source_id
        ):
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "An operational event version is outside its immutable stream scope.",
            )

        history = histories[current_stream.id]
        expected_version = len(history) + 1
        if version.version != expected_version:
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "The operational event correction chain is incomplete or out of order.",
            )
        if content_hash(_version_hash_payload(current_stream, version)) != version.event_hash:
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "An operational event version failed its integrity check.",
            )

        previous = history[-1] if history else None
        if version.version == 1:
            if version.previous_version_id is not None or version.previous_event_hash is not None:
                raise _error(
                    409,
                    "operations_event_integrity_mismatch",
                    "The initial operational event version has an invalid predecessor.",
                )
        elif (
            previous is None
            or version.previous_version_id != previous.id
            or version.previous_event_hash != previous.event_hash
        ):
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "The operational event correction chain is incomplete or out of order.",
            )

        history.append(version)
        verified_ids.add((current_stream.id, version.id))

    current_versions = {
        event_id: (version_id, occurred_at)
        for event_id, version_id, occurred_at in session.execute(
            select(
                OperationsEventCurrent.event_id,
                OperationsEventCurrent.event_version_id,
                OperationsEventCurrent.occurred_at,
            ).where(
                OperationsEventCurrent.tenant_id == scope[0],
                OperationsEventCurrent.company_id == scope[1],
                tuple_(OperationsEventCurrent.source_id, OperationsEventCurrent.event_id).in_(
                    stream_keys
                ),
            )
        ).all()
    }
    for event_id, history in histories.items():
        if not history or current_versions.get(event_id) != (
            history[-1].id,
            history[-1].occurred_at,
        ):
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "The operational current pointer does not reference the latest immutable version.",
            )

    for stream, event in event_rows:
        if (stream.id, event.id) not in verified_ids:
            raise _error(
                409,
                "operations_event_integrity_mismatch",
                "The operational event version is missing from its immutable correction chain.",
            )


def _verify_event_version(
    session: Session,
    stream: OperationsEventStream,
    event: OperationsEventVersion,
) -> None:
    _verify_event_versions(session, [(stream, event)])


def _event_version_summary(
    session: Session,
    stream: OperationsEventStream,
    event: OperationsEventVersion,
    *,
    integrity_verified: bool = False,
) -> OperationsEventVersionSummary:
    if not integrity_verified:
        _verify_event_version(session, stream, event)
    return OperationsEventVersionSummary(
        id=event.id,
        event_id=event.event_id,
        source_id=event.source_id,
        source_event_key=stream.source_event_key,
        version=event.version,
        previous_version_id=event.previous_version_id,
        previous_event_hash=event.previous_event_hash,
        occurred_at=event.occurred_at,
        event_type=event.event_type,
        source_subject_reference=event.source_subject_reference,
        process_reference=event.process_reference,
        state_before=event.state_before,
        state_after=event.state_after,
        description=event.description,
        evidence_kind=cast(Any, event.evidence_kind),
        source_profile_version_id=event.source_profile_version_id,
        canonical_record_id=event.canonical_record_id,
        canonical_record_revision=event.canonical_record_revision,
        canonical_attributes_hash=event.canonical_attributes_hash,
        correction_reason=event.correction_reason,
        created_by_user_id=event.created_by_user_id,
        created_at=event.created_at,
        event_hash=event.event_hash,
    )


def _event_summary(
    session: Session,
    stream: OperationsEventStream,
    event: OperationsEventVersion,
    *,
    replayed: bool = False,
    is_current: bool = True,
    integrity_verified: bool = False,
) -> OperationsEventSummary:
    return OperationsEventSummary(
        id=stream.id,
        source_id=stream.source_id,
        source_event_key=stream.source_event_key,
        created_by_user_id=stream.created_by_user_id,
        created_at=stream.created_at,
        version=_event_version_summary(
            session, stream, event, integrity_verified=integrity_verified
        ),
        is_current=is_current,
        replayed=replayed,
    )


def _stream_for_event(
    session: Session, tenant_id: UUID, company_id: UUID, event_id: UUID
) -> OperationsEventStream:
    stream = session.scalar(
        select(OperationsEventStream).where(
            OperationsEventStream.tenant_id == tenant_id,
            OperationsEventStream.company_id == company_id,
            OperationsEventStream.id == event_id,
        )
    )
    if stream is None:
        raise _error(404, "operations_event_not_found", "The operational event was not found.")
    return stream


def _latest_event_version(
    session: Session, tenant_id: UUID, company_id: UUID, stream: OperationsEventStream
) -> OperationsEventVersion:
    event = session.scalar(
        select(OperationsEventVersion)
        .join(
            OperationsEventCurrent,
            and_(
                OperationsEventCurrent.tenant_id == OperationsEventVersion.tenant_id,
                OperationsEventCurrent.company_id == OperationsEventVersion.company_id,
                OperationsEventCurrent.source_id == OperationsEventVersion.source_id,
                OperationsEventCurrent.event_id == OperationsEventVersion.event_id,
                OperationsEventCurrent.event_version_id == OperationsEventVersion.id,
            ),
        )
        .where(
            OperationsEventCurrent.tenant_id == tenant_id,
            OperationsEventCurrent.company_id == company_id,
            OperationsEventCurrent.source_id == stream.source_id,
            OperationsEventCurrent.event_id == stream.id,
        )
        # Keep the current pointer stable while the full correction chain is verified.
        .with_for_update(of=OperationsEventCurrent)
        .limit(1)
    )
    if event is None:
        raise _error(
            409,
            "operations_event_integrity_mismatch",
            "The operational event has no immutable version.",
        )
    return event


def _create_version(
    session: Session,
    user: UserAccount,
    stream: OperationsEventStream,
    profile: CompanyDataSourceProfileVersion,
    content: dict[str, object],
    *,
    version_number: int,
    idempotency_hash: str,
    request_hash: str,
    created_at: datetime,
    previous: OperationsEventVersion | None = None,
    correction_reason: str | None = None,
    canonical_record_id: UUID | None,
    canonical_record_revision: int | None,
    canonical_attributes_hash: str | None,
) -> OperationsEventVersion:
    event = OperationsEventVersion(
        id=uuid4(),
        tenant_id=stream.tenant_id,
        company_id=stream.company_id,
        source_id=stream.source_id,
        event_id=stream.id,
        version=version_number,
        previous_version_id=previous.id if previous is not None else None,
        previous_event_hash=previous.event_hash if previous is not None else None,
        idempotency_key_hash=idempotency_hash,
        request_hash=request_hash,
        event_hash="0" * 64,
        source_profile_version_id=profile.id,
        occurred_at=cast(datetime, content["occurred_at"]),
        event_type=cast(str, content["event_type"]),
        source_subject_reference=cast(str, content["source_subject_reference"]),
        process_reference=cast(str | None, content["process_reference"]),
        state_before=cast(str | None, content["state_before"]),
        state_after=cast(str | None, content["state_after"]),
        description=cast(str | None, content["description"]),
        evidence_kind=cast(str, content["evidence_kind"]),
        canonical_record_id=canonical_record_id,
        canonical_record_revision=canonical_record_revision,
        canonical_attributes_hash=canonical_attributes_hash,
        correction_reason=correction_reason,
        created_by_user_id=user.id,
        created_at=_utc(created_at),
    )
    event.event_hash = content_hash(_version_hash_payload(stream, event))
    session.add(event)
    session.flush()
    return event


def _idempotent_event(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    idempotency_hash: str,
    request_hash: str,
) -> tuple[OperationsEventStream, OperationsEventVersion] | None:
    existing = session.scalar(
        select(OperationsEventVersion).where(
            OperationsEventVersion.tenant_id == tenant_id,
            OperationsEventVersion.company_id == company_id,
            OperationsEventVersion.source_id == source_id,
            OperationsEventVersion.idempotency_key_hash == idempotency_hash,
        )
    )
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise _error(
            409,
            "idempotency_key_reused",
            "This Idempotency-Key was already used for different operational event content.",
        )
    stream = _stream_for_event(session, tenant_id, company_id, existing.event_id)
    return stream, existing


def create_operations_event(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: OperationsEventCreateRequest,
    idempotency_key: str,
    request_id: UUID,
    now: datetime,
) -> OperationsEventSummary:
    access = _require_company_access(
        session, user, tenant_id, "operations:write", request_id=request_id
    )
    _source_advisory_lock(session, tenant_id, access.company.id, body.source_id)
    _scoped_source(session, tenant_id, access.company.id, body.source_id)
    idempotency_hash = _validate_idempotency_key(idempotency_key)
    content = _normalized_event_content(body)
    request_hash = _request_hash(
        {
            "operation": "create",
            "tenant_id": str(tenant_id),
            "company_id": str(access.company.id),
            "source_id": str(body.source_id),
            "source_event_key": body.source_event_key,
            "actor_user_id": str(user.id),
            "event": _event_request_content_json(content),
        }
    )
    replay = _idempotent_event(
        session,
        tenant_id,
        access.company.id,
        body.source_id,
        idempotency_hash,
        request_hash,
    )
    if replay is not None:
        stream, event = replay
        record_event(
            session,
            "operations.event_create_replayed",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="operations_event",
            target_id=stream.id,
            request_id=request_id,
            details={"source_id": stream.source_id, "version": event.version},
        )
        current = _latest_event_version(session, tenant_id, access.company.id, stream)
        _verify_event_versions(session, [(stream, event), (stream, current)])
        return _event_summary(
            session,
            stream,
            event,
            replayed=True,
            is_current=event.version == current.version,
            integrity_verified=True,
        )

    source, profile = _require_source_for_new_event(
        session, tenant_id, access.company.id, body.source_id
    )
    existing_stream = session.scalar(
        select(OperationsEventStream.id).where(
            OperationsEventStream.tenant_id == tenant_id,
            OperationsEventStream.company_id == access.company.id,
            OperationsEventStream.source_id == source.id,
            OperationsEventStream.source_event_key == body.source_event_key,
        )
    )
    if existing_stream is not None:
        raise _error(
            409,
            "operations_source_event_key_exists",
            "This source event key already has an event stream; corrections must append a version "
            "to that event.",
        )

    canonical_record_id, canonical_revision, canonical_attributes_hash = _resolve_evidence(
        session,
        user,
        tenant_id,
        access.company.id,
        source.id,
        profile,
        body,
    )
    created_at = _utc(now)
    stream = OperationsEventStream(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source.id,
        source_event_key=body.source_event_key,
        created_by_user_id=user.id,
        created_at=created_at,
    )
    session.add(stream)
    session.flush()
    event = _create_version(
        session,
        user,
        stream,
        profile,
        content,
        version_number=1,
        idempotency_hash=idempotency_hash,
        request_hash=request_hash,
        created_at=created_at,
        canonical_record_id=canonical_record_id,
        canonical_record_revision=canonical_revision,
        canonical_attributes_hash=canonical_attributes_hash,
    )
    record_event(
        session,
        "operations.event_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="operations_event",
        target_id=stream.id,
        request_id=request_id,
        details={
            "source_id": source.id,
            "event_type": event.event_type,
            "evidence_kind": event.evidence_kind,
            "version": event.version,
        },
    )
    return _event_summary(session, stream, event)


def correct_operations_event(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    event_id: UUID,
    body: OperationsEventCorrectionRequest,
    idempotency_key: str,
    request_id: UUID,
    now: datetime,
) -> OperationsEventSummary:
    access = _require_company_access(
        session, user, tenant_id, "operations:write", request_id=request_id
    )
    stream = _stream_for_event(session, tenant_id, access.company.id, event_id)
    _source_advisory_lock(session, tenant_id, access.company.id, stream.source_id)
    idempotency_hash = _validate_idempotency_key(idempotency_key)
    content = _normalized_event_content(body)
    request_hash = _request_hash(
        {
            "operation": "correct",
            "tenant_id": str(tenant_id),
            "company_id": str(access.company.id),
            "source_id": str(stream.source_id),
            "event_id": str(stream.id),
            "expected_version": body.expected_version,
            "correction_reason": body.correction_reason,
            "actor_user_id": str(user.id),
            "event": _event_request_content_json(content),
        }
    )
    replay = _idempotent_event(
        session,
        tenant_id,
        access.company.id,
        stream.source_id,
        idempotency_hash,
        request_hash,
    )
    if replay is not None:
        replay_stream, event = replay
        if replay_stream.id != stream.id:
            raise _error(
                409,
                "idempotency_key_reused",
                "This Idempotency-Key belongs to a different operational event.",
            )
        record_event(
            session,
            "operations.event_correction_replayed",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="operations_event",
            target_id=stream.id,
            request_id=request_id,
            details={"source_id": stream.source_id, "version": event.version},
        )
        current = _latest_event_version(session, tenant_id, access.company.id, stream)
        _verify_event_versions(session, [(stream, event), (stream, current)])
        return _event_summary(
            session,
            stream,
            event,
            replayed=True,
            is_current=event.version == current.version,
            integrity_verified=True,
        )

    _source, profile = _require_source_for_new_event(
        session, tenant_id, access.company.id, stream.source_id
    )
    previous = _latest_event_version(session, tenant_id, access.company.id, stream)
    _verify_event_version(session, stream, previous)
    if previous.version != body.expected_version:
        raise _error(
            409,
            "operations_event_version_conflict",
            "The operational event changed; reload its latest version before correcting it.",
        )
    canonical_record_id, canonical_revision, canonical_attributes_hash = _resolve_evidence(
        session,
        user,
        tenant_id,
        access.company.id,
        stream.source_id,
        profile,
        body,
    )
    event = _create_version(
        session,
        user,
        stream,
        profile,
        content,
        version_number=previous.version + 1,
        idempotency_hash=idempotency_hash,
        request_hash=request_hash,
        created_at=_utc(now),
        previous=previous,
        correction_reason=body.correction_reason,
        canonical_record_id=canonical_record_id,
        canonical_record_revision=canonical_revision,
        canonical_attributes_hash=canonical_attributes_hash,
    )
    record_event(
        session,
        "operations.event_corrected",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="operations_event",
        target_id=stream.id,
        request_id=request_id,
        details={
            "source_id": stream.source_id,
            "event_type": event.event_type,
            "version": event.version,
            "previous_version": previous.version,
        },
    )
    return _event_summary(session, stream, event)


def _event_stream_and_version(
    session: Session, version: OperationsEventVersion
) -> OperationsEventStream:
    return _stream_for_event(session, version.tenant_id, version.company_id, version.event_id)


def list_operations_events(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    source_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> OperationsEventPage:
    access = _require_company_access(session, user, tenant_id, "operations:read")
    statement = (
        select(OperationsEventStream, OperationsEventVersion)
        .select_from(OperationsEventStream)
        .join(
            OperationsEventCurrent,
            and_(
                OperationsEventCurrent.tenant_id == OperationsEventStream.tenant_id,
                OperationsEventCurrent.company_id == OperationsEventStream.company_id,
                OperationsEventCurrent.source_id == OperationsEventStream.source_id,
                OperationsEventCurrent.event_id == OperationsEventStream.id,
            ),
        )
        .join(
            OperationsEventVersion,
            and_(
                OperationsEventVersion.tenant_id == OperationsEventCurrent.tenant_id,
                OperationsEventVersion.company_id == OperationsEventCurrent.company_id,
                OperationsEventVersion.source_id == OperationsEventCurrent.source_id,
                OperationsEventVersion.event_id == OperationsEventCurrent.event_id,
                OperationsEventVersion.id == OperationsEventCurrent.event_version_id,
            ),
        )
        .where(
            OperationsEventStream.tenant_id == tenant_id,
            OperationsEventStream.company_id == access.company.id,
        )
    )
    if source_id is not None:
        statement = statement.where(OperationsEventStream.source_id == source_id)
    total = (
        session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    )
    # Lock selected pointers so correction commits cannot race the integrity check and the
    # is_current response flag for those rows.
    rows = session.execute(
        statement.order_by(OperationsEventStream.created_at.desc(), OperationsEventStream.id)
        .limit(limit)
        .offset(offset)
        .with_for_update(of=OperationsEventCurrent)
    ).all()
    event_rows = [(stream, event) for stream, event in rows]
    _verify_event_versions(session, event_rows)
    items = [
        _event_summary(session, stream, event, integrity_verified=True)
        for stream, event in event_rows
    ]
    return OperationsEventPage(items=items, total=total, limit=limit, offset=offset)


def get_operations_event(
    session: Session, user: UserAccount, tenant_id: UUID, event_id: UUID
) -> OperationsEventSummary:
    access = _require_company_access(session, user, tenant_id, "operations:read")
    stream = _stream_for_event(session, tenant_id, access.company.id, event_id)
    latest = _latest_event_version(session, tenant_id, access.company.id, stream)
    return _event_summary(session, stream, latest)


def list_operations_event_versions(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    event_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> OperationsEventHistoryPage:
    access = _require_company_access(session, user, tenant_id, "operations:read")
    stream = _stream_for_event(session, tenant_id, access.company.id, event_id)
    # Lock the current pointer before reading paginated history. Under READ COMMITTED, a
    # correction could otherwise commit between the chain and pointer-verification queries.
    _latest_event_version(session, tenant_id, access.company.id, stream)
    base = select(OperationsEventVersion).where(
        OperationsEventVersion.tenant_id == tenant_id,
        OperationsEventVersion.company_id == access.company.id,
        OperationsEventVersion.source_id == stream.source_id,
        OperationsEventVersion.event_id == stream.id,
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    versions = list(
        session.scalars(
            base.order_by(OperationsEventVersion.version.desc()).limit(limit).offset(offset)
        )
    )
    event_rows = [(stream, version) for version in versions]
    _verify_event_versions(session, event_rows)
    items = [
        _event_version_summary(session, stream, version, integrity_verified=True)
        for version in versions
    ]
    return OperationsEventHistoryPage(
        event_id=stream.id,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def _analysis_event_snapshot(
    stream: OperationsEventStream, event: OperationsEventVersion
) -> dict[str, object]:
    return {
        "event_id": str(stream.id),
        "event_version_id": str(event.id),
        "source_id": str(stream.source_id),
        "source_event_key": stream.source_event_key,
        "version": event.version,
        "source_profile_version_id": str(event.source_profile_version_id),
        "occurred_at": _iso(event.occurred_at),
        "event_type": event.event_type,
        "source_subject_reference": event.source_subject_reference,
        "process_reference": event.process_reference,
        "state_before": event.state_before,
        "state_after": event.state_after,
        "evidence_kind": event.evidence_kind,
        "canonical_record_id": str(event.canonical_record_id)
        if event.canonical_record_id is not None
        else None,
        "canonical_record_revision": event.canonical_record_revision,
        "canonical_attributes_hash": event.canonical_attributes_hash,
        "event_hash": event.event_hash,
        "created_by_user_id": str(event.created_by_user_id),
        "created_at": _iso(event.created_at),
    }


def _analysis_request_hash(
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    user_id: UUID,
    start_at: datetime,
    end_at: datetime,
) -> str:
    return _request_hash(
        {
            "operation": "operations_analysis",
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "source_id": str(source_id),
            "actor_user_id": str(user_id),
            "start_at": _iso(start_at),
            "end_at": _iso(end_at),
            "interval": "[start_at,end_at)",
            "calculation_version": CALCULATION_VERSION,
        }
    )


def _verify_analysis_run(run: OperationsAnalysisRun) -> dict[str, object]:
    if content_hash(run.input_snapshot) != run.input_hash:
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "The saved operational analysis input failed its integrity check.",
        )
    if content_hash(run.result_snapshot) != run.result_hash:
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "The saved operational analysis result failed its integrity check.",
        )
    try:
        replayed = calculate_operations_observations(run.input_snapshot)
    except SnapshotValidationError:
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "The saved operational analysis input is not valid for its calculation version.",
        ) from None
    if replayed != run.result_snapshot or content_hash(replayed) != run.result_hash:
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "Replaying the saved operational analysis did not reproduce its stored result.",
        )
    snapshot = run.input_snapshot
    events = snapshot.get("events") if isinstance(snapshot, dict) else None
    if not isinstance(events, list) or run.input_count != len(events):
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "The saved operational analysis count does not match its input snapshot.",
        )
    return replayed


def _analysis_response(
    run: OperationsAnalysisRun, *, replayed: bool = False
) -> OperationsAnalysisResponse:
    result = _verify_analysis_run(run)
    return OperationsAnalysisResponse(
        id=run.id,
        source_id=run.source_id,
        start_at=run.start_at,
        end_at=run.end_at,
        input_count=run.input_count,
        calculation_version=cast(Any, run.calculation_version),
        input_hash=run.input_hash,
        result_hash=run.result_hash,
        created_by_user_id=run.created_by_user_id,
        created_at=run.created_at,
        result=OperationsAnalysisResult.model_validate(result),
        integrity_verified=True,
        replayed=replayed,
    )


def create_operations_analysis(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: OperationsAnalysisCreateRequest,
    idempotency_key: str,
    request_id: UUID,
    now: datetime,
) -> OperationsAnalysisResponse:
    access = _require_company_access(
        session, user, tenant_id, "operations:write", request_id=request_id
    )
    start_at = _utc(body.start_at)
    end_at = _utc(body.end_at)
    if end_at <= start_at:
        raise _error(422, "invalid_operations_period", "end_at must be later than start_at.")
    _source_advisory_lock(session, tenant_id, access.company.id, body.source_id)
    _scoped_source(session, tenant_id, access.company.id, body.source_id)
    idempotency_hash = _validate_idempotency_key(idempotency_key)
    request_hash = _analysis_request_hash(
        tenant_id,
        access.company.id,
        body.source_id,
        user.id,
        start_at,
        end_at,
    )
    existing = session.scalar(
        select(OperationsAnalysisRun).where(
            OperationsAnalysisRun.tenant_id == tenant_id,
            OperationsAnalysisRun.company_id == access.company.id,
            OperationsAnalysisRun.source_id == body.source_id,
            OperationsAnalysisRun.idempotency_key_hash == idempotency_hash,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise _error(
                409,
                "idempotency_key_reused",
                "This Idempotency-Key was already used for a different operational analysis.",
            )
        response = _analysis_response(existing, replayed=True)
        record_event(
            session,
            "operations.analysis_replayed",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="operations_analysis",
            target_id=existing.id,
            request_id=request_id,
            details={"source_id": existing.source_id, "input_count": existing.input_count},
        )
        return response

    _require_source_for_analysis(session, tenant_id, access.company.id, body.source_id)
    rows_query = (
        select(OperationsEventStream, OperationsEventVersion)
        .select_from(OperationsEventCurrent)
        .join(
            OperationsEventStream,
            and_(
                OperationsEventStream.tenant_id == OperationsEventCurrent.tenant_id,
                OperationsEventStream.company_id == OperationsEventCurrent.company_id,
                OperationsEventStream.source_id == OperationsEventCurrent.source_id,
                OperationsEventStream.id == OperationsEventCurrent.event_id,
            ),
        )
        .join(
            OperationsEventVersion,
            and_(
                OperationsEventVersion.tenant_id == OperationsEventCurrent.tenant_id,
                OperationsEventVersion.company_id == OperationsEventCurrent.company_id,
                OperationsEventVersion.source_id == OperationsEventCurrent.source_id,
                OperationsEventVersion.event_id == OperationsEventCurrent.event_id,
                OperationsEventVersion.id == OperationsEventCurrent.event_version_id,
            ),
        )
        .where(
            OperationsEventCurrent.tenant_id == tenant_id,
            OperationsEventCurrent.company_id == access.company.id,
            OperationsEventCurrent.source_id == body.source_id,
            # Query either timestamp so a corrupt pointer timestamp cannot silently hide an
            # in-period immutable version; chain verification rejects the mismatch below.
            or_(
                and_(
                    OperationsEventCurrent.occurred_at >= start_at,
                    OperationsEventCurrent.occurred_at < end_at,
                ),
                and_(
                    OperationsEventVersion.occurred_at >= start_at,
                    OperationsEventVersion.occurred_at < end_at,
                ),
            ),
        )
        .order_by(
            OperationsEventCurrent.occurred_at,
            OperationsEventStream.source_event_key,
            OperationsEventStream.id,
        )
        .limit(MAX_ANALYSIS_EVENTS + 1)
    )
    rows = session.execute(rows_query).all()
    if len(rows) > MAX_ANALYSIS_EVENTS:
        raise _error(
            422,
            "operations_analysis_input_limit_exceeded",
            "The selected source and time interval exceed the supported evidence bound; "
            "narrow the interval. No partial result was saved.",
        )
    event_rows = [(stream, event) for stream, event in rows]
    _verify_event_versions(session, event_rows)

    events = [_analysis_event_snapshot(stream, event) for stream, event in event_rows]
    input_snapshot: dict[str, object] = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "scope": {
            "tenant_id": str(tenant_id),
            "company_id": str(access.company.id),
            "source_id": str(body.source_id),
        },
        "period": {
            "start_at": _iso(start_at),
            "end_at": _iso(end_at),
            "interval": "[start_at,end_at)",
        },
        "events": events,
    }
    result = calculate_operations_observations(input_snapshot)
    input_hash = content_hash(input_snapshot)
    result_hash = content_hash(result)
    run = OperationsAnalysisRun(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=body.source_id,
        created_by_user_id=user.id,
        idempotency_key_hash=idempotency_hash,
        request_hash=request_hash,
        start_at=start_at,
        end_at=end_at,
        input_count=len(events),
        calculation_version=CALCULATION_VERSION,
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result,
        created_at=_utc(now),
    )
    session.add(run)
    session.flush()
    record_event(
        session,
        "operations.analysis_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="operations_analysis",
        target_id=run.id,
        request_id=request_id,
        details={"source_id": body.source_id, "input_count": len(events)},
    )
    return _analysis_response(run)


def get_operations_analysis(
    session: Session, user: UserAccount, tenant_id: UUID, analysis_id: UUID
) -> OperationsAnalysisResponse:
    access = _require_company_access(session, user, tenant_id, "operations:read")
    run = session.scalar(
        select(OperationsAnalysisRun).where(
            OperationsAnalysisRun.tenant_id == tenant_id,
            OperationsAnalysisRun.company_id == access.company.id,
            OperationsAnalysisRun.id == analysis_id,
        )
    )
    if run is None:
        raise _error(
            404, "operations_analysis_not_found", "The operational analysis was not found."
        )
    return _analysis_response(run, replayed=True)


def list_operations_analysis_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    analysis_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> OperationsAnalysisEvidencePage:
    access = _require_company_access(session, user, tenant_id, "operations:read")
    run = session.scalar(
        select(OperationsAnalysisRun).where(
            OperationsAnalysisRun.tenant_id == tenant_id,
            OperationsAnalysisRun.company_id == access.company.id,
            OperationsAnalysisRun.id == analysis_id,
        )
    )
    if run is None:
        raise _error(
            404, "operations_analysis_not_found", "The operational analysis was not found."
        )
    _verify_analysis_run(run)
    events = run.input_snapshot.get("events")
    if not isinstance(events, list):
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "The saved operational analysis evidence list is invalid.",
        )
    page = events[offset : offset + limit]
    try:
        items = [OperationsAnalysisEvidenceItem.model_validate(item) for item in page]
    except (TypeError, ValueError):
        raise _error(
            409,
            "operations_analysis_integrity_mismatch",
            "The saved operational analysis evidence is invalid.",
        ) from None
    return OperationsAnalysisEvidencePage(
        items=items,
        total=len(events),
        limit=limit,
        offset=offset,
    )
