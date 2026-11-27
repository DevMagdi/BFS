from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_data.commitment_schemas import (
    CommitmentStatus,
    CustomerCommitmentCreateRequest,
    CustomerCommitmentDetailResponse,
    CustomerCommitmentFinding,
    CustomerCommitmentHistoryPage,
    CustomerCommitmentPage,
    CustomerCommitmentReviewRequest,
    CustomerCommitmentSubmitRequest,
    CustomerCommitmentSummary,
    CustomerCommitmentUpdateRequest,
    CustomerCommitmentValidation,
    ManualCustomerCommitmentEventResponse,
    ManualCustomerCommitmentEvidenceResponse,
    ManualCustomerCommitmentEvidenceSummary,
)
from businessflow.company_data.contracts import (
    MANUAL_COMMITMENT_CONTRACT_DEFINITION,
    MANUAL_COMMITMENT_CONTRACT_HASH,
    MANUAL_COMMITMENT_CONTRACT_KEY,
    MANUAL_COMMITMENT_CONTRACT_VERSION,
    MANUAL_COMMITMENT_DATASET,
)
from businessflow.company_data.models import (
    CompanyDataContract,
    CompanyDataSource,
    ManualCustomerCommitment,
    ManualCustomerCommitmentEvent,
    ManualCustomerCommitmentEvidence,
)
from businessflow.data_imports.parsers import ImportFileError, read_limited_upload
from businessflow.errors import ApiError
from businessflow.metrics.snapshot import content_hash
from businessflow.models import TenantMembership, UserAccount
from businessflow.services.company_context_access import (
    CompanyContextAccess,
    require_company_context_access,
)
from businessflow.universal_data.models import CompanyDataSourceProfileVersion

MANUAL_SOURCE_KEY = "manual.customer-commitments"
MANUAL_SOURCE_NAME = "Manual customer commitments"
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_FILES = 10
MAX_HISTORY_PAGE = 100
MAX_HISTORY_OFFSET = 100_000


@dataclass(frozen=True, slots=True)
class EvidenceDownload:
    file_name: str
    media_type: str
    file_hash: str
    content: bytes


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat(timespec="microseconds")


def _date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _contract(session: Session) -> CompanyDataContract:
    contract = session.get(
        CompanyDataContract,
        (MANUAL_COMMITMENT_CONTRACT_KEY, MANUAL_COMMITMENT_CONTRACT_VERSION),
    )
    if (
        contract is None
        or contract.dataset_type != MANUAL_COMMITMENT_DATASET
        or contract.definition_hash != MANUAL_COMMITMENT_CONTRACT_HASH
        or contract.definition != MANUAL_COMMITMENT_CONTRACT_DEFINITION
        or content_hash(contract.definition) != contract.definition_hash
    ):
        raise ApiError(
            409,
            "manual_customer_commitment_contract_unavailable",
            "The published manual customer-commitment contract failed its integrity check.",
        )
    return contract


def _ensure_manual_source_profile(
    session: Session,
    source: CompanyDataSource,
    user: UserAccount,
) -> CompanyDataSourceProfileVersion:
    latest = session.scalar(
        select(CompanyDataSourceProfileVersion)
        .where(
            CompanyDataSourceProfileVersion.tenant_id == source.tenant_id,
            CompanyDataSourceProfileVersion.company_id == source.company_id,
            CompanyDataSourceProfileVersion.source_id == source.id,
        )
        .order_by(CompanyDataSourceProfileVersion.version.desc())
        .limit(1)
    )
    if latest is not None:
        return latest
    profile = CompanyDataSourceProfileVersion(
        id=uuid4(),
        tenant_id=source.tenant_id,
        company_id=source.company_id,
        source_id=source.id,
        version=1,
        source_type="manual_entry",
        display_name=source.display_name,
        business_domain="operations",
        owner_user_id=source.owner_user_id,
        source_status="enabled",
        authority_classification="manual_assertion",
        created_by_user_id=user.id,
    )
    session.add(profile)
    session.flush()
    return profile


