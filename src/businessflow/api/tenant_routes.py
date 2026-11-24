from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.authorization import require_tenant_access
from businessflow.errors import ApiError
from businessflow.models import AuditEvent, Company, UserAccount
from businessflow.schemas import (
    AuditEventPage,
    AuditEventResponse,
    CompanyProfileUpdate,
    CompanyResponse,
    MembershipCreateRequest,
    MembershipPage,
    MembershipResponse,
    MembershipUpdateRequest,
    TenantCreateRequest,
    TenantListResponse,
    TenantResponse,
    TenantUpdateRequest,
)
from businessflow.services.audit_log import list_audit_events
from businessflow.services.memberships import (
    MembershipRow,
    create_membership,
    list_memberships,
    update_membership,
)
from businessflow.services.tenancy import (
    TenantBundle,
    create_tenant,
    get_tenant_bundle,
    list_user_tenants,
    update_company_profile,
    update_tenant_name,
)

router = APIRouter(prefix="/tenants", tags=["tenants", "memberships", "audit"])


def _tenant_response(bundle: TenantBundle) -> TenantResponse:
    return TenantResponse(
        id=bundle.tenant.id,
        name=bundle.tenant.name,
        status=bundle.tenant.status,
        created_at=bundle.tenant.created_at,
        updated_at=bundle.tenant.updated_at,
        role=bundle.membership.role,
        company=CompanyResponse.model_validate(bundle.company),
    )


def _membership_response(row: MembershipRow) -> MembershipResponse:
    return MembershipResponse(
        id=row.membership.id,
        tenant_id=row.membership.tenant_id,
        user_id=row.user.id,
        email=row.user.email,
        display_name=row.user.display_name,
        role=row.membership.role,
        status=row.membership.status,
        created_at=row.membership.created_at,
        revoked_at=row.membership.revoked_at,
    )


def _audit_response(event: AuditEvent) -> AuditEventResponse:
    if event.tenant_id is None:
        raise ApiError(500, "internal_error", "The request could not be completed.")
    return AuditEventResponse(
        id=event.id,
        tenant_id=event.tenant_id,
        actor_user_id=event.actor_user_id,
        event_type=event.event_type,
        target_type=event.target_type,
        target_id=event.target_id,
        outcome=event.outcome,
        metadata=event.details,
        request_id=event.request_id,
        created_at=event.created_at,
    )


@router.get("", response_model=TenantListResponse)
def list_tenants(
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TenantListResponse:
    return TenantListResponse(
        items=[_tenant_response(item) for item in list_user_tenants(session, user)]
    )


@router.post("", response_model=TenantResponse, status_code=201)
def onboard_tenant(
    body: TenantCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TenantResponse:
    result = create_tenant(
        session,
        user,
        body,
        idempotency_key,
        request.state.request_id,
    )
    if not result.created:
        response.status_code = 200
    return _tenant_response(result)


@router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TenantResponse:
    return _tenant_response(get_tenant_bundle(session, user, tenant_id))


@router.patch("/{tenant_id}", response_model=TenantResponse)
def patch_tenant(
    tenant_id: UUID,
    body: TenantUpdateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TenantResponse:
    update_tenant_name(session, user, tenant_id, body.name, request.state.request_id)
    return _tenant_response(get_tenant_bundle(session, user, tenant_id))


@router.get("/{tenant_id}/company", response_model=CompanyResponse)
def get_company(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Company:
    bundle = get_tenant_bundle(session, user, tenant_id)
    require_tenant_access(session, user, tenant_id, "company:read")
    return bundle.company


@router.patch("/{tenant_id}/company", response_model=CompanyResponse)
def patch_company(
    tenant_id: UUID,
    body: CompanyProfileUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Company:
    return update_company_profile(session, user, tenant_id, body, request.state.request_id)


@router.get("/{tenant_id}/memberships", response_model=MembershipPage)
def get_memberships(
    tenant_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MembershipPage:
    rows, total = list_memberships(session, user, tenant_id, limit, offset)
    return MembershipPage(
        items=[_membership_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post(
    "/{tenant_id}/memberships",
    status_code=503,
    response_model=None,
    responses={503: {"description": "Verified membership invitations are unavailable"}},
)
def add_membership(
    tenant_id: UUID,
    body: MembershipCreateRequest,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    access = require_tenant_access(session, user, tenant_id, "membership:create")
    create_membership(session, access, body)


@router.patch("/{tenant_id}/memberships/{membership_id}", response_model=MembershipResponse)
def patch_membership(
    tenant_id: UUID,
    membership_id: UUID,
    body: MembershipUpdateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MembershipResponse:
    access = require_tenant_access(session, user, tenant_id, "membership:manage")
    row = update_membership(session, access, membership_id, body, request.state.request_id)
    return _membership_response(row)


@router.delete("/{tenant_id}/memberships/{membership_id}", status_code=204)
def revoke_membership(
    tenant_id: UUID,
    membership_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    access = require_tenant_access(session, user, tenant_id, "membership:manage")
    update_membership(
        session,
        access,
        membership_id,
        MembershipUpdateRequest(status="revoked"),
        request.state.request_id,
    )
    return Response(status_code=204)


@router.get("/{tenant_id}/audit-events", response_model=AuditEventPage)
def get_audit_events(
    tenant_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> AuditEventPage:
    events, total = list_audit_events(session, user, tenant_id, limit, offset)
    return AuditEventPage(
        items=[_audit_response(event) for event in events], total=total, limit=limit, offset=offset
    )
