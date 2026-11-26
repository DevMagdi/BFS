from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.company_context_routes import _protect_seeded_demo_workspace
from businessflow.api.dependencies import get_current_user, get_session
from businessflow.business_semantic.schemas import (
    SemanticCatalogResponse,
    SemanticEntityCreateRequest,
    SemanticEntityPage,
    SemanticEntityResponse,
    SemanticMappingCreateRequest,
    SemanticMappingPage,
    SemanticMappingResponse,
    SemanticRelationshipCreateRequest,
    SemanticRelationshipPage,
    SemanticRelationshipResponse,
)
from businessflow.business_semantic.service import (
    create_entity,
    create_mapping,
    create_relationship,
    get_catalog,
    list_entities,
    list_mappings,
    list_relationships,
)
from businessflow.models import UserAccount

router = APIRouter(
    prefix="/tenants/{tenant_id}/business-semantic",
    tags=["business semantic layer"],
    dependencies=[Depends(_protect_seeded_demo_workspace)],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


@router.get("/catalog", response_model=SemanticCatalogResponse)
def read_semantic_catalog(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticCatalogResponse:
    return get_catalog(session, user, tenant_id)


@router.get("/entities", response_model=SemanticEntityPage)
def get_semantic_entities(
    tenant_id: UUID,
    entity_type: str | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticEntityPage:
    return list_entities(
        session, user, tenant_id, entity_type=entity_type, limit=limit, offset=offset
    )


@router.post("/entities", response_model=SemanticEntityResponse, status_code=201)
def post_semantic_entity(
    tenant_id: UUID,
    body: SemanticEntityCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticEntityResponse:
    return create_entity(session, user, tenant_id, body, request.state.request_id)


@router.get("/mappings", response_model=SemanticMappingPage)
def get_semantic_mappings(
    tenant_id: UUID,
    semantic_entity_id: UUID | None = None,
    include_history: bool = False,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticMappingPage:
    return list_mappings(
        session,
        user,
        tenant_id,
        semantic_entity_id=semantic_entity_id,
        include_history=include_history,
        limit=limit,
        offset=offset,
    )


@router.post("/mappings", response_model=SemanticMappingResponse, status_code=201)
def post_semantic_mapping(
    tenant_id: UUID,
    body: SemanticMappingCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticMappingResponse:
    return create_mapping(session, user, tenant_id, body, request.state.request_id)


@router.get("/relationships", response_model=SemanticRelationshipPage)
def get_semantic_relationships(
    tenant_id: UUID,
    entity_id: UUID | None = None,
    include_history: bool = False,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticRelationshipPage:
    return list_relationships(
        session,
        user,
        tenant_id,
        entity_id=entity_id,
        include_history=include_history,
        limit=limit,
        offset=offset,
    )


@router.post("/relationships", response_model=SemanticRelationshipResponse, status_code=201)
def post_semantic_relationship(
    tenant_id: UUID,
    body: SemanticRelationshipCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SemanticRelationshipResponse:
    return create_relationship(session, user, tenant_id, body, request.state.request_id)