def _ensure_manual_source(
    session: Session, user: UserAccount, access: CompanyContextAccess, request_id: UUID
) -> CompanyDataSource:
    contract = _contract(session)
    lock_key = f"manual_customer_commitment_source:{access.tenant.id}:{access.company.id}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    source = session.scalar(
        select(CompanyDataSource).where(
            CompanyDataSource.tenant_id == access.tenant.id,
            CompanyDataSource.company_id == access.company.id,
            CompanyDataSource.source_key == MANUAL_SOURCE_KEY,
        )
    )
    if source is not None:
        if (
            source.dataset_type != MANUAL_COMMITMENT_DATASET
            or source.contract_key != contract.contract_key
            or source.contract_version != contract.version
        ):
            raise ApiError(
                409,
                "manual_customer_commitment_source_integrity_error",
                "The registered manual source does not match its published contract.",
            )
        _ensure_manual_source_profile(session, source, user)
        return source

    membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == access.tenant.id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
    )
    if membership is None:
        raise ApiError(404, "tenant_not_found", "Tenant was not found.")
    source = CompanyDataSource(
        id=uuid4(),
        tenant_id=access.tenant.id,
        company_id=access.company.id,
        source_key=MANUAL_SOURCE_KEY,
        display_name=MANUAL_SOURCE_NAME,
        dataset_type=MANUAL_COMMITMENT_DATASET,
        contract_key=contract.contract_key,
        contract_version=contract.version,
        owner_user_id=user.id,
        registered_by_user_id=user.id,
        freshness_max_age_hours=None,
    )
    session.add(source)
    session.flush()
    _ensure_manual_source_profile(session, source, user)
    record_event(
        session,
        "company_data.source_registered",
        tenant_id=access.tenant.id,
        actor_user_id=user.id,
        target_type="company_data_source",
        target_id=source.id,
        request_id=request_id,
        details={
            "source_key": source.source_key,
            "dataset_type": source.dataset_type,
            "contract_key": source.contract_key,
            "contract_version": source.contract_version,
            "manual_capture": True,
        },
    )
    return source


def _membership_role(session: Session, tenant_id: UUID, user_id: UUID) -> str:
    membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
        )
    )
    if membership is None:
        raise ApiError(404, "tenant_not_found", "Tenant was not found.")
    return membership.role


def _assert_editor(
    session: Session, record: ManualCustomerCommitment, access: CompanyContextAccess
) -> None:
    if record.status not in {"draft", "changes_requested"}:
        raise ApiError(
            409,
            "manual_customer_commitment_not_editable",
            "Only a draft or a record returned for correction can be edited.",
        )
    role = _membership_role(session, access.tenant.id, access.user.id)
    if role not in {"owner", "admin"} and record.created_by_user_id != access.user.id:
        raise ApiError(
            403,
            "manual_customer_commitment_edit_forbidden",
            "Members can edit only drafts they created; owners and admins can edit "
            "any returned draft.",
        )


