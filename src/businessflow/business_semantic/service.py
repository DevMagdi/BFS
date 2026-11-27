from __future__ import annotations

import hashlib
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.authorization import role_allows
from businessflow.business_semantic.catalog import (
    SEMANTIC_ENTITIES,
    SEMANTIC_RELATIONSHIPS,
    entity_catalog_payload,
)
from businessflow.business_semantic.models import (
    BusinessSemanticEntity,
    BusinessSemanticMappingVersion,
    BusinessSemanticRelationshipVersion,
)
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
from businessflow.company_context.models import (
    BusinessUnit,
    Department,
    Employee,
    Project,
    ResourceAssignment,
)
from businessflow.errors import ApiError
from businessflow.models import TenantMembership, UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.universal_data.models import (
    CanonicalBusinessRecord,
    CanonicalBusinessRecordRevision,
)


def _error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _has_workforce_read_access(session: Session, user: UserAccount, tenant_id: UUID) -> bool:
    role = session.scalar(
        select(TenantMembership.role).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
    )
    return role is not None and role_allows(role, "workforce:read")


def _require_workforce_read(session: Session, user: UserAccount, tenant_id: UUID) -> None:
    require_company_context_access(session, user, tenant_id, "workforce:read")


def _has_universal_data_read_access(session: Session, user: UserAccount, tenant_id: UUID) -> bool:
    role = session.scalar(
        select(TenantMembership.role).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
    )
    return role is not None and role_allows(role, "universal_data:read")


def _require_universal_data_read(session: Session, user: UserAccount, tenant_id: UUID) -> None:
    require_company_context_access(session, user, tenant_id, "universal_data:read")


_UNIVERSAL_ENTITY_TYPES = frozenset(
    entity_type
    for entity_type, definition in SEMANTIC_ENTITIES.items()
    if "universal_record" in definition.reference_kinds
)


def _lock(session: Session, key: str) -> None:
    raw = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big", signed=False)
    if raw >= 1 << 63:
        raw -= 1 << 64
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": raw})


def _entity(
    session: Session, tenant_id: UUID, company_id: UUID, entity_id: UUID
) -> BusinessSemanticEntity:
    item = session.scalar(
        select(BusinessSemanticEntity).where(
            BusinessSemanticEntity.tenant_id == tenant_id,
            BusinessSemanticEntity.company_id == company_id,
            BusinessSemanticEntity.id == entity_id,
        )
    )
    if item is None:
        raise _error(404, "semantic_entity_not_found", "The semantic entity was not found.")
    return item


def _entity_response(item: BusinessSemanticEntity) -> SemanticEntityResponse:
    return SemanticEntityResponse(
        id=item.id,
        entity_type=cast(Any, item.entity_type),
        entity_key=item.entity_key,
        display_name=item.display_name,
        description=item.description,
        created_by_user_id=item.created_by_user_id,
        created_at=item.created_at,
    )


def get_catalog(session: Session, user: UserAccount, tenant_id: UUID) -> SemanticCatalogResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    del access
    return SemanticCatalogResponse.model_validate(entity_catalog_payload())


def create_entity(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: SemanticEntityCreateRequest,
    request_id: UUID,
) -> SemanticEntityResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    if body.entity_type not in SEMANTIC_ENTITIES or body.entity_type == "company":
        raise _error(422, "semantic_entity_type_unsupported", "Choose a supported semantic entity.")
    if body.entity_type == "employee":
        _require_workforce_read(session, user, tenant_id)
    _lock(
        session,
        f"business-semantic-entity:{tenant_id}:{access.company.id}:{body.entity_type}:{body.entity_key}",
    )
    existing = session.scalar(
        select(BusinessSemanticEntity.id).where(
            BusinessSemanticEntity.tenant_id == tenant_id,
            BusinessSemanticEntity.company_id == access.company.id,
            BusinessSemanticEntity.entity_type == body.entity_type,
            BusinessSemanticEntity.entity_key == body.entity_key,
        )
    )
    if existing is not None:
        raise _error(
            409,
            "semantic_entity_key_conflict",
            "A semantic identity with this type and key already exists in the Company.",
        )
    item = BusinessSemanticEntity(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        entity_type=body.entity_type,
        entity_key=body.entity_key,
        display_name=body.display_name,
        description=body.description,
        created_by_user_id=user.id,
    )
    session.add(item)
    session.flush()
    record_event(
        session,
        "business_semantic.entity_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_semantic_entity",
        target_id=item.id,
        request_id=request_id,
        details={"entity_type": item.entity_type, "entity_key": item.entity_key},
    )
    return _entity_response(item)


