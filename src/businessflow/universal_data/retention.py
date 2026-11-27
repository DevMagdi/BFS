"""Explicit, company-scoped retention and source lifecycle operations.

No retention policy is synthesized here. Purging remains unavailable until a Company owner or
admin configures an exact policy version and a different authorized member approves it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.business_semantic.models import BusinessSemanticMappingVersion
from businessflow.company_data.models import CompanyDataSource
from businessflow.errors import ApiError
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.universal_data.models import (
    CanonicalBusinessRecordRevision,
    CompanyDataSourceProfileVersion,
    UniversalIngestion,
    UniversalIngestionEvent,
    UniversalIngestionRecordLink,
    UniversalIngestionRow,
    UniversalRetentionPolicyApproval,
    UniversalRetentionPolicyRevocation,
    UniversalRetentionPolicyVersion,
    UniversalRowValidation,
    UniversalSourceLifecycleEvent,
    UniversalSourceSnapshot,
)

MAX_POLICY_DAYS = 36_500
MAX_PURGE_BATCH = 100
MAX_PLAN_PAGE = 100


def _require_retention_access(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    request_id: UUID | None = None,
):
    # The default role matrix grants both of these permissions to Company owners/admins only.
    # Keep both checks aligned with the existing Universal Data write contract.
    access = require_company_context_access(
        session,
        user,
        tenant_id,
        "company_context:write",
        request_id=request_id,
    )
    require_company_context_access(
        session,
        user,
        tenant_id,
        "universal_data:read",
        request_id=request_id,
    )
    return access


def _advisory_lock(session: Session, key: str) -> None:
    # This is the same PostgreSQL hash-based transaction lock used by Universal Data writes.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


def _lock_company(session: Session, tenant_id: UUID, company_id: UUID) -> None:
    _advisory_lock(session, f"universal-retention:{tenant_id}:{company_id}")


def _lock_source(session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID) -> None:
    _advisory_lock(session, f"universal-source:{tenant_id}:{company_id}:{source_id}")


def _audit(
    session: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    action: str,
    target_type: str,
    target_id: UUID,
    request_id: UUID | None,
    details: dict[str, Any],
) -> None:
    # Only bounded governance metadata is recorded. Never include file content, path, row data,
    # raw idempotency keys, or credentials in audit details.
    record_event(
        session,
        action,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
        details=details,
    )


def _not_found() -> ApiError:
    return ApiError(404, "universal_source_not_found", "The Universal Data source was not found.")


def _source(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
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
        raise _not_found()
    return source


def _latest_policy(
    session: Session, *, tenant_id: UUID, company_id: UUID
) -> UniversalRetentionPolicyVersion | None:
    return session.scalar(
        select(UniversalRetentionPolicyVersion)
        .where(
            UniversalRetentionPolicyVersion.tenant_id == tenant_id,
            UniversalRetentionPolicyVersion.company_id == company_id,
        )
        .order_by(UniversalRetentionPolicyVersion.version.desc())
        .limit(1)
    )


def _active_policy(
    session: Session, *, tenant_id: UUID, company_id: UUID
) -> UniversalRetentionPolicyVersion | None:
    policy = _latest_policy(session, tenant_id=tenant_id, company_id=company_id)
    if policy is None:
        return None
    approved = session.scalar(
        select(UniversalRetentionPolicyApproval.id).where(
            UniversalRetentionPolicyApproval.tenant_id == tenant_id,
            UniversalRetentionPolicyApproval.company_id == company_id,
            UniversalRetentionPolicyApproval.policy_version_id == policy.id,
            UniversalRetentionPolicyApproval.approved_by_user_id != policy.created_by_user_id,
        )
    )
    if approved is None:
        return None
    revoked = session.scalar(
        select(UniversalRetentionPolicyRevocation.id).where(
            UniversalRetentionPolicyRevocation.tenant_id == tenant_id,
            UniversalRetentionPolicyRevocation.company_id == company_id,
            UniversalRetentionPolicyRevocation.policy_version_id == policy.id,
        )
    )
    return None if revoked is not None else policy


def _policy_summary(
    session: Session,
    policy: UniversalRetentionPolicyVersion,
    *,
    active_policy_id: UUID | None,
) -> dict[str, Any]:
    approval = session.scalar(
        select(UniversalRetentionPolicyApproval).where(
            UniversalRetentionPolicyApproval.tenant_id == policy.tenant_id,
            UniversalRetentionPolicyApproval.company_id == policy.company_id,
            UniversalRetentionPolicyApproval.policy_version_id == policy.id,
        )
    )
    revocation = session.scalar(
        select(UniversalRetentionPolicyRevocation).where(
            UniversalRetentionPolicyRevocation.tenant_id == policy.tenant_id,
            UniversalRetentionPolicyRevocation.company_id == policy.company_id,
            UniversalRetentionPolicyRevocation.policy_version_id == policy.id,
        )
    )
    return {
        "policy_version_id": str(policy.id),
        "version": policy.version,
        "retention_days": policy.retention_days,
        "created_by_user_id": str(policy.created_by_user_id),
        "created_at": policy.created_at,
        "approved_by_user_id": str(approval.approved_by_user_id) if approval else None,
        "approved_at": approval.approved_at if approval else None,
        "revoked_by_user_id": str(revocation.revoked_by_user_id) if revocation else None,
        "revoked_at": revocation.revoked_at if revocation else None,
        "active": policy.id == active_policy_id,
    }


def list_retention_policies(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    policies = list(
        session.scalars(
            select(UniversalRetentionPolicyVersion)
            .where(
                UniversalRetentionPolicyVersion.tenant_id == tenant_id,
                UniversalRetentionPolicyVersion.company_id == access.company.id,
            )
            .order_by(UniversalRetentionPolicyVersion.version.desc())
        )
    )
    active = _active_policy(session, tenant_id=tenant_id, company_id=access.company.id)
    return {
        "items": [
            _policy_summary(session, policy, active_policy_id=active.id if active else None)
            for policy in policies
        ],
        "total": len(policies),
        "active_policy_version_id": str(active.id) if active else None,
        "purge_enabled": active is not None,
    }


def configure_retention_policy(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    retention_days: int,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    if isinstance(retention_days, bool) or not isinstance(retention_days, int):
        raise ApiError(422, "invalid_retention_period", "retention_days must be an integer.")
    if not 1 <= retention_days <= MAX_POLICY_DAYS:
        raise ApiError(
            422,
            "invalid_retention_period",
            f"retention_days must be between 1 and {MAX_POLICY_DAYS}.",
        )
    _lock_company(session, tenant_id, access.company.id)
    latest = _latest_policy(session, tenant_id=tenant_id, company_id=access.company.id)
    policy = UniversalRetentionPolicyVersion(
        tenant_id=tenant_id,
        company_id=access.company.id,
        version=(latest.version if latest else 0) + 1,
        retention_days=retention_days,
        created_by_user_id=user.id,
    )
    session.add(policy)
    session.flush()
    _audit(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        action="universal_retention.policy_configured",
        target_type="universal_retention_policy",
        target_id=policy.id,
        request_id=request_id,
        details={"policy_version": policy.version, "retention_days": policy.retention_days},
    )
    return {
        "policy_version_id": str(policy.id),
        "version": policy.version,
        "retention_days": policy.retention_days,
        "created_by_user_id": str(policy.created_by_user_id),
        "created_at": policy.created_at,
        "approved_by_user_id": None,
        "approved_at": None,
        "revoked_by_user_id": None,
        "revoked_at": None,
        "active": False,
    }


def approve_retention_policy(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    policy_version_id: UUID,
    *,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    _lock_company(session, tenant_id, access.company.id)
    policy = session.scalar(
        select(UniversalRetentionPolicyVersion).where(
            UniversalRetentionPolicyVersion.tenant_id == tenant_id,
            UniversalRetentionPolicyVersion.company_id == access.company.id,
            UniversalRetentionPolicyVersion.id == policy_version_id,
        )
    )
    if policy is None:
        raise ApiError(404, "retention_policy_not_found", "The retention policy was not found.")
    latest = _latest_policy(session, tenant_id=tenant_id, company_id=access.company.id)
    if latest is None or policy.id != latest.id:
        raise ApiError(
            409, "retention_policy_not_latest", "Only the latest configured policy can be approved."
        )
    if policy.created_by_user_id == user.id:
        raise ApiError(
            409,
            "retention_policy_separation_required",
            "Policy approval must be performed by a different Company owner or admin.",
        )
    existing = session.scalar(
        select(UniversalRetentionPolicyApproval.id).where(
            UniversalRetentionPolicyApproval.tenant_id == tenant_id,
            UniversalRetentionPolicyApproval.company_id == access.company.id,
            UniversalRetentionPolicyApproval.policy_version_id == policy.id,
        )
    )
    if existing is not None:
        raise ApiError(
            409, "retention_policy_already_approved", "This policy version is already approved."
        )
    approval = UniversalRetentionPolicyApproval(
        tenant_id=tenant_id,
        company_id=access.company.id,
        policy_version_id=policy.id,
        approved_by_user_id=user.id,
    )
    session.add(approval)
    session.flush()
    _audit(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        action="universal_retention.policy_approved",
        target_type="universal_retention_policy",
        target_id=policy.id,
        request_id=request_id,
        details={"policy_version": policy.version},
    )
    return _policy_summary(session, policy, active_policy_id=policy.id)


def revoke_retention_policy(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    policy_version_id: UUID,
    *,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    _lock_company(session, tenant_id, access.company.id)
    policy = _active_policy(session, tenant_id=tenant_id, company_id=access.company.id)
    if policy is None or policy.id != policy_version_id:
        raise ApiError(
            409, "retention_policy_not_active", "Only the active retention policy can be revoked."
        )
    revocation = UniversalRetentionPolicyRevocation(
        tenant_id=tenant_id,
        company_id=access.company.id,
        policy_version_id=policy.id,
        revoked_by_user_id=user.id,
    )
    session.add(revocation)
    session.flush()
    _audit(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        action="universal_retention.policy_revoked",
        target_type="universal_retention_policy",
        target_id=policy.id,
        request_id=request_id,
        details={"policy_version": policy.version},
    )
    return _policy_summary(session, policy, active_policy_id=None)


def _source_revoked(
    session: Session, *, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> bool:
    return (
        session.scalar(
            select(UniversalSourceLifecycleEvent.id).where(
                UniversalSourceLifecycleEvent.tenant_id == tenant_id,
                UniversalSourceLifecycleEvent.company_id == company_id,
                UniversalSourceLifecycleEvent.source_id == source_id,
                UniversalSourceLifecycleEvent.event_type == "revoked",
            )
        )
        is not None
    )


def _dependency_counts(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
) -> dict[str, int]:
    revision_ids = select(CanonicalBusinessRecordRevision.record_id).where(
        CanonicalBusinessRecordRevision.tenant_id == tenant_id,
        CanonicalBusinessRecordRevision.company_id == company_id,
        CanonicalBusinessRecordRevision.source_id == source_id,
        CanonicalBusinessRecordRevision.ingestion_id == ingestion_id,
    )
    revision_count = (
        session.scalar(
            select(func.count())
            .select_from(CanonicalBusinessRecordRevision)
            .where(
                CanonicalBusinessRecordRevision.tenant_id == tenant_id,
                CanonicalBusinessRecordRevision.company_id == company_id,
                CanonicalBusinessRecordRevision.source_id == source_id,
                CanonicalBusinessRecordRevision.ingestion_id == ingestion_id,
            )
        )
        or 0
    )
    canonical_link_count = (
        session.scalar(
            select(func.count())
            .select_from(UniversalIngestionRecordLink)
            .where(
                UniversalIngestionRecordLink.tenant_id == tenant_id,
                UniversalIngestionRecordLink.company_id == company_id,
                UniversalIngestionRecordLink.ingestion_id == ingestion_id,
                UniversalIngestionRecordLink.canonical_record_id.is_not(None),
            )
        )
        or 0
    )
    semantic_mapping_count = (
        session.scalar(
            select(func.count())
            .select_from(BusinessSemanticMappingVersion)
            .where(
                BusinessSemanticMappingVersion.tenant_id == tenant_id,
                BusinessSemanticMappingVersion.company_id == company_id,
                BusinessSemanticMappingVersion.source_id == source_id,
                BusinessSemanticMappingVersion.universal_record_id.in_(revision_ids),
            )
        )
        or 0
    )
    return {
        "canonical_record_revisions": int(revision_count),
        "canonical_record_links": int(canonical_link_count),
        "semantic_mapping_versions": int(semantic_mapping_count),
    }


def build_purge_plan(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    now: datetime | None = None,
    limit: int = MAX_PLAN_PAGE,
    offset: int = 0,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    if not 1 <= limit <= MAX_PLAN_PAGE or not 0 <= offset <= 1_000_000:
        raise ApiError(
            422,
            "invalid_purge_plan_page",
            "Use a plan page size from 1 to 100 and an offset from 0 to 1,000,000.",
        )
    _lock_company(session, tenant_id, access.company.id)
    _lock_source(session, tenant_id, access.company.id, source_id)
    source = _source(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source_id,
    )
    revoked = _source_revoked(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source_id,
    )
    policy = _active_policy(session, tenant_id=tenant_id, company_id=access.company.id)
    as_of = now or datetime.now(UTC)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ApiError(422, "invalid_plan_time", "The purge plan time must include a timezone.")

    reason: str | None = None
    if not revoked:
        reason = "source_not_revoked"
    elif policy is None:
        reason = "no_configured_approved_active_retention_policy"

    if reason is not None:
        return {
            "source_id": str(source.id),
            "source_revoked": revoked,
            "purge_enabled": False,
            "dry_run": True,
            "reason": reason,
            "as_of": as_of,
            "policy": None,
            "eligible_ingestions": [],
            "blocked_ingestions": [],
            "total_eligible": 0,
            "total_blocked": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "snapshots": [],
        }

    assert policy is not None
    cutoff = as_of - timedelta(days=policy.retention_days)
    aged = list(
        session.scalars(
            select(UniversalIngestion)
            .where(
                UniversalIngestion.tenant_id == tenant_id,
                UniversalIngestion.company_id == access.company.id,
                UniversalIngestion.source_id == source_id,
                UniversalIngestion.created_at <= cutoff,
            )
            .order_by(UniversalIngestion.created_at, UniversalIngestion.id)
        )
    )
    eligible: list[UniversalIngestion] = []
    blocked: list[dict[str, Any]] = []
    for ingestion in aged:
        dependencies = _dependency_counts(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            source_id=source_id,
            ingestion_id=ingestion.id,
        )
        blocker_codes = [name for name, count in dependencies.items() if count]
        snapshot_exists = session.scalar(
            select(UniversalSourceSnapshot.id).where(
                UniversalSourceSnapshot.tenant_id == tenant_id,
                UniversalSourceSnapshot.company_id == access.company.id,
                UniversalSourceSnapshot.source_id == source_id,
                UniversalSourceSnapshot.id == ingestion.snapshot_id,
            )
        )
        if snapshot_exists is None:
            blocker_codes.append("missing_original_snapshot")
        if blocker_codes:
            blocked.append(
                {
                    "ingestion_id": str(ingestion.id),
                    "created_at": ingestion.created_at,
                    "blockers": sorted(blocker_codes),
                    "dependency_counts": dependencies,
                }
            )
        else:
            eligible.append(ingestion)

    page = eligible[offset : offset + limit]
    page_ids = [item.id for item in page]
    page_id_set = set(page_ids)
    ingestion_items: list[dict[str, Any]] = []
    snapshot_ids: set[UUID] = set()
    for ingestion in page:
        snapshot_ids.add(ingestion.snapshot_id)
        row_count = (
            session.scalar(
                select(func.count())
                .select_from(UniversalIngestionRow)
                .where(
                    UniversalIngestionRow.tenant_id == tenant_id,
                    UniversalIngestionRow.company_id == access.company.id,
                    UniversalIngestionRow.source_id == source_id,
                    UniversalIngestionRow.ingestion_id == ingestion.id,
                )
            )
            or 0
        )
        validation_count = (
            session.scalar(
                select(func.count())
                .select_from(UniversalRowValidation)
                .where(
                    UniversalRowValidation.tenant_id == tenant_id,
                    UniversalRowValidation.company_id == access.company.id,
                    UniversalRowValidation.source_id == source_id,
                    UniversalRowValidation.ingestion_id == ingestion.id,
                )
            )
            or 0
        )
        event_count = (
            session.scalar(
                select(func.count())
                .select_from(UniversalIngestionEvent)
                .where(
                    UniversalIngestionEvent.tenant_id == tenant_id,
                    UniversalIngestionEvent.company_id == access.company.id,
                    UniversalIngestionEvent.ingestion_id == ingestion.id,
                )
            )
            or 0
        )
        link_count = (
            session.scalar(
                select(func.count())
                .select_from(UniversalIngestionRecordLink)
                .where(
                    UniversalIngestionRecordLink.tenant_id == tenant_id,
                    UniversalIngestionRecordLink.company_id == access.company.id,
                    UniversalIngestionRecordLink.ingestion_id == ingestion.id,
                )
            )
            or 0
        )
        ingestion_items.append(
            {
                "ingestion_id": str(ingestion.id),
                "created_at": ingestion.created_at,
                "status": ingestion.status,
                "snapshot_id": str(ingestion.snapshot_id),
                "row_count": int(row_count),
                "validation_count": int(validation_count),
                "event_count": int(event_count),
                "record_link_count": int(link_count),
            }
        )

    snapshot_items: list[dict[str, Any]] = []
    for snapshot_id in sorted(snapshot_ids, key=str):
        snapshot = session.scalar(
            select(UniversalSourceSnapshot).where(
                UniversalSourceSnapshot.tenant_id == tenant_id,
                UniversalSourceSnapshot.company_id == access.company.id,
                UniversalSourceSnapshot.source_id == source_id,
                UniversalSourceSnapshot.id == snapshot_id,
            )
        )
        if snapshot is None:
            continue
        retained_ingestion_count = (
            session.scalar(
                select(func.count())
                .select_from(UniversalIngestion)
                .where(
                    UniversalIngestion.tenant_id == tenant_id,
                    UniversalIngestion.company_id == access.company.id,
                    UniversalIngestion.source_id == source_id,
                    UniversalIngestion.snapshot_id == snapshot_id,
                    UniversalIngestion.id.not_in(page_id_set),
                )
            )
            or 0
        )
        revision_count = (
            session.scalar(
                select(func.count())
                .select_from(CanonicalBusinessRecordRevision)
                .where(
                    CanonicalBusinessRecordRevision.tenant_id == tenant_id,
                    CanonicalBusinessRecordRevision.company_id == access.company.id,
                    CanonicalBusinessRecordRevision.source_id == source_id,
                    CanonicalBusinessRecordRevision.snapshot_id == snapshot_id,
                )
            )
            or 0
        )
        snapshot_items.append(
            {
                "snapshot_id": str(snapshot.id),
                "file_name": snapshot.file_name,
                "file_format": snapshot.file_format,
                "created_at": snapshot.created_at,
                "file_size_bytes": snapshot.file_size_bytes,
                "content_hash": snapshot.input_fingerprint,
                "referencing_ingestions_outside_page": int(retained_ingestion_count),
                "canonical_revision_references": int(revision_count),
                "will_be_deleted_if_all_page_ingestions_are_purged": (
                    retained_ingestion_count == 0 and revision_count == 0
                ),
            }
        )

    plan = {
        "source_id": str(source.id),
        "source_revoked": True,
        "purge_enabled": True,
        "dry_run": True,
        "reason": None,
        "as_of": as_of,
        "policy": {
            "policy_version_id": str(policy.id),
            "version": policy.version,
            "retention_days": policy.retention_days,
            "cutoff_at": cutoff,
        },
        "eligible_ingestions": ingestion_items,
        "blocked_ingestions": blocked,
        "total_eligible": len(eligible),
        "total_blocked": len(blocked),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(eligible),
        "snapshots": snapshot_items,
    }
    _audit(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        action="universal_retention.purge_plan_created",
        target_type="universal_data_source",
        target_id=source.id,
        request_id=request_id,
        details={
            "policy_version": policy.version,
            "eligible_ingestion_count": len(eligible),
            "blocked_ingestion_count": len(blocked),
            "page_offset": offset,
            "page_limit": limit,
        },
    )
    return plan


def revoke_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    reason_code: str,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    allowed_reasons = {
        "owner_request",
        "source_replaced",
        "contract_ended",
        "security_concern",
        "other",
    }
    if reason_code not in allowed_reasons:
        raise ApiError(422, "invalid_source_revocation_reason", "Choose a supported reason code.")
    _lock_company(session, tenant_id, access.company.id)
    _lock_source(session, tenant_id, access.company.id, source_id)
    source = _source(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source_id,
    )
    if _source_revoked(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source_id,
    ):
        raise ApiError(409, "universal_source_already_revoked", "This source is already revoked.")
    if (
        session.scalar(
            select(UniversalSourceLifecycleEvent.id).where(
                UniversalSourceLifecycleEvent.tenant_id == tenant_id,
                UniversalSourceLifecycleEvent.company_id == access.company.id,
                UniversalSourceLifecycleEvent.source_id == source_id,
                UniversalSourceLifecycleEvent.event_type == "purged",
            )
        )
        is not None
    ):
        raise ApiError(
            409, "universal_source_already_purged", "This source has already been purged."
        )

    latest_profile = session.scalar(
        select(CompanyDataSourceProfileVersion)
        .where(
            CompanyDataSourceProfileVersion.tenant_id == tenant_id,
            CompanyDataSourceProfileVersion.company_id == access.company.id,
            CompanyDataSourceProfileVersion.source_id == source_id,
        )
        .order_by(CompanyDataSourceProfileVersion.version.desc())
        .limit(1)
    )
    if latest_profile is None:
        raise ApiError(
            409,
            "universal_source_profile_missing",
            "The source profile is incomplete and requires administrator repair.",
        )
    if latest_profile.source_status != "disabled":
        disabled_profile = CompanyDataSourceProfileVersion(
            tenant_id=tenant_id,
            company_id=access.company.id,
            source_id=source_id,
            version=latest_profile.version + 1,
            source_type=latest_profile.source_type,
            display_name=latest_profile.display_name,
            description=latest_profile.description,
            business_domain=latest_profile.business_domain,
            owner_user_id=latest_profile.owner_user_id,
            source_status="disabled",
            connection_reference=latest_profile.connection_reference,
            credential_reference=latest_profile.credential_reference,
            authority_classification=latest_profile.authority_classification,
            freshness_max_age_hours=latest_profile.freshness_max_age_hours,
            created_by_user_id=user.id,
        )
        session.add(disabled_profile)
        session.flush()

    event = UniversalSourceLifecycleEvent(
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source_id,
        event_type="revoked",
        reason_code=reason_code,
        actor_user_id=user.id,
    )
    session.add(event)
    session.flush()
    _audit(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        action="universal_retention.source_revoked",
        target_type="universal_data_source",
        target_id=source.id,
        request_id=request_id,
        details={"reason_code": reason_code},
    )
    return {
        "source_id": str(source.id),
        "revoked": True,
        "reason_code": reason_code,
        "revoked_by_user_id": str(user.id),
        "revoked_at": event.created_at,
    }


def execute_purge(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    ingestion_ids: list[UUID],
    policy_version_id: UUID,
    plan_limit: int = MAX_PLAN_PAGE,
    plan_offset: int = 0,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    """Execute an explicitly selected, database-rechecked plan in the caller transaction."""
    access = _require_retention_access(session, user, tenant_id, request_id=request_id)
    if (
        not 1 <= len(ingestion_ids) <= MAX_PURGE_BATCH
        or len(set(ingestion_ids)) != len(ingestion_ids)
        or not 1 <= plan_limit <= MAX_PLAN_PAGE
        or not 0 <= plan_offset <= 1_000_000
    ):
        raise ApiError(
            422,
            "invalid_purge_batch",
            f"Purge batches require 1 to {MAX_PURGE_BATCH} unique ingestion IDs, "
            f"a plan size from 1 to {MAX_PLAN_PAGE}, and an offset from 0 to 1,000,000.",
        )
    _lock_company(session, tenant_id, access.company.id)
    _lock_source(session, tenant_id, access.company.id, source_id)
    source = _source(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source_id,
    )
    policy = _active_policy(session, tenant_id=tenant_id, company_id=access.company.id)
    if policy is None or policy.id != policy_version_id:
        raise ApiError(
            409,
            "retention_policy_not_active",
            "Purging requires the current explicitly configured and approved policy.",
        )
    plan = build_purge_plan(
        session,
        user,
        tenant_id,
        source_id,
        limit=plan_limit,
        offset=plan_offset,
        request_id=request_id,
    )
    if not plan["purge_enabled"] or not plan["source_revoked"]:
        raise ApiError(
            409, "universal_purge_disabled", "The source or retention policy is not purgeable."
        )
    eligible_ids = {UUID(item["ingestion_id"]) for item in plan["eligible_ingestions"]}
    if set(ingestion_ids) != eligible_ids:
        raise ApiError(
            409,
            "universal_purge_plan_stale",
            "Select exactly the ingestion IDs on the current safe purge-plan page.",
        )

    # The SECURITY DEFINER function verifies these server-established request-scope values.
    # Its SQL is bounded to one Company/source and repeats eligibility/dependency checks.
    session.execute(
        text("SELECT set_config('app.tenant_id', :value, true)"), {"value": str(tenant_id)}
    )
    session.execute(
        text("SELECT set_config('app.company_id', :value, true)"),
        {"value": str(access.company.id)},
    )
    session.execute(text("SELECT set_config('app.user_id', :value, true)"), {"value": str(user.id)})
    try:
        result = (
            session.execute(
                text(
                    "SELECT * FROM businessflow_purge_universal_ingestions("
                    "CAST(:tenant_id AS uuid), CAST(:company_id AS uuid), "
                    "CAST(:source_id AS uuid), CAST(:policy_id AS uuid), "
                    "CAST(:ingestion_ids AS uuid[]), CAST(:actor_id AS uuid))"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "company_id": str(access.company.id),
                    "source_id": str(source_id),
                    "policy_id": str(policy_version_id),
                    "ingestion_ids": [str(item) for item in ingestion_ids],
                    "actor_id": str(user.id),
                },
            )
            .mappings()
            .one()
        )
    except DBAPIError as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate in {"P0001", "23503", "23514", "55000"}:
            raise ApiError(
                409,
                "universal_purge_plan_stale",
                "Purge was blocked because the plan or its dependencies changed; "
                "create a new plan.",
            ) from None
        raise
    outcome = {key: int(value or 0) for key, value in result.items()}
    _audit(
        session,
        tenant_id=tenant_id,
        user_id=user.id,
        action="universal_retention.purge_executed",
        target_type="universal_data_source",
        target_id=source.id,
        request_id=request_id,
        details={
            "policy_version": policy.version,
            "ingestion_count": outcome["purged_ingestion_count"],
            "row_count": outcome["purged_row_count"],
            "validation_count": outcome["purged_validation_count"],
            "event_count": outcome["purged_event_count"],
            "link_count": outcome["purged_link_count"],
            "snapshot_count": outcome["purged_snapshot_count"],
            "snapshot_bytes": outcome["purged_snapshot_bytes"],
        },
    )
    return {
        "source_id": str(source.id),
        "policy_version_id": str(policy.id),
        **outcome,
    }