def _record_and_provenance(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    commitment_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[ManualCustomerCommitment, CompanyDataSource, CompanyDataContract]:
    statement = (
        select(ManualCustomerCommitment, CompanyDataSource, CompanyDataContract)
        .join(
            CompanyDataSource,
            (CompanyDataSource.tenant_id == ManualCustomerCommitment.tenant_id)
            & (CompanyDataSource.company_id == ManualCustomerCommitment.company_id)
            & (CompanyDataSource.id == ManualCustomerCommitment.source_id),
        )
        .join(
            CompanyDataContract,
            (CompanyDataContract.contract_key == ManualCustomerCommitment.contract_key)
            & (CompanyDataContract.version == ManualCustomerCommitment.contract_version),
        )
        .where(
            ManualCustomerCommitment.id == commitment_id,
            ManualCustomerCommitment.tenant_id == tenant_id,
            ManualCustomerCommitment.company_id == company_id,
        )
    )
    if for_update:
        statement = statement.with_for_update(of=ManualCustomerCommitment)
    row = session.execute(statement).one_or_none()
    if row is None:
        raise ApiError(
            404,
            "manual_customer_commitment_not_found",
            "The customer commitment was not found.",
        )
    record, source, contract = row
    if (
        source.dataset_type != MANUAL_COMMITMENT_DATASET
        or source.source_key != MANUAL_SOURCE_KEY
        or source.contract_key != record.contract_key
        or source.contract_version != record.contract_version
        or contract.definition_hash != MANUAL_COMMITMENT_CONTRACT_HASH
        or contract.definition != MANUAL_COMMITMENT_CONTRACT_DEFINITION
        or content_hash(contract.definition) != contract.definition_hash
    ):
        raise ApiError(
            409,
            "manual_customer_commitment_provenance_unverifiable",
            "The customer commitment no longer matches its registered Company Data "
            "source contract.",
        )
    return record, source, contract


def _evidence_rows(
    session: Session, record: ManualCustomerCommitment
) -> list[ManualCustomerCommitmentEvidence]:
    return list(
        session.scalars(
            select(ManualCustomerCommitmentEvidence)
            .where(
                ManualCustomerCommitmentEvidence.tenant_id == record.tenant_id,
                ManualCustomerCommitmentEvidence.company_id == record.company_id,
                ManualCustomerCommitmentEvidence.commitment_id == record.id,
            )
            .order_by(
                ManualCustomerCommitmentEvidence.created_at, ManualCustomerCommitmentEvidence.id
            )
        )
    )


def _evidence_summary(
    evidence: ManualCustomerCommitmentEvidence,
) -> ManualCustomerCommitmentEvidenceSummary:
    return ManualCustomerCommitmentEvidenceSummary(
        id=evidence.id,
        file_name=evidence.file_name,
        media_type=evidence.media_type,
        file_size_bytes=evidence.file_size_bytes,
        file_hash=evidence.file_hash,
        uploaded_by_user_id=evidence.uploaded_by_user_id,
        created_at=evidence.created_at,
    )


def _validation(
    record: ManualCustomerCommitment,
    evidence: list[ManualCustomerCommitmentEvidence],
) -> CustomerCommitmentValidation:
    errors: list[CustomerCommitmentFinding] = []
    warnings: list[CustomerCommitmentFinding] = []
    for field, value, message in (
        ("customer_name", record.customer_name, "Enter the customer name."),
        ("order_date", record.order_date, "Enter the date the commitment was recorded."),
        ("summary", record.summary, "Describe the customer commitment."),
    ):
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(
                CustomerCommitmentFinding(
                    code=f"required_{field}", severity="error", field=field, message=message
                )
            )
    if (
        record.order_date is not None
        and record.requested_delivery_date is not None
        and record.requested_delivery_date < record.order_date
    ):
        errors.append(
            CustomerCommitmentFinding(
                code="delivery_date_before_order_date",
                severity="error",
                field="requested_delivery_date",
                message="Requested delivery date cannot precede the order date.",
            )
        )
    if not record.customer_reference:
        warnings.append(
            CustomerCommitmentFinding(
                code="missing_customer_reference",
                severity="warning",
                field="customer_reference",
                message="No customer reference number is recorded.",
            )
        )
    if record.requested_delivery_date is None:
        warnings.append(
            CustomerCommitmentFinding(
                code="missing_requested_delivery_date",
                severity="warning",
                field="requested_delivery_date",
                message="No requested delivery date is recorded.",
            )
        )
    if not evidence:
        warnings.append(
            CustomerCommitmentFinding(
                code="no_supporting_evidence",
                severity="warning",
                field="evidence",
                message="No supporting evidence file is attached.",
            )
        )
    return CustomerCommitmentValidation(errors=errors, warnings=warnings)


def _summary(
    record: ManualCustomerCommitment,
    source: CompanyDataSource,
    contract: CompanyDataContract,
) -> CustomerCommitmentSummary:
    return CustomerCommitmentSummary(
        id=record.id,
        tenant_id=record.tenant_id,
        company_id=record.company_id,
        source_id=source.id,
        source_key=source.source_key,
        contract_key=contract.contract_key,
        contract_version=contract.version,
        contract_hash=contract.definition_hash,
        origin="manual",
        status=cast(CommitmentStatus, record.status),
        customer_name=record.customer_name,
        customer_reference=record.customer_reference,
        order_date=record.order_date,
        requested_delivery_date=record.requested_delivery_date,
        summary=record.summary,
        notes=record.notes,
        current_revision=record.current_revision,
        created_by_user_id=record.created_by_user_id,
        updated_by_user_id=record.updated_by_user_id,
        submitted_by_user_id=record.submitted_by_user_id,
        reviewed_by_user_id=record.reviewed_by_user_id,
        submitted_at=record.submitted_at,
        reviewed_at=record.reviewed_at,
        review_comment=record.review_comment,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _record_snapshot(
    record: ManualCustomerCommitment,
    source: CompanyDataSource,
    contract: CompanyDataContract,
    evidence: list[ManualCustomerCommitmentEvidence],
    *,
    action_context: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "snapshot_schema_version": 1,
        "commitment_id": str(record.id),
        "tenant_id": str(record.tenant_id),
        "company_id": str(record.company_id),
        "origin": "manual",
        "source": {
            "source_id": str(source.id),
            "source_key": source.source_key,
            "dataset_type": source.dataset_type,
            "contract_key": contract.contract_key,
            "contract_version": contract.version,
            "contract_hash": contract.definition_hash,
        },
        "revision": record.current_revision,
        "status": record.status,
        "customer_name": record.customer_name,
        "customer_reference": record.customer_reference,
        "order_date": _date(record.order_date),
        "requested_delivery_date": _date(record.requested_delivery_date),
        "summary": record.summary,
        "notes": record.notes,
        "created_by_user_id": str(record.created_by_user_id),
        "updated_by_user_id": str(record.updated_by_user_id),
        "submitted_by_user_id": (
            str(record.submitted_by_user_id) if record.submitted_by_user_id else None
        ),
        "reviewed_by_user_id": (
            str(record.reviewed_by_user_id) if record.reviewed_by_user_id else None
        ),
        "submitted_at": _iso(record.submitted_at),
        "reviewed_at": _iso(record.reviewed_at),
        "review_comment": record.review_comment,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "evidence": [
            {
                "evidence_id": str(item.id),
                "file_name": item.file_name,
                "media_type": item.media_type,
                "file_size_bytes": item.file_size_bytes,
                "file_hash": item.file_hash,
                "uploaded_by_user_id": str(item.uploaded_by_user_id),
                "created_at": _iso(item.created_at),
            }
            for item in evidence
        ],
    }
    if action_context:
        snapshot["action_context"] = action_context
    return snapshot


def _event_hash_payload(event: ManualCustomerCommitmentEvent) -> dict[str, object]:
    return {
        "event_id": str(event.id),
        "tenant_id": str(event.tenant_id),
        "company_id": str(event.company_id),
        "commitment_id": str(event.commitment_id),
        "current_revision": event.current_revision,
        "event_type": event.event_type,
        "actor_user_id": str(event.actor_user_id),
        "status_before": event.status_before,
        "status_after": event.status_after,
        "comment": event.comment,
        "snapshot_hash": event.snapshot_hash,
        "previous_event_hash": event.previous_event_hash,
        "occurred_at": _iso(event.occurred_at),
    }


def _verify_event(session: Session, event: ManualCustomerCommitmentEvent) -> None:
    if content_hash(event.snapshot) != event.snapshot_hash:
        raise ApiError(
            409,
            "manual_customer_commitment_history_unverifiable",
            "A customer commitment history snapshot failed its integrity check.",
        )
    if content_hash(_event_hash_payload(event)) != event.event_hash:
        raise ApiError(
            409,
            "manual_customer_commitment_history_unverifiable",
            "A customer commitment history event failed its integrity check.",
        )
    if event.current_revision == 1:
        if event.previous_event_hash is not None:
            raise ApiError(
                409,
                "manual_customer_commitment_history_unverifiable",
                "The customer commitment history chain has an invalid starting point.",
            )
    else:
        previous_hash = session.scalar(
            select(ManualCustomerCommitmentEvent.event_hash).where(
                ManualCustomerCommitmentEvent.tenant_id == event.tenant_id,
                ManualCustomerCommitmentEvent.company_id == event.company_id,
                ManualCustomerCommitmentEvent.commitment_id == event.commitment_id,
                ManualCustomerCommitmentEvent.current_revision == event.current_revision - 1,
            )
        )
        if previous_hash is None or previous_hash != event.previous_event_hash:
            raise ApiError(
                409,
                "manual_customer_commitment_history_unverifiable",
                "The customer commitment history chain is incomplete or out of order.",
            )


def _event_response(
    session: Session, event: ManualCustomerCommitmentEvent
) -> ManualCustomerCommitmentEventResponse:
    _verify_event(session, event)
    return ManualCustomerCommitmentEventResponse(
        id=event.id,
        commitment_id=event.commitment_id,
        current_revision=event.current_revision,
        event_type=event.event_type,
        actor_user_id=event.actor_user_id,
        status_before=cast(CommitmentStatus | None, event.status_before),
        status_after=cast(CommitmentStatus, event.status_after),
        comment=event.comment,
        snapshot=event.snapshot,
        snapshot_hash=event.snapshot_hash,
        previous_event_hash=event.previous_event_hash,
        event_hash=event.event_hash,
        occurred_at=event.occurred_at,
    )


def _append_event(
    session: Session,
    record: ManualCustomerCommitment,
    source: CompanyDataSource,
    contract: CompanyDataContract,
    user: UserAccount,
    *,
    event_type: str,
    status_before: str | None,
    comment: str | None = None,
    action_context: dict[str, object] | None = None,
    occurred_at: datetime,
) -> ManualCustomerCommitmentEvent:
    evidence = _evidence_rows(session, record)
    snapshot = _record_snapshot(record, source, contract, evidence, action_context=action_context)
    previous = session.scalar(
        select(ManualCustomerCommitmentEvent)
        .where(
            ManualCustomerCommitmentEvent.tenant_id == record.tenant_id,
            ManualCustomerCommitmentEvent.company_id == record.company_id,
            ManualCustomerCommitmentEvent.commitment_id == record.id,
        )
        .order_by(ManualCustomerCommitmentEvent.current_revision.desc())
        .limit(1)
    )
    event_id = uuid4()
    snapshot_hash = content_hash(snapshot)
    event = ManualCustomerCommitmentEvent(
        id=event_id,
        tenant_id=record.tenant_id,
        company_id=record.company_id,
        commitment_id=record.id,
        current_revision=record.current_revision,
        event_type=event_type,
        actor_user_id=user.id,
        status_before=status_before,
        status_after=record.status,
        comment=comment,
        snapshot=snapshot,
        snapshot_hash=snapshot_hash,
        previous_event_hash=previous.event_hash if previous else None,
        event_hash="0" * 64,
        occurred_at=occurred_at,
    )
    event.event_hash = content_hash(_event_hash_payload(event))
    session.add(event)
    session.flush()
    return event


def _detail(
    session: Session,
    record: ManualCustomerCommitment,
    source: CompanyDataSource,
    contract: CompanyDataContract,
) -> CustomerCommitmentDetailResponse:
    evidence = _evidence_rows(session, record)
    return CustomerCommitmentDetailResponse(
        commitment=_summary(record, source, contract),
        validation=_validation(record, evidence),
        evidence=[_evidence_summary(item) for item in evidence],
    )


def list_commitments(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    status: CommitmentStatus | None = None,
) -> CustomerCommitmentPage:
    access = require_company_context_access(session, user, tenant_id, "customer_commitment:read")
    filters = [
        ManualCustomerCommitment.tenant_id == tenant_id,
        ManualCustomerCommitment.company_id == access.company.id,
    ]
    if status is not None:
        filters.append(ManualCustomerCommitment.status == status)
    total = (
        session.scalar(select(func.count()).select_from(ManualCustomerCommitment).where(*filters))
        or 0
    )
    rows = session.execute(
        select(ManualCustomerCommitment, CompanyDataSource, CompanyDataContract)
        .join(
            CompanyDataSource,
            (CompanyDataSource.tenant_id == ManualCustomerCommitment.tenant_id)
            & (CompanyDataSource.company_id == ManualCustomerCommitment.company_id)
            & (CompanyDataSource.id == ManualCustomerCommitment.source_id),
        )
        .join(
            CompanyDataContract,
            (CompanyDataContract.contract_key == ManualCustomerCommitment.contract_key)
            & (CompanyDataContract.version == ManualCustomerCommitment.contract_version),
        )
        .where(*filters)
        .order_by(ManualCustomerCommitment.updated_at.desc(), ManualCustomerCommitment.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return CustomerCommitmentPage(
        items=[_summary(record, source, contract) for record, source, contract in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_commitment(
    session: Session, user: UserAccount, tenant_id: UUID, commitment_id: UUID
) -> CustomerCommitmentDetailResponse:
    access = require_company_context_access(session, user, tenant_id, "customer_commitment:read")
    record, source, contract = _record_and_provenance(
        session, tenant_id, access.company.id, commitment_id
    )
    return _detail(session, record, source, contract)


def create_draft(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: CustomerCommitmentCreateRequest,
    request_id: UUID,
) -> CustomerCommitmentDetailResponse:
    access = require_company_context_access(
        session, user, tenant_id, "customer_commitment:write", request_id=request_id
    )
    source = _ensure_manual_source(session, user, access, request_id)
    contract = _contract(session)
    now = datetime.now(UTC)
    values = body.model_dump()
    record = ManualCustomerCommitment(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source.id,
        contract_key=contract.contract_key,
        contract_version=contract.version,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
        status="draft",
        current_revision=1,
        created_at=now,
        updated_at=now,
        **values,
    )
    session.add(record)
    session.flush()
    _append_event(
        session,
        record,
        source,
        contract,
        user,
        event_type="draft_created",
        status_before=None,
        occurred_at=now,
    )
    record_event(
        session,
        "manual_customer_commitment.draft_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="manual_customer_commitment",
        target_id=record.id,
        request_id=request_id,
        details={"source_id": str(source.id), "origin": "manual", "revision": 1},
    )
    return _detail(session, record, source, contract)


def save_draft(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    commitment_id: UUID,
    body: CustomerCommitmentUpdateRequest,
    request_id: UUID,
) -> CustomerCommitmentDetailResponse:
    access = require_company_context_access(
        session, user, tenant_id, "customer_commitment:write", request_id=request_id
    )
    record, source, contract = _record_and_provenance(
        session, tenant_id, access.company.id, commitment_id, for_update=True
    )
    _assert_editor(session, record, access)
    if body.expected_revision != record.current_revision:
        raise ApiError(
            409,
            "manual_customer_commitment_revision_conflict",
            "This draft changed since it was opened. Refresh the record before saving again.",
        )
    previous_status = record.status
    for field, value in body.model_dump(exclude={"expected_revision"}).items():
        setattr(record, field, value)
    record.current_revision += 1
    record.updated_by_user_id = user.id
    now = datetime.now(UTC)
    record.updated_at = now
    session.flush()
    event_type = "draft_saved" if previous_status == "draft" else "corrected"
    _append_event(
        session,
        record,
        source,
        contract,
        user,
        event_type=event_type,
        status_before=previous_status,
        occurred_at=now,
    )
    record_event(
        session,
        f"manual_customer_commitment.{event_type}",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="manual_customer_commitment",
        target_id=record.id,
        request_id=request_id,
        details={"revision": record.current_revision, "status": record.status},
    )
    return _detail(session, record, source, contract)


def submit_for_review(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    commitment_id: UUID,
    body: CustomerCommitmentSubmitRequest,
    request_id: UUID,
) -> CustomerCommitmentDetailResponse:
    access = require_company_context_access(
        session, user, tenant_id, "customer_commitment:write", request_id=request_id
    )
    record, source, contract = _record_and_provenance(
        session, tenant_id, access.company.id, commitment_id, for_update=True
    )
    _assert_editor(session, record, access)
    if body.expected_revision != record.current_revision:
        raise ApiError(
            409,
            "manual_customer_commitment_revision_conflict",
            "This draft changed since it was opened. Refresh the record before submitting it.",
        )
    evidence = _evidence_rows(session, record)
    validation = _validation(record, evidence)
    if validation.errors:
        raise ApiError(
            422,
            "manual_customer_commitment_validation_failed",
            "Complete or correct the required information before submitting this record.",
        )
    if validation.warnings and not body.acknowledge_warnings:
        raise ApiError(
            422,
            "manual_customer_commitment_warnings_unacknowledged",
            "Review and acknowledge the optional-information and evidence warnings "
            "before submitting.",
        )

    previous_status = record.status
    now = datetime.now(UTC)
    record.status = "submitted"
    record.current_revision += 1
    record.updated_by_user_id = user.id
    record.submitted_by_user_id = user.id
    record.submitted_at = now
    record.reviewed_by_user_id = None
    record.reviewed_at = None
    record.review_comment = None
    record.updated_at = now
    session.flush()
    _append_event(
        session,
        record,
        source,
        contract,
        user,
        event_type="submitted",
        status_before=previous_status,
        action_context={
            "warnings_acknowledged": body.acknowledge_warnings,
            "warning_codes": [item.code for item in validation.warnings],
            "origin": "manual",
            "manual_submission_is_not_factual_verification": True,
        },
        occurred_at=now,
    )
    record_event(
        session,
        "manual_customer_commitment.submitted",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="manual_customer_commitment",
        target_id=record.id,
        request_id=request_id,
        details={
            "revision": record.current_revision,
            "warnings_acknowledged": body.acknowledge_warnings,
        },
    )
    return _detail(session, record, source, contract)


def review_commitment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    commitment_id: UUID,
    body: CustomerCommitmentReviewRequest,
    request_id: UUID,
) -> CustomerCommitmentDetailResponse:
    access = require_company_context_access(
        session, user, tenant_id, "customer_commitment:review", request_id=request_id
    )
    record, source, contract = _record_and_provenance(
        session, tenant_id, access.company.id, commitment_id, for_update=True
    )
    if record.status != "submitted":
        raise ApiError(
            409,
            "manual_customer_commitment_not_pending_review",
            "Only a submitted customer commitment can be reviewed.",
        )
    if body.expected_revision != record.current_revision:
        raise ApiError(
            409,
            "manual_customer_commitment_revision_conflict",
            "This record changed since it was opened. Refresh it before reviewing.",
        )
    if record.submitted_by_user_id == user.id:
        raise ApiError(
            403,
            "manual_customer_commitment_self_review_forbidden",
            "A submitter cannot review their own submission.",
        )
    evidence = _evidence_rows(session, record)
    validation = _validation(record, evidence)
    if validation.errors:
        raise ApiError(
            409,
            "manual_customer_commitment_validation_failed",
            "The submitted record no longer meets the required intake criteria.",
        )
    if body.decision == "accept":
        if not body.criteria_confirmed:
            raise ApiError(
                422,
                "manual_customer_commitment_review_criteria_required",
                "Confirm that the record meets the stated intake criteria before accepting it.",
            )
        if validation.warnings and not body.acknowledge_warnings:
            raise ApiError(
                422,
                "manual_customer_commitment_review_warnings_unacknowledged",
                "Review and acknowledge outstanding warnings before accepting this record.",
            )
        next_status = "accepted"
        event_type = "accepted"
        audit_type = "manual_customer_commitment.accepted"
    elif body.decision == "request_correction":
        if not body.comment:
            raise ApiError(
                422,
                "manual_customer_commitment_correction_comment_required",
                "Explain what must be corrected before returning this record.",
            )
        next_status = "changes_requested"
        event_type = "correction_requested"
        audit_type = "manual_customer_commitment.correction_requested"
    else:
        if not body.comment:
            raise ApiError(
                422,
                "manual_customer_commitment_rejection_reason_required",
                "Provide a reason when rejecting this record.",
            )
        next_status = "rejected"
        event_type = "rejected"
        audit_type = "manual_customer_commitment.rejected"

    previous_status = record.status
    now = datetime.now(UTC)
    record.status = next_status
    record.current_revision += 1
    record.updated_by_user_id = user.id
    record.reviewed_by_user_id = user.id
    record.reviewed_at = now
    record.review_comment = body.comment
    record.updated_at = now
    session.flush()
    action_context: dict[str, object] = {
        "decision": body.decision,
        "process_criteria_confirmed": body.criteria_confirmed,
        "warnings_acknowledged": body.acknowledge_warnings,
        "warning_codes": [item.code for item in validation.warnings],
        "review_confirms_intake_criteria_not_underlying_event_truth": True,
    }
    _append_event(
        session,
        record,
        source,
        contract,
        user,
        event_type=event_type,
        status_before=previous_status,
        comment=body.comment,
        action_context=action_context,
        occurred_at=now,
    )
    record_event(
        session,
        audit_type,
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="manual_customer_commitment",
        target_id=record.id,
        request_id=request_id,
        details={
            "revision": record.current_revision,
            "decision": body.decision,
            "process_criteria_confirmed": body.criteria_confirmed,
            "warnings_acknowledged": body.acknowledge_warnings,
        },
    )
    return _detail(session, record, source, contract)


def list_history(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    commitment_id: UUID,
    *,
    limit: int,
    offset: int,
) -> CustomerCommitmentHistoryPage:
    access = require_company_context_access(session, user, tenant_id, "customer_commitment:read")
    _record_and_provenance(session, tenant_id, access.company.id, commitment_id)
    filters = (
        ManualCustomerCommitmentEvent.tenant_id == tenant_id,
        ManualCustomerCommitmentEvent.company_id == access.company.id,
        ManualCustomerCommitmentEvent.commitment_id == commitment_id,
    )
    total = (
        session.scalar(
            select(func.count()).select_from(ManualCustomerCommitmentEvent).where(*filters)
        )
        or 0
    )
    rows = list(
        session.scalars(
            select(ManualCustomerCommitmentEvent)
            .where(*filters)
            .order_by(ManualCustomerCommitmentEvent.current_revision.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CustomerCommitmentHistoryPage(
        items=[_event_response(session, event) for event in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _safe_file_name(filename: str | None) -> str:
    if not filename:
        raise ApiError(422, "invalid_evidence_file_name", "Choose a named evidence file.")
    leaf = filename.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[\x00-\x1f\x7f]", "_", leaf).strip(" .")
    if not safe or len(safe) > 255 or "/" in safe or "\\" in safe:
        raise ApiError(422, "invalid_evidence_file_name", "The evidence file name is invalid.")
    return safe


def _detect_evidence_type(filename: str, declared_type: str | None, content: bytes) -> str:
    suffix = PurePosixPath(filename).suffix.casefold()
    if suffix == ".pdf" and content.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif suffix in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif suffix == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif suffix == ".txt":
        if b"\x00" in content:
            detected = ""
        else:
            try:
                content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                detected = ""
            else:
                detected = "text/plain"
    else:
        detected = ""
    if not detected:
        raise ApiError(
            415,
            "unsupported_evidence_file",
            "Attach a valid PDF, PNG, JPEG, or UTF-8 plain-text file.",
        )
    normalized_declared = (declared_type or "").split(";", 1)[0].strip().casefold()
    if normalized_declared and normalized_declared not in {detected, "application/octet-stream"}:
        raise ApiError(
            415,
            "evidence_media_type_mismatch",
            "The declared file type does not match the supported evidence format.",
        )
    return detected


def add_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    commitment_id: UUID,
    *,
    filename: str | None,
    declared_type: str | None,
    upload: BinaryIO,
    request_id: UUID,
) -> ManualCustomerCommitmentEvidenceResponse:
    access = require_company_context_access(
        session, user, tenant_id, "customer_commitment:write", request_id=request_id
    )
    record, source, contract = _record_and_provenance(
        session, tenant_id, access.company.id, commitment_id, for_update=True
    )
    _assert_editor(session, record, access)
    safe_name = _safe_file_name(filename)
    try:
        content = read_limited_upload(upload, MAX_EVIDENCE_BYTES)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    media_type = _detect_evidence_type(safe_name, declared_type, content)
    evidence = ManualCustomerCommitmentEvidence(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        commitment_id=record.id,
        uploaded_by_user_id=user.id,
        file_name=safe_name,
        media_type=media_type,
        file_size_bytes=len(content),
        file_hash=hashlib.sha256(content).hexdigest(),
        content_bytes=content,
        created_at=datetime.now(UTC),
    )
    session.add(evidence)
    session.flush()
    previous_status = record.status
    now = datetime.now(UTC)
    record.current_revision += 1
    record.updated_by_user_id = user.id
    record.updated_at = now
    session.flush()
    _append_event(
        session,
        record,
        source,
        contract,
        user,
        event_type="evidence_added",
        status_before=previous_status,
        action_context={
            "evidence_id": str(evidence.id),
            "file_name": evidence.file_name,
            "file_hash": evidence.file_hash,
            "file_size_bytes": evidence.file_size_bytes,
            "evidence_is_user_supplied_not_independently_verified": True,
        },
        occurred_at=now,
    )
    record_event(
        session,
        "manual_customer_commitment.evidence_added",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="manual_customer_commitment",
        target_id=record.id,
        request_id=request_id,
        details={
            "revision": record.current_revision,
            "evidence_id": str(evidence.id),
            "file_hash": evidence.file_hash,
            "file_size_bytes": evidence.file_size_bytes,
        },
    )
    return ManualCustomerCommitmentEvidenceResponse(evidence=_evidence_summary(evidence))


def download_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    commitment_id: UUID,
    evidence_id: UUID,
) -> EvidenceDownload:
    access = require_company_context_access(session, user, tenant_id, "customer_commitment:read")
    _record_and_provenance(session, tenant_id, access.company.id, commitment_id)
    evidence = session.scalar(
        select(ManualCustomerCommitmentEvidence).where(
            ManualCustomerCommitmentEvidence.id == evidence_id,
            ManualCustomerCommitmentEvidence.tenant_id == tenant_id,
            ManualCustomerCommitmentEvidence.company_id == access.company.id,
            ManualCustomerCommitmentEvidence.commitment_id == commitment_id,
        )
    )
    if evidence is None:
        raise ApiError(
            404,
            "manual_customer_commitment_evidence_not_found",
            "The customer commitment evidence file was not found.",
        )
    if (
        len(evidence.content_bytes) != evidence.file_size_bytes
        or hashlib.sha256(evidence.content_bytes).hexdigest() != evidence.file_hash
    ):
        raise ApiError(
            409,
            "manual_customer_commitment_evidence_unverifiable",
            "The saved evidence file failed its integrity check.",
        )
    return EvidenceDownload(
        file_name=evidence.file_name,
        media_type=evidence.media_type,
        file_hash=evidence.file_hash,
        content=evidence.content_bytes,
    )