def list_entities(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    entity_type: str | None,
    limit: int,
    offset: int,
) -> SemanticEntityPage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    statement = select(BusinessSemanticEntity).where(
        BusinessSemanticEntity.tenant_id == tenant_id,
        BusinessSemanticEntity.company_id == access.company.id,
    )
    can_read_workforce = _has_workforce_read_access(session, user, tenant_id)
    can_read_universal_data = _has_universal_data_read_access(session, user, tenant_id)
    if entity_type is not None:
        if entity_type not in SEMANTIC_ENTITIES or entity_type == "company":
            raise _error(
                422, "semantic_entity_type_unsupported", "Choose a supported semantic entity."
            )
        if entity_type == "employee" and not can_read_workforce:
            _require_workforce_read(session, user, tenant_id)
        if entity_type in _UNIVERSAL_ENTITY_TYPES and not can_read_universal_data:
            _require_universal_data_read(session, user, tenant_id)
        statement = statement.where(BusinessSemanticEntity.entity_type == entity_type)
    else:
        if not can_read_workforce:
            statement = statement.where(BusinessSemanticEntity.entity_type != "employee")
        if not can_read_universal_data:
            statement = statement.where(
                BusinessSemanticEntity.entity_type.notin_(_UNIVERSAL_ENTITY_TYPES)
            )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = list(
        session.scalars(
            statement.order_by(
                BusinessSemanticEntity.entity_type, BusinessSemanticEntity.entity_key
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return SemanticEntityPage(
        items=[_entity_response(item) for item in items], total=total, limit=limit, offset=offset
    )


def _reference_details(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    reference_kind: str,
    reference_id: UUID,
) -> dict[str, object]:
    if reference_kind == "universal_record":
        record = session.scalar(
            select(CanonicalBusinessRecord)
            .where(
                CanonicalBusinessRecord.tenant_id == tenant_id,
                CanonicalBusinessRecord.company_id == company_id,
                CanonicalBusinessRecord.id == reference_id,
            )
            .with_for_update()
        )
        if record is None:
            raise _error(
                404, "semantic_source_record_not_found", "The source record was not found."
            )
        revision = session.scalar(
            select(CanonicalBusinessRecordRevision).where(
                CanonicalBusinessRecordRevision.tenant_id == tenant_id,
                CanonicalBusinessRecordRevision.company_id == company_id,
                CanonicalBusinessRecordRevision.record_id == record.id,
                CanonicalBusinessRecordRevision.revision == record.current_revision,
            )
        )
        if revision is None:
            raise _error(
                409,
                "semantic_source_record_incomplete",
                "The source record has no current immutable revision.",
            )
        return {
            "entity_type": record.entity_type,
            "reference_key_hash": hashlib.sha256(
                f"universal_record:{record.source_id}:{record.id}".encode("ascii")
            ).hexdigest(),
            "source_id": record.source_id,
            "universal_record_id": record.id,
            "universal_revision": revision.revision,
            "source_mapping_profile_version_id": revision.mapping_profile_version_id,
        }

    reference_models: dict[str, tuple[type[Any], str]] = {
        "employee": (Employee, "employee_id"),
        "department": (Department, "department_id"),
        "business_unit": (BusinessUnit, "business_unit_id"),
        "project": (Project, "project_id"),
    }
    model_and_field = reference_models.get(reference_kind)
    if model_and_field is None:
        raise _error(422, "semantic_reference_type_invalid", "Choose a supported source reference.")
    model, field = model_and_field
    record = session.scalar(
        select(model)
        .where(
            model.tenant_id == tenant_id,
            model.company_id == company_id,
            model.id == reference_id,
        )
        .with_for_update()
    )
    if record is None:
        raise _error(404, "semantic_source_record_not_found", "The source record was not found.")
    return {
        "entity_type": reference_kind,
        "reference_key_hash": hashlib.sha256(
            f"{reference_kind}:{reference_id}".encode("ascii")
        ).hexdigest(),
        field: record.id,
    }


def _mapping_by_hash(
    session: Session, tenant_id: UUID, company_id: UUID, reference_key_hash: str
) -> BusinessSemanticMappingVersion | None:
    return session.scalar(
        select(BusinessSemanticMappingVersion)
        .where(
            BusinessSemanticMappingVersion.tenant_id == tenant_id,
            BusinessSemanticMappingVersion.company_id == company_id,
            BusinessSemanticMappingVersion.reference_key_hash == reference_key_hash,
        )
        .order_by(BusinessSemanticMappingVersion.version.desc())
        .limit(1)
    )


def _reference_id(mapping: BusinessSemanticMappingVersion) -> UUID:
    reference = {
        "universal_record": mapping.universal_record_id,
        "employee": mapping.employee_id,
        "department": mapping.department_id,
        "business_unit": mapping.business_unit_id,
        "project": mapping.project_id,
    }.get(mapping.reference_kind)
    if reference is None:
        raise _error(
            409, "semantic_mapping_integrity_error", "The semantic mapping has no reference."
        )
    return reference


def _mapping_response(
    mapping: BusinessSemanticMappingVersion,
    *,
    entity_type: str,
    is_current: bool,
    source_revision_current: bool | None,
) -> SemanticMappingResponse:
    return SemanticMappingResponse(
        id=mapping.id,
        semantic_entity_id=mapping.semantic_entity_id,
        entity_type=cast(Any, entity_type),
        reference_kind=cast(Any, mapping.reference_kind),
        reference_id=_reference_id(mapping),
        source_id=mapping.source_id,
        source_mapping_profile_version_id=mapping.source_mapping_profile_version_id,
        source_revision=mapping.universal_revision,
        source_revision_current=source_revision_current,
        version=mapping.version,
        supersedes_mapping_version_id=mapping.supersedes_mapping_version_id,
        review_note=mapping.review_note,
        correction_reason=mapping.correction_reason,
        created_by_user_id=mapping.created_by_user_id,
        created_at=mapping.created_at,
        is_current=is_current,
    )


def create_mapping(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: SemanticMappingCreateRequest,
    request_id: UUID,
) -> SemanticMappingResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    if body.reference_kind == "universal_record":
        _require_universal_data_read(session, user, tenant_id)
    details = _reference_details(
        session, tenant_id, access.company.id, body.reference_kind, body.reference_id
    )
    entity = _entity(session, tenant_id, access.company.id, body.semantic_entity_id)
    if entity.entity_type in _UNIVERSAL_ENTITY_TYPES:
        _require_universal_data_read(session, user, tenant_id)
    if details["entity_type"] == "employee" or entity.entity_type == "employee":
        _require_workforce_read(session, user, tenant_id)
    if details["entity_type"] != entity.entity_type:
        raise _error(
            422,
            "semantic_mapping_type_mismatch",
            "The source record type must match the target semantic entity type.",
        )
    reference_key_hash = cast(str, details["reference_key_hash"])
    _lock(
        session, f"business-semantic-mapping:{tenant_id}:{access.company.id}:{reference_key_hash}"
    )
    current = _mapping_by_hash(session, tenant_id, access.company.id, reference_key_hash)
    actual_version = current.version if current is not None else 0
    if body.expected_version != actual_version:
        raise _error(
            409,
            "semantic_mapping_version_conflict",
            f"The source identifier is at version {actual_version}; reload before saving.",
        )
    same_semantic_entity = current is not None and current.semantic_entity_id == entity.id
    same_source_snapshot = current is not None and all(
        getattr(current, field) == details.get(field)
        for field in (
            "source_id",
            "universal_record_id",
            "universal_revision",
            "source_mapping_profile_version_id",
            "employee_id",
            "department_id",
            "business_unit_id",
            "project_id",
        )
    )
    if current is not None and same_semantic_entity and same_source_snapshot:
        return _mapping_response(
            current,
            entity_type=entity.entity_type,
            is_current=True,
            source_revision_current=True if current.reference_kind == "universal_record" else None,
        )
    if current is not None and not same_semantic_entity and not body.correction_reason:
        raise _error(
            409,
            "semantic_mapping_correction_reason_required",
            "Provide a correction reason before changing an existing semantic identity mapping.",
        )
    mapping = BusinessSemanticMappingVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        semantic_entity_id=entity.id,
        reference_kind=body.reference_kind,
        reference_key_hash=reference_key_hash,
        version=actual_version + 1,
        source_id=details.get("source_id"),
        universal_record_id=details.get("universal_record_id"),
        universal_revision=details.get("universal_revision"),
        source_mapping_profile_version_id=details.get("source_mapping_profile_version_id"),
        employee_id=details.get("employee_id"),
        department_id=details.get("department_id"),
        business_unit_id=details.get("business_unit_id"),
        project_id=details.get("project_id"),
        supersedes_mapping_version_id=current.id if current is not None else None,
        review_note=body.review_note,
        correction_reason=body.correction_reason,
        created_by_user_id=user.id,
    )
    session.add(mapping)
    session.flush()
    record_event(
        session,
        "business_semantic.identifier_mapped",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_semantic_mapping",
        target_id=mapping.id,
        request_id=request_id,
        details={
            "entity_type": entity.entity_type,
            "reference_kind": mapping.reference_kind,
            "version": mapping.version,
            "corrected": current is not None and not same_semantic_entity,
            "source_snapshot_refreshed": current is not None and not same_source_snapshot,
        },
    )
    return _mapping_response(
        mapping,
        entity_type=entity.entity_type,
        is_current=True,
        source_revision_current=True if mapping.reference_kind == "universal_record" else None,
    )


def list_mappings(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    semantic_entity_id: UUID | None,
    include_history: bool,
    limit: int,
    offset: int,
) -> SemanticMappingPage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    statement = select(BusinessSemanticMappingVersion).where(
        BusinessSemanticMappingVersion.tenant_id == tenant_id,
        BusinessSemanticMappingVersion.company_id == access.company.id,
    )
    can_read_workforce = _has_workforce_read_access(session, user, tenant_id)
    can_read_universal_data = _has_universal_data_read_access(session, user, tenant_id)
    if semantic_entity_id is not None:
        entity = _entity(session, tenant_id, access.company.id, semantic_entity_id)
        if entity.entity_type == "employee" and not can_read_workforce:
            _require_workforce_read(session, user, tenant_id)
        if entity.entity_type in _UNIVERSAL_ENTITY_TYPES and not can_read_universal_data:
            _require_universal_data_read(session, user, tenant_id)
        statement = statement.where(
            BusinessSemanticMappingVersion.semantic_entity_id == semantic_entity_id
        )
    if not can_read_workforce:
        employee_entity_ids = select(BusinessSemanticEntity.id).where(
            BusinessSemanticEntity.tenant_id == tenant_id,
            BusinessSemanticEntity.company_id == access.company.id,
            BusinessSemanticEntity.entity_type == "employee",
        )
        statement = statement.where(
            BusinessSemanticMappingVersion.reference_kind != "employee",
            ~BusinessSemanticMappingVersion.semantic_entity_id.in_(employee_entity_ids),
        )
    if not can_read_universal_data:
        universal_entity_ids = select(BusinessSemanticEntity.id).where(
            BusinessSemanticEntity.tenant_id == tenant_id,
            BusinessSemanticEntity.company_id == access.company.id,
            BusinessSemanticEntity.entity_type.in_(_UNIVERSAL_ENTITY_TYPES),
        )
        statement = statement.where(
            BusinessSemanticMappingVersion.reference_kind != "universal_record",
            ~BusinessSemanticMappingVersion.semantic_entity_id.in_(universal_entity_ids),
        )
    latest_versions = (
        select(
            BusinessSemanticMappingVersion.tenant_id,
            BusinessSemanticMappingVersion.company_id,
            BusinessSemanticMappingVersion.reference_key_hash,
            func.max(BusinessSemanticMappingVersion.version).label("max_version"),
        )
        .where(
            BusinessSemanticMappingVersion.tenant_id == tenant_id,
            BusinessSemanticMappingVersion.company_id == access.company.id,
        )
        .group_by(
            BusinessSemanticMappingVersion.tenant_id,
            BusinessSemanticMappingVersion.company_id,
            BusinessSemanticMappingVersion.reference_key_hash,
        )
        .subquery()
    )
    if not include_history:
        statement = statement.join(
            latest_versions,
            and_(
                BusinessSemanticMappingVersion.tenant_id == latest_versions.c.tenant_id,
                BusinessSemanticMappingVersion.company_id == latest_versions.c.company_id,
                BusinessSemanticMappingVersion.reference_key_hash
                == latest_versions.c.reference_key_hash,
                BusinessSemanticMappingVersion.version == latest_versions.c.max_version,
            ),
        )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    mappings = list(
        session.scalars(
            statement.order_by(
                BusinessSemanticMappingVersion.reference_key_hash,
                BusinessSemanticMappingVersion.version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    entity_ids = {item.semantic_entity_id for item in mappings}
    entities = {
        item.id: item.entity_type
        for item in session.scalars(
            select(BusinessSemanticEntity).where(
                BusinessSemanticEntity.tenant_id == tenant_id,
                BusinessSemanticEntity.company_id == access.company.id,
                BusinessSemanticEntity.id.in_(entity_ids or {UUID(int=0)}),
            )
        )
    }
    reference_hashes = {item.reference_key_hash for item in mappings}
    universal_record_ids = {
        item.universal_record_id
        for item in mappings
        if item.reference_kind == "universal_record" and item.universal_record_id is not None
    }
    source_revisions = {
        record.id: record.current_revision
        for record in session.scalars(
            select(CanonicalBusinessRecord).where(
                CanonicalBusinessRecord.tenant_id == tenant_id,
                CanonicalBusinessRecord.company_id == access.company.id,
                CanonicalBusinessRecord.id.in_(universal_record_ids or {UUID(int=0)}),
            )
        )
    }
    current_versions = {}
    if reference_hashes:
        current_versions = {
            key: version
            for key, version in session.execute(
                select(
                    BusinessSemanticMappingVersion.reference_key_hash,
                    func.max(BusinessSemanticMappingVersion.version),
                )
                .where(
                    BusinessSemanticMappingVersion.tenant_id == tenant_id,
                    BusinessSemanticMappingVersion.company_id == access.company.id,
                    BusinessSemanticMappingVersion.reference_key_hash.in_(reference_hashes),
                )
                .group_by(BusinessSemanticMappingVersion.reference_key_hash)
            )
        }
    items = []
    for item in mappings:
        entity_type = entities.get(item.semantic_entity_id)
        if entity_type is None:
            raise _error(
                409,
                "semantic_mapping_integrity_error",
                "The semantic mapping references an unavailable Company identity.",
            )
        items.append(
            _mapping_response(
                item,
                entity_type=entity_type,
                is_current=item.version == current_versions.get(item.reference_key_hash),
                source_revision_current=(
                    source_revisions.get(item.universal_record_id) == item.universal_revision
                    if item.reference_kind == "universal_record"
                    and item.universal_record_id is not None
                    else None
                ),
            )
        )
    return SemanticMappingPage(items=items, total=total, limit=limit, offset=offset)


def _mapping_version(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    mapping_id: UUID,
) -> BusinessSemanticMappingVersion:
    mapping = session.scalar(
        select(BusinessSemanticMappingVersion).where(
            BusinessSemanticMappingVersion.tenant_id == tenant_id,
            BusinessSemanticMappingVersion.company_id == company_id,
            BusinessSemanticMappingVersion.id == mapping_id,
        )
    )
    if mapping is None:
        raise _error(
            404, "semantic_mapping_not_found", "The semantic mapping version was not found."
        )
    return mapping


def _verified_relationship(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    relationship_type: str,
    subject: BusinessSemanticEntity,
    object_: BusinessSemanticEntity,
    subject_mapping: BusinessSemanticMappingVersion,
    object_mapping: BusinessSemanticMappingVersion,
    *,
    lock_source: bool,
) -> bool:
    if (
        subject_mapping.semantic_entity_id != subject.id
        or object_mapping.semantic_entity_id != object_.id
    ):
        raise _error(
            422,
            "semantic_relationship_mapping_mismatch",
            "Evidence mappings must resolve to the requested relationship endpoints.",
        )
    if relationship_type in {
        "customer_has_sales_transaction",
        "product_has_sales_transaction",
        "supplier_has_expense",
    }:
        if (
            subject_mapping.reference_kind != "universal_record"
            or object_mapping.reference_kind != "universal_record"
            or subject_mapping.source_id is None
            or object_mapping.source_id is None
            or subject_mapping.universal_record_id is None
            or object_mapping.universal_record_id is None
            or subject_mapping.universal_revision is None
            or object_mapping.universal_revision is None
            or subject_mapping.source_id != object_mapping.source_id
        ):
            raise _error(
                422,
                "semantic_relationship_evidence_incompatible",
                "This relationship requires both records from the same Universal Data source.",
            )
        if lock_source:
            # Universal Data promotions hold this same lock before locking canonical rows.
            _lock(
                session,
                f"universal-source:{tenant_id}:{company_id}:{subject_mapping.source_id}",
            )
        subject_query = select(CanonicalBusinessRecord.current_revision).where(
            CanonicalBusinessRecord.tenant_id == tenant_id,
            CanonicalBusinessRecord.company_id == company_id,
            CanonicalBusinessRecord.id == subject_mapping.universal_record_id,
            CanonicalBusinessRecord.source_id == subject_mapping.source_id,
        )
        object_query = select(CanonicalBusinessRecord.current_revision).where(
            CanonicalBusinessRecord.tenant_id == tenant_id,
            CanonicalBusinessRecord.company_id == company_id,
            CanonicalBusinessRecord.id == object_mapping.universal_record_id,
            CanonicalBusinessRecord.source_id == object_mapping.source_id,
        )
        if lock_source:
            subject_query = subject_query.with_for_update()
            object_query = object_query.with_for_update()
        subject_current_revision = session.scalar(subject_query)
        object_current_revision = session.scalar(object_query)
        if (
            subject_current_revision != subject_mapping.universal_revision
            or object_current_revision != object_mapping.universal_revision
        ):
            raise _error(
                409,
                "semantic_relationship_stale_source_revision",
                "Refresh the reviewed source mappings before verifying a relationship against "
                "changed records.",
            )
        subject_revision = session.scalar(
            select(CanonicalBusinessRecordRevision).where(
                CanonicalBusinessRecordRevision.tenant_id == tenant_id,
                CanonicalBusinessRecordRevision.company_id == company_id,
                CanonicalBusinessRecordRevision.record_id == subject_mapping.universal_record_id,
                CanonicalBusinessRecordRevision.revision == subject_mapping.universal_revision,
            )
        )
        object_revision = session.scalar(
            select(CanonicalBusinessRecordRevision).where(
                CanonicalBusinessRecordRevision.tenant_id == tenant_id,
                CanonicalBusinessRecordRevision.company_id == company_id,
                CanonicalBusinessRecordRevision.record_id == object_mapping.universal_record_id,
                CanonicalBusinessRecordRevision.revision == object_mapping.universal_revision,
            )
        )
        if subject_revision is None or object_revision is None:
            raise _error(
                409,
                "semantic_relationship_evidence_missing",
                "The cited source revisions are unavailable.",
            )
        field_name = {
            "customer_has_sales_transaction": "customer_code",
            "product_has_sales_transaction": "product_code",
            "supplier_has_expense": "supplier_code",
        }[relationship_type]
        left = subject_revision.attributes.get(field_name)
        right = object_revision.attributes.get(field_name)
        if (
            not isinstance(left, str)
            or not left.strip()
            or not isinstance(right, str)
            or left.strip() != right.strip()
        ):
            raise _error(
                409,
                "semantic_relationship_not_supported_by_source",
                "The source records do not contain a matching, non-empty identifier for this "
                "relationship.",
            )
        return True

    if relationship_type == "employee_belongs_to_department":
        if subject_mapping.employee_id is None or object_mapping.department_id is None:
            raise _error(
                422,
                "semantic_relationship_evidence_incompatible",
                "Use Company Context employee and department references.",
            )
        employee_query = select(Employee).where(
            Employee.tenant_id == tenant_id,
            Employee.company_id == company_id,
            Employee.id == subject_mapping.employee_id,
        )
        department_query = select(Department).where(
            Department.tenant_id == tenant_id,
            Department.company_id == company_id,
            Department.id == object_mapping.department_id,
        )
        if lock_source:
            employee_query = employee_query.with_for_update()
            department_query = department_query.with_for_update()
        employee = session.scalar(employee_query)
        department = session.scalar(department_query)
        if employee is None or department is None or employee.department_id != department.id:
            raise _error(
                409,
                "semantic_relationship_not_supported_by_source",
                "The employee's stored department does not match this relationship.",
            )
        return True

    if relationship_type == "department_in_business_unit":
        if subject_mapping.department_id is None or object_mapping.business_unit_id is None:
            raise _error(
                422,
                "semantic_relationship_evidence_incompatible",
                "Use Company Context department and business-unit references.",
            )
        department_query = select(Department).where(
            Department.tenant_id == tenant_id,
            Department.company_id == company_id,
            Department.id == subject_mapping.department_id,
        )
        unit_query = select(BusinessUnit).where(
            BusinessUnit.tenant_id == tenant_id,
            BusinessUnit.company_id == company_id,
            BusinessUnit.id == object_mapping.business_unit_id,
        )
        if lock_source:
            department_query = department_query.with_for_update()
            unit_query = unit_query.with_for_update()
        department = session.scalar(department_query)
        unit = session.scalar(unit_query)
        if department is None or unit is None or department.business_unit_id != unit.id:
            raise _error(
                409,
                "semantic_relationship_not_supported_by_source",
                "The department's stored business unit does not match this relationship.",
            )
        return True

    if relationship_type == "project_assigned_to_employee":
        if subject_mapping.project_id is None or object_mapping.employee_id is None:
            raise _error(
                422,
                "semantic_relationship_evidence_incompatible",
                "Use Company Context project and employee references.",
            )
        assignment_query = (
            select(ResourceAssignment.id)
            .where(
                ResourceAssignment.tenant_id == tenant_id,
                ResourceAssignment.company_id == company_id,
                ResourceAssignment.project_id == subject_mapping.project_id,
                ResourceAssignment.employee_id == object_mapping.employee_id,
                ResourceAssignment.status.in_(("committed", "completed")),
            )
            .limit(1)
        )
        if lock_source:
            assignment_query = assignment_query.with_for_update()
        assignment = session.scalar(assignment_query)
        if assignment is None:
            raise _error(
                409,
                "semantic_relationship_not_supported_by_source",
                "No non-cancelled Company Context assignment supports this relationship.",
            )
        project_query = select(Project).where(
            Project.tenant_id == tenant_id,
            Project.company_id == company_id,
            Project.id == subject_mapping.project_id,
        )
        employee_query = select(Employee).where(
            Employee.tenant_id == tenant_id,
            Employee.company_id == company_id,
            Employee.id == object_mapping.employee_id,
        )
        if lock_source:
            # Match assignment updates' lock order: assignment, project, then employee.
            project_query = project_query.with_for_update()
            employee_query = employee_query.with_for_update()
        project = session.scalar(project_query)
        employee = session.scalar(employee_query)
        if project is None or employee is None:
            raise _error(
                409,
                "semantic_relationship_evidence_missing",
                "The cited Company Context project or employee is unavailable.",
            )
        return True

    if relationship_type == "project_managed_by_employee":
        if subject_mapping.project_id is None or object_mapping.employee_id is None:
            raise _error(
                422,
                "semantic_relationship_evidence_incompatible",
                "Use Company Context project and employee references.",
            )
        project_query = select(Project).where(
            Project.tenant_id == tenant_id,
            Project.company_id == company_id,
            Project.id == subject_mapping.project_id,
        )
        employee_query = select(Employee).where(
            Employee.tenant_id == tenant_id,
            Employee.company_id == company_id,
            Employee.id == object_mapping.employee_id,
        )
        if lock_source:
            project_query = project_query.with_for_update()
            employee_query = employee_query.with_for_update()
        project = session.scalar(project_query)
        employee = session.scalar(employee_query)
        if project is None or employee is None:
            raise _error(
                409,
                "semantic_relationship_evidence_missing",
                "The cited Company Context project or employee is unavailable.",
            )
        if project.project_manager_employee_id != employee.id:
            raise _error(
                409,
                "semantic_relationship_not_supported_by_source",
                "The project's stored manager does not match this relationship.",
            )
        return True

    raise _error(
        422, "semantic_relationship_type_unsupported", "Choose a supported semantic relationship."
    )


def _relationship_key(subject_id: UUID, relationship_type: str, object_id: UUID) -> str:
    return hashlib.sha256(
        f"{subject_id}:{relationship_type}:{object_id}".encode("ascii")
    ).hexdigest()


def create_relationship(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: SemanticRelationshipCreateRequest,
    request_id: UUID,
) -> SemanticRelationshipResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    subject = _entity(session, tenant_id, access.company.id, body.subject_entity_id)
    object_ = _entity(session, tenant_id, access.company.id, body.object_entity_id)
    definition = SEMANTIC_RELATIONSHIPS.get(body.relationship_type)
    if (
        definition is None
        or subject.entity_type != definition.subject_type
        or object_.entity_type != definition.object_type
    ):
        raise _error(
            422,
            "semantic_relationship_type_mismatch",
            "The selected relationship does not accept these entity types.",
        )
    if subject.entity_type == "employee" or object_.entity_type == "employee":
        _require_workforce_read(session, user, tenant_id)
    if (
        subject.entity_type in _UNIVERSAL_ENTITY_TYPES
        or object_.entity_type in _UNIVERSAL_ENTITY_TYPES
    ):
        _require_universal_data_read(session, user, tenant_id)
    subject_mapping = object_mapping = None
    if body.subject_mapping_version_id is not None:
        subject_mapping = _mapping_version(
            session, tenant_id, access.company.id, body.subject_mapping_version_id
        )
        object_mapping = _mapping_version(
            session, tenant_id, access.company.id, cast(UUID, body.object_mapping_version_id)
        )
        if (
            subject_mapping.semantic_entity_id != subject.id
            or object_mapping.semantic_entity_id != object_.id
        ):
            raise _error(
                422,
                "semantic_relationship_mapping_mismatch",
                "Evidence mappings must belong to the selected relationship endpoints.",
            )
    if body.evidence_kind == "source_verified":
        if subject_mapping is None or object_mapping is None:
            raise _error(
                422,
                "semantic_relationship_evidence_required",
                "Source verification requires both mapping versions.",
            )
        if body.status == "active":
            _verified_relationship(
                session,
                tenant_id,
                access.company.id,
                body.relationship_type,
                subject,
                object_,
                subject_mapping,
                object_mapping,
                lock_source=True,
            )
            if not _is_current_mapping(
                session, tenant_id, access.company.id, subject_mapping
            ) or not _is_current_mapping(session, tenant_id, access.company.id, object_mapping):
                raise _error(
                    409,
                    "semantic_relationship_stale_mapping",
                    "Source verification must cite the current reviewed mapping versions.",
                )

    relationship_hash = _relationship_key(subject.id, body.relationship_type, object_.id)
    _lock(
        session,
        f"business-semantic-relationship:{tenant_id}:{access.company.id}:{relationship_hash}",
    )
    current = session.scalar(
        select(BusinessSemanticRelationshipVersion)
        .where(
            BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
            BusinessSemanticRelationshipVersion.company_id == access.company.id,
            BusinessSemanticRelationshipVersion.relationship_key_hash == relationship_hash,
        )
        .order_by(BusinessSemanticRelationshipVersion.version.desc())
        .limit(1)
    )
    actual_version = current.version if current is not None else 0
    if body.expected_version != actual_version:
        raise _error(
            409,
            "semantic_relationship_version_conflict",
            f"The relationship is at version {actual_version}; reload before saving.",
        )
    if current is None and body.status == "retracted":
        raise _error(
            409,
            "semantic_relationship_not_found",
            "A relationship cannot be retracted before it exists.",
        )
    if (
        current is not None
        and current.status == body.status
        and current.evidence_kind == body.evidence_kind
        and current.subject_mapping_version_id == (subject_mapping.id if subject_mapping else None)
        and current.object_mapping_version_id == (object_mapping.id if object_mapping else None)
        and current.evidence_note == body.evidence_note
    ):
        return _relationship_response(
            current,
            is_current=True,
            analytically_usable=_current_evidence_mappings(
                session, tenant_id, access.company.id, current
            ),
        )
    relationship = BusinessSemanticRelationshipVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        relationship_key_hash=relationship_hash,
        subject_entity_id=subject.id,
        relationship_type=body.relationship_type,
        object_entity_id=object_.id,
        version=actual_version + 1,
        status=body.status,
        evidence_kind=body.evidence_kind,
        subject_mapping_version_id=subject_mapping.id if subject_mapping else None,
        object_mapping_version_id=object_mapping.id if object_mapping else None,
        evidence_note=body.evidence_note,
        supersedes_relationship_version_id=current.id if current is not None else None,
        created_by_user_id=user.id,
    )
    session.add(relationship)
    session.flush()
    record_event(
        session,
        "business_semantic.relationship_versioned",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_semantic_relationship",
        target_id=relationship.id,
        request_id=request_id,
        details={
            "relationship_type": relationship.relationship_type,
            "version": relationship.version,
            "status": relationship.status,
            "evidence_kind": relationship.evidence_kind,
        },
    )
    return _relationship_response(
        relationship,
        is_current=True,
        analytically_usable=_current_evidence_mappings(
            session, tenant_id, access.company.id, relationship
        ),
    )


def _is_current_mapping(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    mapping: BusinessSemanticMappingVersion,
) -> bool:
    latest = _mapping_by_hash(session, tenant_id, company_id, mapping.reference_key_hash)
    return latest is not None and latest.id == mapping.id


def _is_current_relationship(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    relationship: BusinessSemanticRelationshipVersion,
) -> bool:
    latest_id = session.scalar(
        select(BusinessSemanticRelationshipVersion.id)
        .where(
            BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
            BusinessSemanticRelationshipVersion.company_id == company_id,
            BusinessSemanticRelationshipVersion.relationship_key_hash
            == relationship.relationship_key_hash,
        )
        .order_by(BusinessSemanticRelationshipVersion.version.desc())
        .limit(1)
    )
    return latest_id == relationship.id


def _current_evidence_mappings(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    relationship: BusinessSemanticRelationshipVersion,
) -> bool:
    if not _is_current_relationship(session, tenant_id, company_id, relationship):
        return False
    definition = SEMANTIC_RELATIONSHIPS.get(relationship.relationship_type)
    if (
        definition is None
        or not definition.analytic_use
        or relationship.status != "active"
        or relationship.evidence_kind != "source_verified"
        or relationship.subject_mapping_version_id is None
        or relationship.object_mapping_version_id is None
    ):
        return False
    found = list(
        session.scalars(
            select(BusinessSemanticMappingVersion).where(
                BusinessSemanticMappingVersion.tenant_id == tenant_id,
                BusinessSemanticMappingVersion.company_id == company_id,
                BusinessSemanticMappingVersion.id.in_(
                    [
                        relationship.subject_mapping_version_id,
                        relationship.object_mapping_version_id,
                    ]
                ),
            )
        )
    )
    if len(found) != 2:
        return False
    mappings_by_id = {item.id: item for item in found}
    subject_mapping = mappings_by_id.get(relationship.subject_mapping_version_id)
    object_mapping = mappings_by_id.get(relationship.object_mapping_version_id)
    if subject_mapping is None or object_mapping is None:
        return False
    if not _is_current_mapping(session, tenant_id, company_id, subject_mapping):
        return False
    if not _is_current_mapping(session, tenant_id, company_id, object_mapping):
        return False
    entities = {
        item.id: item
        for item in session.scalars(
            select(BusinessSemanticEntity).where(
                BusinessSemanticEntity.tenant_id == tenant_id,
                BusinessSemanticEntity.company_id == company_id,
                BusinessSemanticEntity.id.in_(
                    [relationship.subject_entity_id, relationship.object_entity_id]
                ),
            )
        )
    }
    subject = entities.get(relationship.subject_entity_id)
    object_ = entities.get(relationship.object_entity_id)
    if subject is None or object_ is None:
        return False
    if (
        subject.entity_type != definition.subject_type
        or object_.entity_type != definition.object_type
    ):
        return False
    try:
        _verified_relationship(
            session,
            tenant_id,
            company_id,
            relationship.relationship_type,
            subject,
            object_,
            subject_mapping,
            object_mapping,
            lock_source=False,
        )
    except ApiError:
        return False
    return True


def _relationship_response(
    relationship: BusinessSemanticRelationshipVersion,
    *,
    is_current: bool,
    analytically_usable: bool,
) -> SemanticRelationshipResponse:
    return SemanticRelationshipResponse(
        id=relationship.id,
        subject_entity_id=relationship.subject_entity_id,
        relationship_type=cast(Any, relationship.relationship_type),
        object_entity_id=relationship.object_entity_id,
        version=relationship.version,
        status=cast(Any, relationship.status),
        evidence_kind=cast(Any, relationship.evidence_kind),
        subject_mapping_version_id=relationship.subject_mapping_version_id,
        object_mapping_version_id=relationship.object_mapping_version_id,
        evidence_note=relationship.evidence_note,
        supersedes_relationship_version_id=relationship.supersedes_relationship_version_id,
        created_by_user_id=relationship.created_by_user_id,
        created_at=relationship.created_at,
        is_current=is_current,
        analytically_usable=analytically_usable,
    )


def list_relationships(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    entity_id: UUID | None,
    include_history: bool,
    limit: int,
    offset: int,
) -> SemanticRelationshipPage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    statement = select(BusinessSemanticRelationshipVersion).where(
        BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
        BusinessSemanticRelationshipVersion.company_id == access.company.id,
    )
    can_read_workforce = _has_workforce_read_access(session, user, tenant_id)
    can_read_universal_data = _has_universal_data_read_access(session, user, tenant_id)
    if not can_read_workforce:
        employee_entity_ids = select(BusinessSemanticEntity.id).where(
            BusinessSemanticEntity.tenant_id == tenant_id,
            BusinessSemanticEntity.company_id == access.company.id,
            BusinessSemanticEntity.entity_type == "employee",
        )
        statement = statement.where(
            ~BusinessSemanticRelationshipVersion.subject_entity_id.in_(employee_entity_ids),
            ~BusinessSemanticRelationshipVersion.object_entity_id.in_(employee_entity_ids),
        )
    if not can_read_universal_data:
        universal_entity_ids = select(BusinessSemanticEntity.id).where(
            BusinessSemanticEntity.tenant_id == tenant_id,
            BusinessSemanticEntity.company_id == access.company.id,
            BusinessSemanticEntity.entity_type.in_(_UNIVERSAL_ENTITY_TYPES),
        )
        statement = statement.where(
            ~BusinessSemanticRelationshipVersion.subject_entity_id.in_(universal_entity_ids),
            ~BusinessSemanticRelationshipVersion.object_entity_id.in_(universal_entity_ids),
        )
    if not include_history:
        latest_versions = (
            select(
                BusinessSemanticRelationshipVersion.tenant_id,
                BusinessSemanticRelationshipVersion.company_id,
                BusinessSemanticRelationshipVersion.relationship_key_hash,
                func.max(BusinessSemanticRelationshipVersion.version).label("max_version"),
            )
            .where(
                BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
                BusinessSemanticRelationshipVersion.company_id == access.company.id,
            )
            .group_by(
                BusinessSemanticRelationshipVersion.tenant_id,
                BusinessSemanticRelationshipVersion.company_id,
                BusinessSemanticRelationshipVersion.relationship_key_hash,
            )
            .subquery()
        )
        statement = statement.join(
            latest_versions,
            and_(
                BusinessSemanticRelationshipVersion.tenant_id == latest_versions.c.tenant_id,
                BusinessSemanticRelationshipVersion.company_id == latest_versions.c.company_id,
                BusinessSemanticRelationshipVersion.relationship_key_hash
                == latest_versions.c.relationship_key_hash,
                BusinessSemanticRelationshipVersion.version == latest_versions.c.max_version,
            ),
        )
    if entity_id is not None:
        entity = _entity(session, tenant_id, access.company.id, entity_id)
        if entity.entity_type == "employee" and not can_read_workforce:
            _require_workforce_read(session, user, tenant_id)
        if entity.entity_type in _UNIVERSAL_ENTITY_TYPES and not can_read_universal_data:
            _require_universal_data_read(session, user, tenant_id)
        statement = statement.where(
            (BusinessSemanticRelationshipVersion.subject_entity_id == entity_id)
            | (BusinessSemanticRelationshipVersion.object_entity_id == entity_id)
        )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    relationships = list(
        session.scalars(
            statement.order_by(
                BusinessSemanticRelationshipVersion.created_at.desc(),
                BusinessSemanticRelationshipVersion.relationship_key_hash,
                BusinessSemanticRelationshipVersion.version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    relationship_hashes = {item.relationship_key_hash for item in relationships}
    current_versions = {}
    if relationship_hashes:
        current_versions = {
            key: version
            for key, version in session.execute(
                select(
                    BusinessSemanticRelationshipVersion.relationship_key_hash,
                    func.max(BusinessSemanticRelationshipVersion.version),
                )
                .where(
                    BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
                    BusinessSemanticRelationshipVersion.company_id == access.company.id,
                    BusinessSemanticRelationshipVersion.relationship_key_hash.in_(
                        relationship_hashes
                    ),
                )
                .group_by(BusinessSemanticRelationshipVersion.relationship_key_hash)
            )
        }
    items = [
        _relationship_response(
            item,
            is_current=item.version == current_versions.get(item.relationship_key_hash),
            analytically_usable=_current_evidence_mappings(
                session, tenant_id, access.company.id, item
            ),
        )
        for item in relationships
    ]
    return SemanticRelationshipPage(items=items, total=total, limit=limit, offset=offset)
