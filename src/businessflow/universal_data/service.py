from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any, TypeVar, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_data.models import CompanyDataContract, CompanyDataSource
from businessflow.data_imports.parsers import ImportFileError, parse_file
from businessflow.errors import ApiError, CommitAndRaise
from businessflow.metrics.snapshot import content_hash
from businessflow.models import TenantMembership, UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.universal_data.catalog import (
    CANONICAL_ENTITIES,
    UNIVERSAL_CONTRACT_KEY,
    UNIVERSAL_CONTRACT_VERSION,
    VALIDATION_RULE_VERSION,
    CanonicalEntity,
    canonical_contract_definition,
)
from businessflow.universal_data.connectors import ConnectorError, LocalDirectoryConnector
from businessflow.universal_data.models import (
    CanonicalBusinessRecord,
    CanonicalBusinessRecordRevision,
    CompanyDataSourceProfileVersion,
    UniversalIngestion,
    UniversalIngestionEvent,
    UniversalIngestionRecordLink,
    UniversalIngestionRow,
    UniversalMappingProfile,
    UniversalMappingProfileVersion,
    UniversalRowValidation,
    UniversalSourceSnapshot,
)
from businessflow.universal_data.normalization import (
    NormalizationError,
    normalize_field,
    safe_file_name,
)
from businessflow.universal_data.schema_detection import inspect_parsed_file
from businessflow.universal_data.schemas import (
    CanonicalRecordPage,
    CanonicalRecordSummary,
    UniversalEntityCatalog,
    UniversalEntitySchema,
    UniversalFileIngestionResponse,
    UniversalIngestionCounts,
    UniversalIngestionDetail,
    UniversalIngestionPage,
    UniversalIngestionSummary,
    UniversalLineageItem,
    UniversalLineagePage,
    UniversalMappingCreateRequest,
    UniversalMappingProfileList,
    UniversalMappingProfileVersionResponse,
    UniversalPromotionRequest,
    UniversalPromotionResult,
    UniversalRowPage,
    UniversalRowPreview,
    UniversalSourceCreateRequest,
    UniversalSourcePage,
    UniversalSourceProfileHistory,
    UniversalSourceProfileSummary,
    UniversalSourceProfileUpdateRequest,
    UniversalValidationResult,
)

_UNIVERSAL_SOURCE_TYPE = "universal_records"
_UNIVERSAL_FILE_CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
UNIVERSAL_QUERY_BATCH_SIZE = 5_000
_T = TypeVar("_T")


def _api_error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _advisory_lock(session: Session, key: str) -> None:
    value = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big", signed=False)
    if value >= 1 << 63:
        value -= 1 << 64
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": value})


def _batches(values: Iterable[_T], *, size: int = UNIVERSAL_QUERY_BATCH_SIZE) -> Iterator[list[_T]]:
    if size < 1:
        raise ValueError("batch size must be positive")
    batch: list[_T] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _require_company_access(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    write: bool,
    request_id: UUID | None = None,
):
    permission = "company_context:write" if write else "company_context:read"
    return require_company_context_access(
        session, user, tenant_id, permission, request_id=request_id
    )


def _company_source(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID, *, lock: bool = False
) -> CompanyDataSource:
    statement = select(CompanyDataSource).where(
        CompanyDataSource.id == source_id,
        CompanyDataSource.tenant_id == tenant_id,
        CompanyDataSource.company_id == company_id,
        CompanyDataSource.dataset_type == _UNIVERSAL_SOURCE_TYPE,
    )
    if lock:
        # M12's source registry is INSERT-only for bf_runtime, so a row lock would require
        # an UPDATE grant on an append-only table. Serialize Universal writes with an
        # advisory lock instead; the CompanyDataSource row itself remains immutable.
        _advisory_lock(session, f"universal-source:{tenant_id}:{company_id}:{source_id}")
    source = session.scalar(statement)
    if source is None:
        raise _api_error(
            404, "universal_source_not_found", "The Universal Data source was not found."
        )
    return source


def _latest_source_profile(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> CompanyDataSourceProfileVersion:
    statement = (
        select(CompanyDataSourceProfileVersion)
        .where(
            CompanyDataSourceProfileVersion.tenant_id == tenant_id,
            CompanyDataSourceProfileVersion.company_id == company_id,
            CompanyDataSourceProfileVersion.source_id == source_id,
        )
        .order_by(CompanyDataSourceProfileVersion.version.desc())
        .limit(1)
    )
    # Source-level advisory locking serializes profile version creation without requiring
    # UPDATE privileges on this append-only table.
    profile = session.scalar(statement)
    if profile is None:
        raise _api_error(
            409,
            "universal_source_profile_missing",
            "The Universal Data source profile is incomplete and requires administrator repair.",
        )
    return profile


def _source_summary(
    session: Session,
    source: CompanyDataSource,
    profile: CompanyDataSourceProfileVersion,
) -> UniversalSourceProfileSummary:
    latest = session.scalar(
        select(UniversalIngestion)
        .where(
            UniversalIngestion.tenant_id == source.tenant_id,
            UniversalIngestion.company_id == source.company_id,
            UniversalIngestion.source_id == source.id,
        )
        .order_by(UniversalIngestion.created_at.desc(), UniversalIngestion.id.desc())
        .limit(1)
    )
    last_successful_at = session.scalar(
        select(func.max(UniversalIngestion.promoted_at)).where(
            UniversalIngestion.tenant_id == source.tenant_id,
            UniversalIngestion.company_id == source.company_id,
            UniversalIngestion.source_id == source.id,
        )
    )
    return UniversalSourceProfileSummary(
        source_id=source.id,
        source_key=source.source_key,
        dataset_type=cast(Any, source.dataset_type),
        contract_key=source.contract_key,
        contract_version=source.contract_version,
        contract_hash=content_hash(canonical_contract_definition()),
        version=profile.version,
        source_type=cast(Any, profile.source_type),
        display_name=profile.display_name,
        description=profile.description,
        business_domain=profile.business_domain,
        owner_user_id=profile.owner_user_id,
        source_status=cast(Any, profile.source_status),
        connection_reference_configured=profile.connection_reference is not None,
        credential_reference_configured=profile.credential_reference is not None,
        authority_classification=cast(Any, profile.authority_classification),
        freshness_max_age_hours=profile.freshness_max_age_hours,
        created_by_user_id=profile.created_by_user_id,
        created_at=profile.created_at,
        last_attempted_at=latest.created_at if latest else None,
        last_successful_at=last_successful_at,
        last_error_code=latest.failure_code if latest and latest.status == "failed" else None,
    )


def _contract(session: Session) -> CompanyDataContract:
    contract = session.scalar(
        select(CompanyDataContract).where(
            CompanyDataContract.contract_key == UNIVERSAL_CONTRACT_KEY,
            CompanyDataContract.version == UNIVERSAL_CONTRACT_VERSION,
            CompanyDataContract.dataset_type == _UNIVERSAL_SOURCE_TYPE,
        )
    )
    if contract is None or content_hash(contract.definition) != contract.definition_hash:
        raise _api_error(
            409,
            "universal_contract_integrity_error",
            "The published Universal Data contract is unavailable or failed its integrity check.",
        )
    if content_hash(canonical_contract_definition()) != contract.definition_hash:
        raise _api_error(
            409,
            "universal_contract_integrity_error",
            "The running Universal Data schema does not match its published contract.",
        )
    return contract


def _source_profile_values(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: UniversalSourceCreateRequest | UniversalSourceProfileUpdateRequest,
    *,
    previous: CompanyDataSourceProfileVersion | None = None,
) -> dict[str, object]:
    if previous is None:
        values: dict[str, object] = {
            "source_type": body.source_type or "csv",
            "display_name": body.display_name or "",
            "description": body.description,
            "business_domain": body.business_domain,
            "owner_user_id": body.owner_user_id or user.id,
            "source_status": body.source_status or "enabled",
            "connection_reference": body.connection_reference,
            "credential_reference": body.credential_reference,
            "authority_classification": body.authority_classification or "unclassified",
            "freshness_max_age_hours": body.freshness_max_age_hours,
        }
    else:
        values = {
            field: getattr(previous, field)
            for field in (
                "source_type",
                "display_name",
                "description",
                "business_domain",
                "owner_user_id",
                "source_status",
                "connection_reference",
                "credential_reference",
                "authority_classification",
                "freshness_max_age_hours",
            )
        }
        supplied = body.model_dump(exclude_unset=True)
        required = {
            "source_type",
            "display_name",
            "owner_user_id",
            "source_status",
            "authority_classification",
        }
        for field, value in supplied.items():
            if field in required and value is None:
                raise _api_error(422, "invalid_source_profile", f"{field} cannot be cleared.")
            values[field] = value
    if (
        values["source_type"] == "local_directory"
        and values["connection_reference"] != "config://universal/local-directory"
    ):
        raise _api_error(
            422,
            "invalid_connector_reference",
            "Local directory sources must use the configured local connector reference.",
        )
    owner_user_id = cast(UUID, values["owner_user_id"])
    owner_membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == owner_user_id,
            TenantMembership.status == "active",
        )
    )
    if owner_membership is None:
        raise _api_error(
            404,
            "universal_source_owner_not_found",
            "The selected source owner is not an active tenant member.",
        )
    return values


def create_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: UniversalSourceCreateRequest,
    request_id: UUID,
) -> UniversalSourceProfileSummary:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    existing = session.scalar(
        select(CompanyDataSource.id).where(
            CompanyDataSource.tenant_id == tenant_id,
            CompanyDataSource.company_id == access.company.id,
            CompanyDataSource.source_key == body.source_key,
        )
    )
    if existing is not None:
        raise _api_error(
            409, "universal_source_key_conflict", "A source with this key already exists."
        )
    contract = _contract(session)
    profile_values = _source_profile_values(session, user, tenant_id, body)
    source = CompanyDataSource(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_key=body.source_key,
        display_name=cast(str, profile_values["display_name"]),
        dataset_type=_UNIVERSAL_SOURCE_TYPE,
        contract_key=contract.contract_key,
        contract_version=contract.version,
        owner_user_id=cast(UUID, profile_values["owner_user_id"]),
        registered_by_user_id=user.id,
        freshness_max_age_hours=cast(int | None, profile_values["freshness_max_age_hours"]),
    )
    profile = CompanyDataSourceProfileVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source.id,
        version=1,
        created_by_user_id=user.id,
        **profile_values,
    )
    session.add_all([source, profile])
    session.flush()
    record_event(
        session,
        "universal_data.source_registered",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="company_data_source",
        target_id=source.id,
        request_id=request_id,
        details={
            "source_key": source.source_key,
            "source_type": profile.source_type,
            "profile_version": profile.version,
            "connection_reference_configured": profile.connection_reference is not None,
            "credential_reference_configured": profile.credential_reference is not None,
            "authority_classification": profile.authority_classification,
        },
    )
    return _source_summary(session, source, profile)


def update_source_profile(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    body: UniversalSourceProfileUpdateRequest,
    request_id: UUID,
) -> UniversalSourceProfileSummary:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id, lock=True)
    previous = _latest_source_profile(session, tenant_id, access.company.id, source.id)
    profile_values = _source_profile_values(session, user, tenant_id, body, previous=previous)
    profile = CompanyDataSourceProfileVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=source.company_id,
        source_id=source.id,
        version=previous.version + 1,
        created_by_user_id=user.id,
        **profile_values,
    )
    session.add(profile)
    session.flush()
    record_event(
        session,
        "universal_data.source_profile_versioned",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="company_data_source",
        target_id=source.id,
        request_id=request_id,
        details={
            "previous_version": previous.version,
            "version": profile.version,
            "source_type": profile.source_type,
            "source_status": profile.source_status,
            "connection_reference_configured": profile.connection_reference is not None,
            "credential_reference_configured": profile.credential_reference is not None,
        },
    )
    return _source_summary(session, source, profile)


def list_sources(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> UniversalSourcePage:
    access = _require_company_access(session, user, tenant_id, write=False)
    base = select(CompanyDataSource).where(
        CompanyDataSource.tenant_id == tenant_id,
        CompanyDataSource.company_id == access.company.id,
        CompanyDataSource.dataset_type == _UNIVERSAL_SOURCE_TYPE,
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    sources = list(
        session.scalars(
            base.order_by(CompanyDataSource.created_at.desc(), CompanyDataSource.id)
            .limit(limit)
            .offset(offset)
        )
    )
    items = []
    for source in sources:
        profile = _latest_source_profile(session, tenant_id, access.company.id, source.id)
        items.append(_source_summary(session, source, profile))
    return UniversalSourcePage(items=items, total=total, limit=limit, offset=offset)


def get_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
) -> UniversalSourceProfileSummary:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    profile = _latest_source_profile(session, tenant_id, access.company.id, source.id)
    return _source_summary(session, source, profile)


def list_source_profile_history(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> UniversalSourceProfileHistory:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    base = select(CompanyDataSourceProfileVersion).where(
        CompanyDataSourceProfileVersion.tenant_id == tenant_id,
        CompanyDataSourceProfileVersion.company_id == access.company.id,
        CompanyDataSourceProfileVersion.source_id == source.id,
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    profiles = list(
        session.scalars(
            base.order_by(CompanyDataSourceProfileVersion.version.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return UniversalSourceProfileHistory(
        items=[_source_summary(session, source, profile) for profile in profiles],
        total=total,
        limit=limit,
        offset=offset,
    )


def list_entity_catalog(
    session: Session, user: UserAccount, tenant_id: UUID
) -> UniversalEntityCatalog:
    _require_company_access(session, user, tenant_id, write=False)
    _contract(session)
    definition = canonical_contract_definition()
    entities = cast(list[dict[str, object]], definition["entities"])
    items = [UniversalEntitySchema.model_validate(item) for item in entities]
    return UniversalEntityCatalog(
        contract_key=UNIVERSAL_CONTRACT_KEY,
        contract_version=UNIVERSAL_CONTRACT_VERSION,
        contract_hash=content_hash(canonical_contract_definition()),
        items=items,
        note=(
            "Canonical records are source-scoped; they do not prove authority, "
            "completeness, accounting correctness, or Core Financials eligibility."
        ),
    )


def _append_event(
    session: Session,
    ingestion: UniversalIngestion,
    actor_user_id: UUID,
    event_type: str,
    details: dict[str, object],
    *,
    from_status: str | None = None,
    to_status: str | None = None,
) -> UniversalIngestionEvent:
    event_id = uuid4()
    event_payload = {
        "id": str(event_id),
        "tenant_id": str(ingestion.tenant_id),
        "company_id": str(ingestion.company_id),
        "ingestion_id": str(ingestion.id),
        "actor_user_id": str(actor_user_id),
        "event_type": event_type,
        "from_status": from_status,
        "to_status": to_status,
        "details": details,
    }
    event = UniversalIngestionEvent(
        id=event_id,
        tenant_id=ingestion.tenant_id,
        company_id=ingestion.company_id,
        ingestion_id=ingestion.id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        details=details,
        event_hash=content_hash(event_payload),
    )
    session.add(event)
    return event


def _ingestion_counts(ingestion: UniversalIngestion) -> UniversalIngestionCounts:
    return UniversalIngestionCounts(
        total_rows=ingestion.total_rows,
        valid_rows=ingestion.valid_rows,
        invalid_rows=ingestion.invalid_rows,
        warning_rows=ingestion.warning_rows,
        duplicate_rows=ingestion.duplicate_rows,
        promoted_rows=ingestion.promoted_rows,
        created_records=ingestion.created_records,
        revised_records=ingestion.revised_records,
        unchanged_records=ingestion.unchanged_records,
    )


def _ingestion_summary(
    session: Session,
    ingestion: UniversalIngestion,
) -> UniversalIngestionSummary:
    mapping_version = None
    mapping_profile = None
    entity_type = None
    if ingestion.mapping_profile_version_id is not None:
        mapping_version = session.scalar(
            select(UniversalMappingProfileVersion).where(
                UniversalMappingProfileVersion.tenant_id == ingestion.tenant_id,
                UniversalMappingProfileVersion.company_id == ingestion.company_id,
                UniversalMappingProfileVersion.source_id == ingestion.source_id,
                UniversalMappingProfileVersion.id == ingestion.mapping_profile_version_id,
            )
        )
        if mapping_version is not None:
            entity_type = mapping_version.entity_type
            mapping_profile = session.scalar(
                select(UniversalMappingProfile).where(
                    UniversalMappingProfile.tenant_id == ingestion.tenant_id,
                    UniversalMappingProfile.company_id == ingestion.company_id,
                    UniversalMappingProfile.source_id == ingestion.source_id,
                    UniversalMappingProfile.id == mapping_version.profile_id,
                )
            )
    return UniversalIngestionSummary(
        id=ingestion.id,
        source_id=ingestion.source_id,
        snapshot_id=ingestion.snapshot_id,
        source_profile_version_id=ingestion.source_profile_version_id,
        initiated_by_user_id=ingestion.initiated_by_user_id,
        promoted_by_user_id=ingestion.promoted_by_user_id,
        file_name=ingestion.file_name,
        file_format=cast(Any, ingestion.file_format),
        input_fingerprint=ingestion.input_fingerprint,
        selected_worksheet=ingestion.selected_worksheet,
        mapping_profile_id=mapping_version.profile_id if mapping_version else None,
        mapping_profile_version_id=mapping_version.id if mapping_version else None,
        mapping_profile_name=mapping_profile.display_name if mapping_profile else None,
        mapping_version=mapping_version.version if mapping_version else None,
        entity_type=cast(Any, entity_type) if entity_type else None,
        status=cast(Any, ingestion.status),
        counts=_ingestion_counts(ingestion),
        validation_hash=ingestion.validation_hash,
        failure_code=ingestion.failure_code,
        created_at=ingestion.created_at,
        updated_at=ingestion.updated_at,
        promoted_at=ingestion.promoted_at,
    )


def _ingestion_response(
    session: Session, ingestion: UniversalIngestion, *, replayed: bool = False
) -> UniversalFileIngestionResponse:
    return UniversalFileIngestionResponse(
        ingestion=_ingestion_summary(session, ingestion),
        inspection=ingestion.inspection,
        replayed=replayed,
    )


def _require_enabled_profile(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> CompanyDataSourceProfileVersion:
    profile = _latest_source_profile(session, tenant_id, company_id, source_id)
    if profile.source_status != "enabled":
        raise _api_error(
            409, "universal_source_disabled", "This source is disabled for new ingestions."
        )
    return profile


def stage_file_ingestion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    file_name: str | None,
    content: bytes,
    idempotency_key: str,
    max_rows: int,
    max_file_bytes: int,
    csv_delimiter: str = ",",
    csv_encoding: str = "utf-8-sig",
    header_row: int = 1,
    request_id: UUID,
) -> UniversalFileIngestionResponse:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    profile = _require_enabled_profile(session, tenant_id, access.company.id, source.id)
    if not content or len(content) > max_file_bytes:
        status = 413 if len(content) > max_file_bytes else 422
        code = "file_too_large" if status == 413 else "malformed_file"
        message = (
            "The upload exceeds the configured size limit."
            if status == 413
            else "The selected file is empty."
        )
        raise _api_error(status, code, message)
    if (
        not idempotency_key
        or not 16 <= len(idempotency_key) <= 200
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in idempotency_key)
    ):
        raise _api_error(
            422, "invalid_idempotency_key", "Use a printable 16- to 200-character idempotency key."
        )
    try:
        normalized_file_name = safe_file_name(file_name)
    except ValueError as exc:
        raise _api_error(422, "invalid_file_name", str(exc)) from None
    file_format = normalized_file_name.rsplit(".", 1)[-1].casefold()
    input_fingerprint = hashlib.sha256(content).hexdigest()
    parse_options: dict[str, object] = {
        "csv_delimiter": csv_delimiter,
        "csv_encoding": csv_encoding,
        "header_row": header_row,
    }
    idempotency_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    _advisory_lock(
        session,
        f"universal-idempotency:{tenant_id}:{access.company.id}:{source.id}:{idempotency_hash}",
    )
    existing = session.scalar(
        select(UniversalIngestion).where(
            UniversalIngestion.tenant_id == tenant_id,
            UniversalIngestion.company_id == access.company.id,
            UniversalIngestion.source_id == source.id,
            UniversalIngestion.idempotency_key_hash == idempotency_hash,
        )
    )
    if existing is not None:
        if (
            existing.input_fingerprint != input_fingerprint
            or existing.file_name != normalized_file_name
            or existing.file_format != file_format
            or existing.parse_options != parse_options
        ):
            raise _api_error(
                409,
                "idempotency_key_reused",
                "This idempotency key was already used for a different file name, format, "
                "content, or parser configuration.",
            )
        return _ingestion_response(session, existing, replayed=True)

    try:
        parsed = parse_file(
            normalized_file_name,
            content,
            max_rows,
            csv_delimiter=csv_delimiter,
            csv_encoding=csv_encoding,
            header_row=header_row,
        )
    except ImportFileError as exc:
        raise _api_error(exc.status_code, exc.code, exc.message) from None
    except ValueError:
        raise _api_error(
            422, "invalid_parser_options", "The selected parser options are invalid."
        ) from None
    if parsed.total_rows == 0:
        raise _api_error(422, "empty_dataset", "The selected file contains no non-empty data rows.")

    contract = _contract(session)
    inspection = inspect_parsed_file(parsed, header_row=header_row)
    inspection["parse_options"] = parse_options
    snapshot = session.scalar(
        select(UniversalSourceSnapshot).where(
            UniversalSourceSnapshot.tenant_id == tenant_id,
            UniversalSourceSnapshot.company_id == access.company.id,
            UniversalSourceSnapshot.source_id == source.id,
            UniversalSourceSnapshot.input_fingerprint == input_fingerprint,
            UniversalSourceSnapshot.file_format == file_format,
        )
    )
    if snapshot is None:
        snapshot = UniversalSourceSnapshot(
            id=uuid4(),
            tenant_id=tenant_id,
            company_id=access.company.id,
            source_id=source.id,
            input_fingerprint=input_fingerprint,
            file_name=normalized_file_name,
            file_format=file_format,
            # Never persist a client-declared MIME type; derive it from the accepted format.
            content_type=_UNIVERSAL_FILE_CONTENT_TYPES[file_format],
            file_size_bytes=len(content),
            original_content=content,
            created_by_user_id=user.id,
        )
        session.add(snapshot)
        session.flush()
    elif hashlib.sha256(snapshot.original_content).hexdigest() != snapshot.input_fingerprint:
        raise _api_error(
            409,
            "source_snapshot_integrity_error",
            "The stored source evidence failed its content-fingerprint check.",
        )

    ingestion = UniversalIngestion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source.id,
        snapshot_id=snapshot.id,
        source_profile_version_id=profile.id,
        initiated_by_user_id=user.id,
        idempotency_key_hash=idempotency_hash,
        file_name=normalized_file_name,
        file_format=file_format,
        input_fingerprint=input_fingerprint,
        parse_options=parse_options,
        inspection=inspection,
        status="staged",
        total_rows=parsed.total_rows,
    )
    session.add(ingestion)
    session.flush()
    for sheet in parsed.worksheets:
        for row in sheet.rows:
            raw_values = list(row.values)
            session.add(
                UniversalIngestionRow(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    company_id=access.company.id,
                    source_id=source.id,
                    snapshot_id=snapshot.id,
                    ingestion_id=ingestion.id,
                    worksheet_name=sheet.name,
                    row_number=row.row_number,
                    row_hash=content_hash({"headers": sheet.headers, "values": raw_values}),
                    raw_values=raw_values,
                )
            )
    _append_event(
        session,
        ingestion,
        user.id,
        "uploaded",
        {
            "file_format": file_format,
            "input_fingerprint": input_fingerprint,
            "file_size_bytes": len(content),
            "source_profile_version_id": str(profile.id),
            "contract_hash": contract.definition_hash,
            "worksheet_count": len(parsed.worksheets),
            "row_count": parsed.total_rows,
        },
        to_status="staged",
    )
    record_event(
        session,
        "universal_data.file_staged",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="universal_ingestion",
        target_id=ingestion.id,
        request_id=request_id,
        details={
            "source_id": str(source.id),
            "file_format": file_format,
            "input_fingerprint": input_fingerprint,
            "row_count": parsed.total_rows,
        },
    )
    return _ingestion_response(session, ingestion)


def stage_connector_ingestion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    relative_path: str,
    idempotency_key: str,
    local_root: str | None,
    max_rows: int,
    max_file_bytes: int,
    csv_delimiter: str = ",",
    csv_encoding: str = "utf-8-sig",
    header_row: int = 1,
    request_id: UUID,
) -> UniversalFileIngestionResponse:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    profile = _require_enabled_profile(session, tenant_id, access.company.id, source.id)
    if (
        profile.source_type != "local_directory"
        or profile.connection_reference != "config://universal/local-directory"
    ):
        raise _api_error(
            409,
            "universal_connector_not_configured",
            "This source is not registered with the local directory connector.",
        )
    if not local_root:
        raise _api_error(
            503,
            "universal_connector_not_configured",
            "The operator has not configured the local connector root directory.",
        )
    try:
        connector = LocalDirectoryConnector(
            local_root, company_scope_id=access.company.id, max_file_bytes=max_file_bytes
        )
        connector_file = connector.read_file(relative_path)
    except ConnectorError as exc:
        raise _api_error(exc.status_code, exc.code, exc.message) from None
    return stage_file_ingestion(
        session,
        user,
        tenant_id,
        source_id,
        file_name=connector_file.file_name,
        content=connector_file.content,
        idempotency_key=idempotency_key,
        max_rows=max_rows,
        max_file_bytes=max_file_bytes,
        csv_delimiter=csv_delimiter,
        csv_encoding=csv_encoding,
        header_row=header_row,
        request_id=request_id,
    )


def _ingestion(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    *,
    lock: bool = False,
) -> UniversalIngestion:
    statement = select(UniversalIngestion).where(
        UniversalIngestion.tenant_id == tenant_id,
        UniversalIngestion.company_id == company_id,
        UniversalIngestion.source_id == source_id,
        UniversalIngestion.id == ingestion_id,
    )
    if lock:
        statement = statement.with_for_update()
    ingestion = session.scalar(statement)
    if ingestion is None:
        raise _api_error(
            404, "universal_ingestion_not_found", "The Universal Data ingestion was not found."
        )
    return ingestion


def _inspection_worksheets(ingestion: UniversalIngestion) -> list[dict[str, Any]]:
    raw = ingestion.inspection.get("worksheets", [])
    if not isinstance(raw, list):
        raise _api_error(
            409, "ingestion_inspection_invalid", "The file inspection record is invalid."
        )
    return [item for item in raw if isinstance(item, dict)]


def _mapping_headers(ingestion: UniversalIngestion, worksheet_name: str) -> list[str]:
    for worksheet in _inspection_worksheets(ingestion):
        if worksheet.get("name") == worksheet_name:
            headers = worksheet.get("headers")
            if isinstance(headers, list) and all(isinstance(header, str) for header in headers):
                return cast(list[str], headers)
    raise _api_error(422, "worksheet_not_found", "Select a worksheet present in the uploaded file.")


def _mapping_profile_version(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    version_id: UUID,
) -> UniversalMappingProfileVersion:
    version = session.scalar(
        select(UniversalMappingProfileVersion).where(
            UniversalMappingProfileVersion.tenant_id == tenant_id,
            UniversalMappingProfileVersion.company_id == company_id,
            UniversalMappingProfileVersion.source_id == source_id,
            UniversalMappingProfileVersion.id == version_id,
        )
    )
    if version is None:
        raise _api_error(
            404, "universal_mapping_not_found", "The source mapping profile version was not found."
        )
    return version


def _mapping_response(
    session: Session,
    version: UniversalMappingProfileVersion,
) -> UniversalMappingProfileVersionResponse:
    profile = session.scalar(
        select(UniversalMappingProfile).where(
            UniversalMappingProfile.tenant_id == version.tenant_id,
            UniversalMappingProfile.company_id == version.company_id,
            UniversalMappingProfile.source_id == version.source_id,
            UniversalMappingProfile.id == version.profile_id,
        )
    )
    if profile is None:
        raise _api_error(
            409, "universal_mapping_profile_missing", "The mapping profile is incomplete."
        )
    entity = CANONICAL_ENTITIES.get(version.entity_type)
    if entity is None:
        raise _api_error(
            409, "universal_mapping_entity_invalid", "The mapping entity is not supported."
        )
    missing = [field for field in entity.required_fields if field not in version.field_mapping]
    return UniversalMappingProfileVersionResponse(
        id=version.id,
        profile_id=profile.id,
        profile_key=profile.profile_key,
        display_name=profile.display_name,
        version=version.version,
        entity_type=cast(Any, version.entity_type),
        worksheet_name=version.worksheet_name,
        field_mapping=version.field_mapping,
        ignored_columns=version.ignored_columns,
        field_options=version.field_options,
        missing_required_fields=missing,
        contract_hash=version.contract_hash,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
    )


def _validate_mapping_against_ingestion(
    ingestion: UniversalIngestion,
    version: UniversalMappingProfileVersion,
) -> list[str]:
    headers = _mapping_headers(ingestion, version.worksheet_name)
    if version.contract_hash != content_hash(canonical_contract_definition()):
        raise _api_error(
            409,
            "universal_mapping_contract_mismatch",
            "The mapping uses a different canonical contract version.",
        )
    entity = CANONICAL_ENTITIES.get(version.entity_type)
    if entity is None:
        raise _api_error(
            422,
            "universal_mapping_entity_invalid",
            "The selected canonical entity is not supported.",
        )
    if not version.field_mapping:
        raise _api_error(
            422, "universal_mapping_empty", "Map at least one source column to a canonical field."
        )
    unknown_fields = set(version.field_mapping) - set(entity.field_by_name)
    unknown_columns = set(version.field_mapping.values()) - set(headers)
    if unknown_fields or unknown_columns:
        raise _api_error(
            422,
            "universal_mapping_invalid",
            "The mapping references an unknown field or source column.",
        )
    if len(set(version.field_mapping.values())) != len(version.field_mapping):
        raise _api_error(
            422,
            "universal_mapping_duplicate_column",
            "A source column may map to only one canonical field.",
        )
    ignored = set(version.ignored_columns)
    if ignored - set(headers):
        raise _api_error(
            422,
            "universal_mapping_invalid",
            "An ignored column is not present in the selected worksheet.",
        )
    if set(version.field_mapping.values()).intersection(ignored):
        raise _api_error(
            422, "universal_mapping_invalid", "A source column cannot be mapped and ignored."
        )
    if set(version.field_mapping.values()) | ignored != set(headers):
        raise _api_error(
            422, "universal_mapping_incomplete", "Map or explicitly ignore every source column."
        )
    if set(version.field_options) - set(version.field_mapping):
        raise _api_error(
            422,
            "universal_mapping_options_invalid",
            "Field options may be supplied only for mapped canonical fields.",
        )
    return [field for field in entity.required_fields if field not in version.field_mapping]


def _apply_mapping_version(
    session: Session,
    user: UserAccount,
    ingestion: UniversalIngestion,
    version: UniversalMappingProfileVersion,
    request_id: UUID,
) -> UniversalMappingProfileVersionResponse:
    if ingestion.status == "promoted":
        raise _api_error(
            409, "universal_ingestion_already_promoted", "A promoted ingestion is immutable."
        )
    missing = _validate_mapping_against_ingestion(ingestion, version)
    if ingestion.mapping_profile_version_id == version.id and ingestion.validation_hash is not None:
        saved_validation_snapshot = ingestion.validation_snapshot
        if (
            saved_validation_snapshot is None
            or content_hash(saved_validation_snapshot) != ingestion.validation_hash
            or saved_validation_snapshot.get("mapping_profile_version_id") != str(version.id)
        ):
            raise _api_error(
                409,
                "validation_snapshot_integrity_error",
                "The saved validation snapshot failed its integrity check.",
            )
        return _mapping_response(session, version)

    validations_for_version = session.scalar(
        select(func.count())
        .select_from(UniversalRowValidation)
        .where(
            UniversalRowValidation.tenant_id == ingestion.tenant_id,
            UniversalRowValidation.company_id == ingestion.company_id,
            UniversalRowValidation.ingestion_id == ingestion.id,
            UniversalRowValidation.mapping_profile_version_id == version.id,
        )
    )
    if validations_for_version:
        raise _api_error(
            409,
            "universal_mapping_version_already_validated",
            "This mapping version already has immutable validation rows for this ingestion. "
            "Create a new mapping version to validate different settings.",
        )
    previous_status = ingestion.status
    ingestion.mapping_profile_version_id = version.id
    ingestion.selected_worksheet = version.worksheet_name
    ingestion.status = "mapped"
    ingestion.total_rows = (
        session.scalar(
            select(func.count())
            .select_from(UniversalIngestionRow)
            .where(
                UniversalIngestionRow.tenant_id == ingestion.tenant_id,
                UniversalIngestionRow.company_id == ingestion.company_id,
                UniversalIngestionRow.ingestion_id == ingestion.id,
                UniversalIngestionRow.worksheet_name == version.worksheet_name,
            )
        )
        or 0
    )
    ingestion.valid_rows = 0
    ingestion.invalid_rows = 0
    ingestion.warning_rows = 0
    ingestion.duplicate_rows = 0
    ingestion.promoted_rows = 0
    ingestion.validation_hash = None
    ingestion.validation_snapshot = None
    ingestion.failure_code = None
    ingestion.created_records = 0
    ingestion.revised_records = 0
    ingestion.unchanged_records = 0
    _append_event(
        session,
        ingestion,
        user.id,
        "mapping_applied",
        {
            "mapping_profile_version_id": str(version.id),
            "mapping_version": version.version,
            "entity_type": version.entity_type,
            "worksheet_name": version.worksheet_name,
            "missing_required_fields": missing,
        },
        from_status=previous_status,
        to_status="mapped",
    )
    record_event(
        session,
        "universal_data.mapping_applied",
        tenant_id=ingestion.tenant_id,
        actor_user_id=user.id,
        target_type="universal_ingestion",
        target_id=ingestion.id,
        request_id=request_id,
        details={"mapping_profile_version_id": str(version.id), "mapping_version": version.version},
    )
    return _mapping_response(session, version)


def create_mapping_and_apply(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    body: UniversalMappingCreateRequest,
    request_id: UUID,
) -> UniversalMappingProfileVersionResponse:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id, lock=True)
    ingestion = _ingestion(
        session, tenant_id, access.company.id, source.id, ingestion_id, lock=True
    )
    _contract(session)
    headers = _mapping_headers(ingestion, body.worksheet_name)
    entity = CANONICAL_ENTITIES[body.entity_type]
    unknown_fields = set(body.field_mapping) - set(entity.field_by_name)
    unknown_columns = set(body.field_mapping.values()) - set(headers)
    if unknown_fields or unknown_columns:
        raise _api_error(
            422,
            "universal_mapping_invalid",
            "The mapping references an unknown field or source column.",
        )
    if not body.field_mapping:
        raise _api_error(
            422, "universal_mapping_empty", "Map at least one source column to a canonical field."
        )
    if len(set(body.field_mapping.values())) != len(body.field_mapping):
        raise _api_error(
            422,
            "universal_mapping_duplicate_column",
            "A source column may map to only one canonical field.",
        )
    if set(body.ignored_columns) - set(headers):
        raise _api_error(
            422,
            "universal_mapping_invalid",
            "An ignored column is not present in the selected worksheet.",
        )
    if set(body.field_mapping.values()).intersection(body.ignored_columns):
        raise _api_error(
            422, "universal_mapping_invalid", "A source column cannot be mapped and ignored."
        )
    if set(body.field_mapping.values()) | set(body.ignored_columns) != set(headers):
        raise _api_error(
            422, "universal_mapping_incomplete", "Map or explicitly ignore every source column."
        )
    field_options = {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in body.field_options.items()
    }
    if set(field_options) - set(body.field_mapping):
        raise _api_error(
            422,
            "universal_mapping_options_invalid",
            "Field options may be supplied only for mapped canonical fields.",
        )

    profile = session.scalar(
        select(UniversalMappingProfile).where(
            UniversalMappingProfile.tenant_id == tenant_id,
            UniversalMappingProfile.company_id == access.company.id,
            UniversalMappingProfile.source_id == source.id,
            UniversalMappingProfile.profile_key == body.profile_key,
        )
    )
    if profile is None:
        profile = UniversalMappingProfile(
            id=uuid4(),
            tenant_id=tenant_id,
            company_id=access.company.id,
            source_id=source.id,
            profile_key=body.profile_key,
            display_name=body.display_name,
            created_by_user_id=user.id,
        )
        session.add(profile)
        session.flush()
        version_number = 1
    else:
        if profile.display_name != body.display_name:
            raise _api_error(
                409,
                "universal_mapping_profile_name_conflict",
                "Mapping profile names are immutable; reuse the saved name or choose a new "
                "profile key.",
            )
        version_number = (
            session.scalar(
                select(func.max(UniversalMappingProfileVersion.version)).where(
                    UniversalMappingProfileVersion.tenant_id == tenant_id,
                    UniversalMappingProfileVersion.company_id == access.company.id,
                    UniversalMappingProfileVersion.profile_id == profile.id,
                )
            )
            or 0
        ) + 1
    version = UniversalMappingProfileVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source.id,
        profile_id=profile.id,
        version=version_number,
        entity_type=body.entity_type,
        worksheet_name=body.worksheet_name,
        field_mapping=body.field_mapping,
        ignored_columns=body.ignored_columns,
        field_options=field_options,
        contract_hash=content_hash(canonical_contract_definition()),
        created_by_user_id=user.id,
    )
    session.add(version)
    session.flush()
    return _apply_mapping_version(session, user, ingestion, version, request_id)


def apply_existing_mapping(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    mapping_version_id: UUID,
    request_id: UUID,
) -> UniversalMappingProfileVersionResponse:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id, lock=True)
    ingestion = _ingestion(
        session, tenant_id, access.company.id, source.id, ingestion_id, lock=True
    )
    version = _mapping_profile_version(
        session, tenant_id, access.company.id, source.id, mapping_version_id
    )
    return _apply_mapping_version(session, user, ingestion, version, request_id)


def list_mapping_profiles(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> UniversalMappingProfileList:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    base = select(UniversalMappingProfileVersion).where(
        UniversalMappingProfileVersion.tenant_id == tenant_id,
        UniversalMappingProfileVersion.company_id == access.company.id,
        UniversalMappingProfileVersion.source_id == source.id,
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    versions = list(
        session.scalars(
            base.order_by(
                UniversalMappingProfileVersion.profile_id,
                UniversalMappingProfileVersion.version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return UniversalMappingProfileList(
        items=[_mapping_response(session, version) for version in versions],
        total=total,
        limit=limit,
        offset=offset,
    )


def list_ingestions(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> UniversalIngestionPage:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    base = select(UniversalIngestion).where(
        UniversalIngestion.tenant_id == tenant_id,
        UniversalIngestion.company_id == access.company.id,
        UniversalIngestion.source_id == source.id,
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    ingestions = list(
        session.scalars(
            base.order_by(UniversalIngestion.created_at.desc(), UniversalIngestion.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return UniversalIngestionPage(
        items=[_ingestion_summary(session, ingestion) for ingestion in ingestions],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_ingestion_detail(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
) -> UniversalIngestionDetail:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    ingestion = _ingestion(session, tenant_id, access.company.id, source.id, ingestion_id)
    mapping_payload = None
    if ingestion.mapping_profile_version_id is not None:
        version = _mapping_profile_version(
            session,
            tenant_id,
            access.company.id,
            source.id,
            ingestion.mapping_profile_version_id,
        )
        mapping_payload = _mapping_response(session, version).model_dump(mode="json")
    return UniversalIngestionDetail(
        ingestion=_ingestion_summary(session, ingestion),
        inspection=ingestion.inspection,
        validation_snapshot=ingestion.validation_snapshot,
        mapping_profile_version=mapping_payload,
        available_worksheets=_inspection_worksheets(ingestion),
        evidence_retained=True,
        source_authority_note=(
            "The file is retained as source evidence. Its registration, freshness, row validity, "
            "and owner-selected authority classification do not prove completeness or correctness."
        ),
    )


def _raw_row_dict(headers: list[str], raw_values: list[object]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for index, header in enumerate(headers):
        value = raw_values[index] if index < len(raw_values) else None
        result[header] = value if isinstance(value, str) else None
    return result


def _validate_ingestion_input(
    session: Session,
    ingestion: UniversalIngestion,
    snapshot: UniversalSourceSnapshot,
    version: UniversalMappingProfileVersion,
) -> None:
    if (
        hashlib.sha256(snapshot.original_content).hexdigest() != ingestion.input_fingerprint
        or snapshot.input_fingerprint != ingestion.input_fingerprint
        or snapshot.file_format != ingestion.file_format
        or snapshot.file_size_bytes != len(snapshot.original_content)
    ):
        raise _api_error(
            409,
            "source_snapshot_integrity_error",
            "The retained source evidence failed its fingerprint or metadata check.",
        )
    if snapshot.id != ingestion.snapshot_id or snapshot.source_id != ingestion.source_id:
        raise _api_error(
            409,
            "source_snapshot_integrity_error",
            "The retained source evidence is not bound to this ingestion.",
        )
    if version.id != ingestion.mapping_profile_version_id:
        raise _api_error(
            409, "universal_mapping_changed", "Apply a mapping profile version before validation."
        )
    _validate_mapping_against_ingestion(ingestion, version)
    contract = _contract(session)
    if version.contract_hash != contract.definition_hash:
        raise _api_error(
            409,
            "universal_mapping_contract_mismatch",
            "The mapping no longer matches the published data contract.",
        )


def _finding(
    code: str, severity: str, message: str, *, field: str | None = None, **extra: object
) -> dict[str, object]:
    result: dict[str, object] = {"code": code, "severity": severity, "message": message}
    if field is not None:
        result["field"] = field
    result.update(extra)
    return result


def _validate_source_row(
    raw: dict[str, str | None],
    version: UniversalMappingProfileVersion,
    entity: CanonicalEntity,
) -> tuple[dict[str, object], str | None, list[dict[str, object]], str]:
    mapped: dict[str, object] = {}
    findings: list[dict[str, object]] = []
    for field in entity.fields:
        source_column = version.field_mapping.get(field.name)
        options = version.field_options.get(field.name, {})
        if source_column is None:
            mapped[field.name] = None
            if field.required:
                findings.append(
                    _finding(
                        "required_field_unmapped",
                        "error",
                        f"Required canonical field '{field.name}' is not mapped.",
                        field=field.name,
                    )
                )
            continue
        raw_value = raw.get(source_column)
        try:
            normalized, extra_findings = normalize_field(raw_value, field, options)
        except NormalizationError as exc:
            mapped[field.name] = None
            findings.append(_finding(exc.code, "error", exc.message, field=field.name))
            continue
        mapped[field.name] = normalized
        findings.extend(extra_findings)
    key_value = mapped.get(entity.key_field)
    source_record_id = key_value if isinstance(key_value, str) else None
    if source_record_id is not None and len(source_record_id) > 160:
        findings.append(
            _finding(
                "source_record_id_too_long",
                "error",
                "The source record key may not exceed 160 characters.",
                field=entity.key_field,
            )
        )
        source_record_id = None
    if source_record_id is not None and not source_record_id:
        findings.append(
            _finding(
                "source_record_id_empty",
                "error",
                "The source record key may not be empty.",
                field=entity.key_field,
            )
        )
        source_record_id = None
    status = "invalid" if any(item["severity"] == "error" for item in findings) else "valid"
    fingerprint = content_hash(
        {
            "source_record_id": source_record_id,
            "mapped_values": mapped,
            "findings": findings,
            "mapping_profile_version_id": str(version.id),
            "validation_rule_version": VALIDATION_RULE_VERSION,
        }
    )
    return mapped, source_record_id, findings, status + ":" + fingerprint


def _current_attributes(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    record: CanonicalBusinessRecord,
) -> dict[str, object] | None:
    revision = session.scalar(
        select(CanonicalBusinessRecordRevision).where(
            CanonicalBusinessRecordRevision.tenant_id == tenant_id,
            CanonicalBusinessRecordRevision.company_id == company_id,
            CanonicalBusinessRecordRevision.record_id == record.id,
            CanonicalBusinessRecordRevision.revision == record.current_revision,
        )
    )
    return revision.attributes if revision is not None else None


def _mapped_text(row: dict[str, object], field_name: str) -> str | None:
    mapped = row.get("mapped_values")
    if not isinstance(mapped, dict):
        return None
    value = mapped.get(field_name)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _possible_cross_source_matches(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    entity_type: str,
    mapped_rows: list[dict[str, object]],
) -> dict[int, list[dict[str, object]]]:
    if entity_type not in {"customer", "supplier"}:
        return {}
    emails = {
        value.casefold() for row in mapped_rows if (value := _mapped_text(row, "email")) is not None
    }
    tax_ids = {
        value for row in mapped_rows if (value := _mapped_text(row, "tax_identifier")) is not None
    }
    if not emails and not tax_ids:
        return {}

    email_value = func.lower(CanonicalBusinessRecordRevision.attributes.op("->>")("email"))
    tax_value = CanonicalBusinessRecordRevision.attributes.op("->>")("tax_identifier")
    candidates_by_id: dict[UUID, tuple[CanonicalBusinessRecord, dict[str, object]]] = {}

    def collect_candidates(column: Any, values: set[str]) -> None:
        for value_batch in _batches(sorted(values)):
            candidates = session.execute(
                select(CanonicalBusinessRecord, CanonicalBusinessRecordRevision.attributes)
                .join(
                    CanonicalBusinessRecordRevision,
                    and_(
                        CanonicalBusinessRecordRevision.tenant_id
                        == CanonicalBusinessRecord.tenant_id,
                        CanonicalBusinessRecordRevision.company_id
                        == CanonicalBusinessRecord.company_id,
                        CanonicalBusinessRecordRevision.record_id == CanonicalBusinessRecord.id,
                        CanonicalBusinessRecordRevision.revision
                        == CanonicalBusinessRecord.current_revision,
                    ),
                )
                .where(
                    CanonicalBusinessRecord.tenant_id == tenant_id,
                    CanonicalBusinessRecord.company_id == company_id,
                    CanonicalBusinessRecord.source_id != source_id,
                    CanonicalBusinessRecord.entity_type == entity_type,
                    column.in_(value_batch),
                )
            ).all()
            for record, attributes in candidates:
                candidates_by_id[record.id] = (record, attributes)

    if emails:
        collect_candidates(email_value, emails)
    if tax_ids:
        collect_candidates(tax_value, tax_ids)
    if not candidates_by_id:
        return {}

    record_ids_by_email: dict[str, set[UUID]] = defaultdict(set)
    record_ids_by_tax_id: dict[str, set[UUID]] = defaultdict(set)
    for record_id, (_record, attributes) in candidates_by_id.items():
        candidate_email = attributes.get("email")
        if isinstance(candidate_email, str) and candidate_email.strip():
            record_ids_by_email[candidate_email.strip().casefold()].add(record_id)
        candidate_tax_id = attributes.get("tax_identifier")
        if isinstance(candidate_tax_id, str) and candidate_tax_id.strip():
            record_ids_by_tax_id[candidate_tax_id.strip()].add(record_id)

    result: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row_index, staged in enumerate(mapped_rows):
        email = _mapped_text(staged, "email")
        tax_id = _mapped_text(staged, "tax_identifier")
        matching_fields_by_record: dict[UUID, set[str]] = defaultdict(set)
        if email is not None:
            for record_id in record_ids_by_email.get(email.casefold(), set()):
                matching_fields_by_record[record_id].add("email")
        if tax_id is not None:
            for record_id in record_ids_by_tax_id.get(tax_id, set()):
                matching_fields_by_record[record_id].add("tax_identifier")
        for record_id in sorted(matching_fields_by_record, key=str):
            record, _attributes = candidates_by_id[record_id]
            result[row_index].append(
                {
                    "record_id": str(record.id),
                    "source_id": str(record.source_id),
                    "matching_fields": sorted(matching_fields_by_record[record_id]),
                    "resolution": "unresolved",
                }
            )
    return result


def validate_ingestion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    request_id: UUID,
) -> UniversalValidationResult:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id, lock=True)
    ingestion = _ingestion(
        session, tenant_id, access.company.id, source.id, ingestion_id, lock=True
    )
    if ingestion.status == "promoted":
        raise _api_error(
            409, "universal_ingestion_already_promoted", "A promoted ingestion is immutable."
        )
    if ingestion.mapping_profile_version_id is None:
        raise _api_error(
            409, "universal_mapping_required", "Apply a versioned field mapping before validation."
        )
    version = _mapping_profile_version(
        session, tenant_id, access.company.id, source.id, ingestion.mapping_profile_version_id
    )
    snapshot = session.scalar(
        select(UniversalSourceSnapshot).where(
            UniversalSourceSnapshot.tenant_id == tenant_id,
            UniversalSourceSnapshot.company_id == access.company.id,
            UniversalSourceSnapshot.source_id == source.id,
            UniversalSourceSnapshot.id == ingestion.snapshot_id,
        )
    )
    if snapshot is None:
        raise _api_error(
            409, "source_snapshot_missing", "The original source evidence is unavailable."
        )
    _validate_ingestion_input(session, ingestion, snapshot, version)

    existing_validations = list(
        session.scalars(
            select(UniversalRowValidation).where(
                UniversalRowValidation.tenant_id == tenant_id,
                UniversalRowValidation.company_id == access.company.id,
                UniversalRowValidation.ingestion_id == ingestion.id,
                UniversalRowValidation.mapping_profile_version_id == version.id,
            )
        )
    )
    if ingestion.validation_hash is not None:
        saved_validation_snapshot = ingestion.validation_snapshot
        if (
            saved_validation_snapshot is None
            or content_hash(saved_validation_snapshot) != ingestion.validation_hash
            or saved_validation_snapshot.get("mapping_profile_version_id") != str(version.id)
        ):
            raise _api_error(
                409,
                "validation_snapshot_integrity_error",
                "The saved validation snapshot failed its integrity check.",
            )
        return UniversalValidationResult(
            ingestion=_ingestion_summary(session, ingestion),
            dataset_findings=cast(
                list[dict[str, object]], saved_validation_snapshot.get("dataset_findings", [])
            ),
            validation_snapshot=saved_validation_snapshot,
        )
    if existing_validations:
        raise _api_error(
            409,
            "universal_mapping_version_already_validated",
            "This mapping version already has immutable validation rows for this ingestion. "
            "Apply a new mapping version before validating again.",
        )

    entity = CANONICAL_ENTITIES[version.entity_type]
    headers = _mapping_headers(ingestion, version.worksheet_name)
    rows = list(
        session.scalars(
            select(UniversalIngestionRow)
            .where(
                UniversalIngestionRow.tenant_id == tenant_id,
                UniversalIngestionRow.company_id == access.company.id,
                UniversalIngestionRow.ingestion_id == ingestion.id,
                UniversalIngestionRow.worksheet_name == version.worksheet_name,
            )
            .order_by(UniversalIngestionRow.row_number, UniversalIngestionRow.id)
        )
    )
    if len(rows) != ingestion.total_rows:
        ingestion.total_rows = len(rows)
    staged: list[dict[str, object]] = []
    for source_row in rows:
        raw = _raw_row_dict(headers, source_row.raw_values)
        mapped, source_record_id, findings, status_fingerprint = _validate_source_row(
            raw, version, entity
        )
        status, row_fingerprint = status_fingerprint.split(":", maxsplit=1)
        staged.append(
            {
                "source_row": source_row,
                "raw_values": raw,
                "mapped_values": mapped,
                "source_record_id": source_record_id,
                "findings": findings,
                "status": status,
                "row_fingerprint": row_fingerprint,
            }
        )

    source_key_counts = Counter(
        cast(str, item["source_record_id"])
        for item in staged
        if item["source_record_id"] is not None
    )
    for item in staged:
        key = item["source_record_id"]
        if key is not None and source_key_counts[cast(str, key)] > 1:
            cast(list[dict[str, object]], item["findings"]).append(
                _finding(
                    "duplicate_source_record_key",
                    "error",
                    "This source record key occurs more than once in the selected worksheet. "
                    "Resolve the duplicate before promotion.",
                    field=entity.key_field,
                )
            )
            if item["status"] != "invalid":
                item["status"] = "duplicate"

    existing_by_key: dict[str, CanonicalBusinessRecord] = {}
    keys = {
        cast(str, item["source_record_id"])
        for item in staged
        if item["source_record_id"] is not None
    }
    for key_batch in _batches(sorted(keys)):
        existing_records = session.scalars(
            select(CanonicalBusinessRecord).where(
                CanonicalBusinessRecord.tenant_id == tenant_id,
                CanonicalBusinessRecord.company_id == access.company.id,
                CanonicalBusinessRecord.source_id == source.id,
                CanonicalBusinessRecord.entity_type == version.entity_type,
                CanonicalBusinessRecord.source_record_id.in_(key_batch),
            )
        )
        existing_by_key.update({record.source_record_id: record for record in existing_records})
    for item in staged:
        key = item["source_record_id"]
        if key is None or cast(str, item["status"]) in {"invalid", "duplicate"}:
            continue
        existing_record = existing_by_key.get(cast(str, key))
        if existing_record is None:
            continue
        existing_attributes = _current_attributes(
            session, tenant_id, access.company.id, existing_record
        )
        new_attributes = cast(dict[str, object], item["mapped_values"])
        if existing_attributes is not None and content_hash(existing_attributes) != content_hash(
            new_attributes
        ):
            cast(list[dict[str, object]], item["findings"]).append(
                _finding(
                    "existing_source_record_changed",
                    "warning",
                    "The source key already exists with different attributes. "
                    "Promotion creates an immutable revision and requires a reviewer reason.",
                    field=entity.key_field,
                    existing_revision=existing_record.current_revision,
                )
            )
            item["status"] = "warning"

    cross_source_matches = _possible_cross_source_matches(
        session, tenant_id, access.company.id, source.id, version.entity_type, staged
    )
    for row_index, matches in cross_source_matches.items():
        if not matches:
            continue
        item = staged[row_index]
        if cast(str, item["status"]) in {"invalid", "duplicate"}:
            continue
        cast(list[dict[str, object]], item["findings"]).append(
            _finding(
                "possible_cross_source_duplicate",
                "warning",
                "A matching identifier exists in another source. No automatic merge will occur; "
                "review the candidates before promotion.",
                field="email"
                if any("email" in cast(list[str], match["matching_fields"]) for match in matches)
                else "tax_identifier",
                candidates=matches,
            )
        )
        item["status"] = "warning"

    validations: list[UniversalRowValidation] = []
    for item in staged:
        source_row = cast(UniversalIngestionRow, item["source_row"])
        findings = cast(list[dict[str, object]], item["findings"])
        mapped_values = cast(dict[str, object], item["mapped_values"])
        row_fingerprint = content_hash(
            {
                "source_row_hash": source_row.row_hash,
                "source_record_id": item["source_record_id"],
                "mapped_values": mapped_values,
                "findings": findings,
                "mapping_profile_version_id": str(version.id),
                "validation_rule_version": VALIDATION_RULE_VERSION,
            }
        )
        validations.append(
            UniversalRowValidation(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=access.company.id,
                source_id=source.id,
                snapshot_id=ingestion.snapshot_id,
                ingestion_id=ingestion.id,
                source_row_id=source_row.id,
                mapping_profile_version_id=version.id,
                source_record_id=cast(str | None, item["source_record_id"]),
                mapped_values=mapped_values,
                findings=findings,
                row_status=cast(str, item["status"]),
                row_fingerprint=row_fingerprint,
                validation_rule_version=VALIDATION_RULE_VERSION,
            )
        )
    session.add_all(validations)
    session.flush()

    counts = Counter(item.row_status for item in validations)
    dataset_findings: list[dict[str, object]] = []
    if not rows:
        dataset_findings.append(
            _finding(
                "empty_dataset",
                "error",
                "The selected worksheet contains no non-empty data rows.",
            )
        )
    missing_required = [
        field for field in entity.required_fields if field not in version.field_mapping
    ]
    if missing_required:
        dataset_findings.append(
            _finding(
                "required_fields_unmapped",
                "error",
                "The mapping does not cover every required canonical field.",
                missing_fields=missing_required,
            )
        )
    validation_snapshot: dict[str, object] = {
        "validation_rule_version": VALIDATION_RULE_VERSION,
        "contract_key": UNIVERSAL_CONTRACT_KEY,
        "contract_version": UNIVERSAL_CONTRACT_VERSION,
        "contract_hash": version.contract_hash,
        "entity_type": version.entity_type,
        "ingestion_id": str(ingestion.id),
        "source_profile_version_id": str(ingestion.source_profile_version_id),
        "mapping_profile_version_id": str(version.id),
        "worksheet_name": version.worksheet_name,
        "total_rows": len(rows),
        "valid_rows": counts["valid"],
        "invalid_rows": counts["invalid"],
        "warning_rows": counts["warning"],
        "duplicate_rows": counts["duplicate"],
        "dataset_findings": dataset_findings,
        "rows": [
            {
                "source_row_id": str(validation.source_row_id),
                "row_fingerprint": validation.row_fingerprint,
                "row_status": validation.row_status,
            }
            for validation in validations
        ],
    }
    validation_hash = content_hash(validation_snapshot)
    previous_status = ingestion.status
    ingestion.validation_snapshot = validation_snapshot
    ingestion.validation_hash = validation_hash
    ingestion.valid_rows = counts["valid"]
    ingestion.invalid_rows = counts["invalid"]
    ingestion.warning_rows = counts["warning"]
    ingestion.duplicate_rows = counts["duplicate"]
    dataset_has_errors = any(finding.get("severity") == "error" for finding in dataset_findings)
    if counts["invalid"]:
        ingestion.failure_code = "row_validation_failed"
        ingestion.status = "blocked"
    elif dataset_has_errors:
        ingestion.failure_code = "dataset_validation_failed"
        ingestion.status = "blocked"
    elif counts["warning"] or counts["duplicate"]:
        ingestion.failure_code = None
        ingestion.status = "review_required"
    else:
        ingestion.failure_code = None
        ingestion.status = "validated"
    _append_event(
        session,
        ingestion,
        user.id,
        "validated",
        {
            "validation_hash": validation_hash,
            "total_rows": len(rows),
            "valid_rows": counts["valid"],
            "invalid_rows": counts["invalid"],
            "warning_rows": counts["warning"],
            "duplicate_rows": counts["duplicate"],
            "dataset_findings": dataset_findings,
        },
        from_status=previous_status,
        to_status=ingestion.status,
    )
    record_event(
        session,
        "universal_data.validation_completed",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="universal_ingestion",
        target_id=ingestion.id,
        request_id=request_id,
        details={
            "validation_hash": validation_hash,
            "total_rows": len(rows),
            "valid_rows": counts["valid"],
            "invalid_rows": counts["invalid"],
            "warning_rows": counts["warning"],
            "duplicate_rows": counts["duplicate"],
        },
    )
    return UniversalValidationResult(
        ingestion=_ingestion_summary(session, ingestion),
        dataset_findings=dataset_findings,
        validation_snapshot=validation_snapshot,
    )


def list_rows(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> UniversalRowPage:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    ingestion = _ingestion(session, tenant_id, access.company.id, source.id, ingestion_id)
    selected_sheet = ingestion.selected_worksheet
    row_query = select(UniversalIngestionRow).where(
        UniversalIngestionRow.tenant_id == tenant_id,
        UniversalIngestionRow.company_id == access.company.id,
        UniversalIngestionRow.ingestion_id == ingestion.id,
    )
    if selected_sheet is not None:
        row_query = row_query.where(UniversalIngestionRow.worksheet_name == selected_sheet)
    total = session.scalar(select(func.count()).select_from(row_query.subquery())) or 0
    rows = list(
        session.scalars(
            row_query.order_by(
                UniversalIngestionRow.worksheet_name, UniversalIngestionRow.row_number
            )
            .limit(limit)
            .offset(offset)
        )
    )
    versions = []
    if ingestion.mapping_profile_version_id is not None:
        versions = list(
            session.scalars(
                select(UniversalRowValidation).where(
                    UniversalRowValidation.tenant_id == tenant_id,
                    UniversalRowValidation.company_id == access.company.id,
                    UniversalRowValidation.ingestion_id == ingestion.id,
                    UniversalRowValidation.mapping_profile_version_id
                    == ingestion.mapping_profile_version_id,
                )
            )
        )
    validations_by_row = {item.source_row_id: item for item in versions}
    headers_by_sheet: dict[str, list[str]] = {}
    for sheet in _inspection_worksheets(ingestion):
        if isinstance(sheet.get("name"), str) and isinstance(sheet.get("headers"), list):
            headers_by_sheet[sheet["name"]] = [
                header for header in sheet["headers"] if isinstance(header, str)
            ]
    items: list[UniversalRowPreview] = []
    for row in rows:
        validation = validations_by_row.get(row.id)
        items.append(
            UniversalRowPreview(
                source_row_id=row.id,
                worksheet_name=row.worksheet_name,
                row_number=row.row_number,
                row_hash=row.row_hash,
                raw_values=_raw_row_dict(
                    headers_by_sheet.get(row.worksheet_name, []), row.raw_values
                ),
                mapped_values=validation.mapped_values if validation else {},
                row_status=cast(Any, validation.row_status if validation else "pending"),
                findings=validation.findings if validation else [],
                source_record_id=validation.source_record_id if validation else None,
            )
        )
    return UniversalRowPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        counts=_ingestion_counts(ingestion),
    )


def get_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
) -> tuple[bytes, str, str]:
    access = _require_company_access(session, user, tenant_id, write=False)
    source = _company_source(session, tenant_id, access.company.id, source_id)
    ingestion = _ingestion(session, tenant_id, access.company.id, source.id, ingestion_id)
    snapshot = session.scalar(
        select(UniversalSourceSnapshot).where(
            UniversalSourceSnapshot.tenant_id == tenant_id,
            UniversalSourceSnapshot.company_id == access.company.id,
            UniversalSourceSnapshot.source_id == source.id,
            UniversalSourceSnapshot.id == ingestion.snapshot_id,
        )
    )
    if (
        snapshot is None
        or ingestion.file_format not in _UNIVERSAL_FILE_CONTENT_TYPES
        or hashlib.sha256(snapshot.original_content).hexdigest() != ingestion.input_fingerprint
        or snapshot.input_fingerprint != ingestion.input_fingerprint
        or snapshot.file_format != ingestion.file_format
        or snapshot.file_size_bytes != len(snapshot.original_content)
    ):
        raise _api_error(
            409,
            "source_snapshot_integrity_error",
            "The retained source evidence is unavailable or failed its "
            "fingerprint or metadata check.",
        )
    content_type = _UNIVERSAL_FILE_CONTENT_TYPES[ingestion.file_format]
    safe_name = safe_file_name(ingestion.file_name)
    return snapshot.original_content, safe_name, content_type


def promote_ingestion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    body: UniversalPromotionRequest,
    request_id: UUID,
) -> UniversalPromotionResult:
    access = _require_company_access(session, user, tenant_id, write=True, request_id=request_id)
    source = _company_source(session, tenant_id, access.company.id, source_id, lock=True)
    ingestion = _ingestion(
        session, tenant_id, access.company.id, source.id, ingestion_id, lock=True
    )
    if ingestion.status == "promoted":
        return UniversalPromotionResult(
            ingestion=_ingestion_summary(session, ingestion),
            created_records=ingestion.created_records,
            revised_records=ingestion.revised_records,
            unchanged_records=ingestion.unchanged_records,
            withheld_duplicate_rows=ingestion.duplicate_rows,
            replayed=True,
            note="This ingestion was already promoted; no records were written again.",
        )
    if (
        ingestion.status not in {"validated", "review_required"}
        or ingestion.validation_snapshot is None
    ):
        _append_event(
            session,
            ingestion,
            user.id,
            "promotion_blocked",
            {"reason": "validation_required", "status": ingestion.status},
            from_status=ingestion.status,
            to_status=ingestion.status,
        )
        record_event(
            session,
            "universal_data.promotion_blocked",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="universal_ingestion",
            target_id=ingestion.id,
            request_id=request_id,
            outcome="failure",
            details={"reason": "validation_required", "status": ingestion.status},
        )
        raise CommitAndRaise(
            _api_error(
                409,
                "universal_ingestion_not_promotable",
                "Validate the ingestion and resolve blocking errors before promotion.",
            )
        )
    if content_hash(ingestion.validation_snapshot) != ingestion.validation_hash:
        raise _api_error(
            409,
            "validation_snapshot_integrity_error",
            "The saved validation snapshot failed its integrity check.",
        )
    dataset_findings = ingestion.validation_snapshot.get("dataset_findings", [])
    if isinstance(dataset_findings, list) and any(
        isinstance(finding, dict) and finding.get("severity") == "error"
        for finding in dataset_findings
    ):
        raise _api_error(
            409,
            "universal_ingestion_dataset_invalid",
            "Resolve dataset-level validation errors before promotion.",
        )
    if ingestion.invalid_rows:
        raise _api_error(
            409, "universal_ingestion_invalid_rows", "Resolve invalid rows before promotion."
        )
    if (ingestion.warning_rows or ingestion.duplicate_rows) and not body.acknowledge_warnings:
        _append_event(
            session,
            ingestion,
            user.id,
            "promotion_blocked",
            {
                "reason": "review_acknowledgement_required",
                "warning_rows": ingestion.warning_rows,
                "duplicate_rows": ingestion.duplicate_rows,
            },
            from_status=ingestion.status,
            to_status=ingestion.status,
        )
        record_event(
            session,
            "universal_data.promotion_blocked",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="universal_ingestion",
            target_id=ingestion.id,
            request_id=request_id,
            outcome="failure",
            details={"reason": "review_acknowledgement_required"},
        )
        raise CommitAndRaise(
            _api_error(
                409,
                "universal_review_acknowledgement_required",
                "Acknowledge the validation warnings and duplicate findings before promotion.",
            )
        )

    if ingestion.mapping_profile_version_id is None:
        raise _api_error(
            409, "universal_mapping_required", "Apply a mapping profile before promotion."
        )
    mapping_version = _mapping_profile_version(
        session, tenant_id, access.company.id, source.id, ingestion.mapping_profile_version_id
    )
    snapshot = session.scalar(
        select(UniversalSourceSnapshot).where(
            UniversalSourceSnapshot.tenant_id == tenant_id,
            UniversalSourceSnapshot.company_id == access.company.id,
            UniversalSourceSnapshot.source_id == source.id,
            UniversalSourceSnapshot.id == ingestion.snapshot_id,
        )
    )
    if snapshot is None:
        raise _api_error(
            409, "source_snapshot_missing", "The original source evidence is unavailable."
        )
    _validate_ingestion_input(session, ingestion, snapshot, mapping_version)
    validations = list(
        session.scalars(
            select(UniversalRowValidation)
            .where(
                UniversalRowValidation.tenant_id == tenant_id,
                UniversalRowValidation.company_id == access.company.id,
                UniversalRowValidation.ingestion_id == ingestion.id,
                UniversalRowValidation.mapping_profile_version_id == mapping_version.id,
            )
            .order_by(UniversalRowValidation.source_row_id)
        )
    )
    if len(validations) != ingestion.total_rows:
        raise _api_error(
            409,
            "universal_validation_incomplete",
            "The validation snapshot is incomplete; validate the ingestion again.",
        )

    record_keys = sorted(
        {
            validation.source_record_id
            for validation in validations
            if validation.source_record_id and validation.row_status in {"valid", "warning"}
        }
    )
    # The source-scoped advisory lock acquired above serializes all promotions for this source.
    # Batch the remaining queries to stay below PostgreSQL's bind-parameter limit at the
    # configured maximum row count.
    current_records: dict[str, CanonicalBusinessRecord] = {}
    for key_batch in _batches(record_keys):
        records = session.scalars(
            select(CanonicalBusinessRecord)
            .where(
                CanonicalBusinessRecord.tenant_id == tenant_id,
                CanonicalBusinessRecord.company_id == access.company.id,
                CanonicalBusinessRecord.source_id == source.id,
                CanonicalBusinessRecord.entity_type == mapping_version.entity_type,
                CanonicalBusinessRecord.source_record_id.in_(key_batch),
            )
            .with_for_update()
        )
        current_records.update({record.source_record_id: record for record in records})

    revisions_by_record: dict[UUID, CanonicalBusinessRecordRevision] = {}
    record_ids = [record.id for record in current_records.values()]
    for record_id_batch in _batches(record_ids):
        revisions = session.scalars(
            select(CanonicalBusinessRecordRevision)
            .join(
                CanonicalBusinessRecord,
                and_(
                    CanonicalBusinessRecordRevision.tenant_id == CanonicalBusinessRecord.tenant_id,
                    CanonicalBusinessRecordRevision.company_id
                    == CanonicalBusinessRecord.company_id,
                    CanonicalBusinessRecordRevision.record_id == CanonicalBusinessRecord.id,
                    CanonicalBusinessRecordRevision.revision
                    == CanonicalBusinessRecord.current_revision,
                ),
            )
            .where(
                CanonicalBusinessRecord.tenant_id == tenant_id,
                CanonicalBusinessRecord.company_id == access.company.id,
                CanonicalBusinessRecord.id.in_(record_id_batch),
            )
        )
        revisions_by_record.update({revision.record_id: revision for revision in revisions})

    changed_existing = []
    for validation in validations:
        if validation.row_status not in {"valid", "warning"} or not validation.source_record_id:
            continue
        existing_record = current_records.get(validation.source_record_id)
        existing_revision = revisions_by_record.get(existing_record.id) if existing_record else None
        if existing_revision is not None and content_hash(
            existing_revision.attributes
        ) != content_hash(validation.mapped_values):
            changed_existing.append(validation)
    if changed_existing and not body.correction_reason:
        _append_event(
            session,
            ingestion,
            user.id,
            "promotion_blocked",
            {"reason": "revision_reason_required", "changed_records": len(changed_existing)},
            from_status=ingestion.status,
            to_status=ingestion.status,
        )
        record_event(
            session,
            "universal_data.promotion_blocked",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="universal_ingestion",
            target_id=ingestion.id,
            request_id=request_id,
            outcome="failure",
            details={
                "reason": "revision_reason_required",
                "changed_records": len(changed_existing),
            },
        )
        raise CommitAndRaise(
            _api_error(
                409,
                "universal_revision_reason_required",
                "Provide a reviewer reason before revising an existing source record.",
            )
        )

    rows_by_id = {
        row.id: row
        for row in session.scalars(
            select(UniversalIngestionRow).where(
                UniversalIngestionRow.tenant_id == tenant_id,
                UniversalIngestionRow.company_id == access.company.id,
                UniversalIngestionRow.ingestion_id == ingestion.id,
            )
        )
    }
    counts: Counter[str] = Counter()
    previous_status = ingestion.status
    now = datetime.now(UTC)
    for validation in validations:
        if validation.row_status == "duplicate":
            session.add(
                UniversalIngestionRecordLink(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    company_id=access.company.id,
                    ingestion_id=ingestion.id,
                    source_row_id=validation.source_row_id,
                    source_record_id=validation.source_record_id,
                    canonical_record_id=None,
                    accepted_revision=None,
                    outcome="withheld_duplicate",
                )
            )
            counts["withheld_duplicate"] += 1
            continue
        if validation.row_status not in {"valid", "warning"}:
            continue
        if validation.source_record_id is None:
            raise _api_error(
                409,
                "universal_validation_integrity_error",
                "A validated row has no stable source record key.",
            )
        source_row = rows_by_id.get(validation.source_row_id)
        if source_row is None:
            raise _api_error(
                409,
                "universal_validation_integrity_error",
                "A validated row has no source evidence row.",
            )
        record = current_records.get(validation.source_record_id)
        if record is None:
            record = CanonicalBusinessRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=access.company.id,
                entity_type=mapping_version.entity_type,
                source_id=source.id,
                source_record_id=validation.source_record_id,
                current_revision=1,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(record)
            session.flush()
            revision_number = 1
            outcome = "created"
            change_reason = "source_import"
            correction_note = None
            counts["created"] += 1
        else:
            current_revision = revisions_by_record.get(record.id)
            if current_revision is None:
                raise _api_error(
                    409,
                    "canonical_record_integrity_error",
                    "The canonical record has no current revision.",
                )
            if content_hash(current_revision.attributes) == content_hash(validation.mapped_values):
                record.last_seen_at = now
                session.add(
                    UniversalIngestionRecordLink(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        company_id=access.company.id,
                        ingestion_id=ingestion.id,
                        source_row_id=source_row.id,
                        source_record_id=validation.source_record_id,
                        canonical_record_id=record.id,
                        accepted_revision=current_revision.revision,
                        outcome="unchanged",
                    )
                )
                counts["unchanged"] += 1
                continue
            revision_number = record.current_revision + 1
            outcome = "revised"
            change_reason = "reviewed_source_update"
            correction_note = body.correction_reason
            counts["revised"] += 1

        revision = CanonicalBusinessRecordRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            company_id=access.company.id,
            record_id=record.id,
            revision=revision_number,
            source_id=source.id,
            source_record_id=validation.source_record_id,
            ingestion_id=ingestion.id,
            snapshot_id=ingestion.snapshot_id,
            source_row_id=source_row.id,
            source_profile_version_id=ingestion.source_profile_version_id,
            mapping_profile_version_id=mapping_version.id,
            attributes=validation.mapped_values,
            attributes_hash=content_hash(validation.mapped_values),
            validation_hash=ingestion.validation_hash,
            change_reason=change_reason,
            correction_note=correction_note,
            created_by_user_id=user.id,
        )
        session.add(revision)
        session.flush()
        if outcome == "revised":
            record.current_revision = revision_number
            record.last_seen_at = now
        session.add(
            UniversalIngestionRecordLink(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=access.company.id,
                ingestion_id=ingestion.id,
                source_row_id=source_row.id,
                source_record_id=validation.source_record_id,
                canonical_record_id=record.id,
                accepted_revision=revision_number,
                outcome=outcome,
            )
        )
        revisions_by_record[record.id] = revision
        current_records[validation.source_record_id] = record

    ingestion.status = "promoted"
    ingestion.promoted_by_user_id = user.id
    ingestion.promoted_at = now
    ingestion.promoted_rows = counts["created"] + counts["revised"] + counts["unchanged"]
    ingestion.created_records = counts["created"]
    ingestion.revised_records = counts["revised"]
    ingestion.unchanged_records = counts["unchanged"]
    ingestion.failure_code = None
    session.flush()
    _append_event(
        session,
        ingestion,
        user.id,
        "promoted",
        {
            "validation_hash": ingestion.validation_hash,
            "created_records": counts["created"],
            "revised_records": counts["revised"],
            "unchanged_records": counts["unchanged"],
            "withheld_duplicate_rows": counts["withheld_duplicate"],
            "source_profile_version_id": str(ingestion.source_profile_version_id),
            "core_financials_eligible": False,
        },
        from_status=previous_status,
        to_status="promoted",
    )
    record_event(
        session,
        "universal_data.ingestion_promoted",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="universal_ingestion",
        target_id=ingestion.id,
        request_id=request_id,
        details={
            "created_records": counts["created"],
            "revised_records": counts["revised"],
            "unchanged_records": counts["unchanged"],
            "withheld_duplicate_rows": counts["withheld_duplicate"],
            "core_financials_eligible": False,
        },
    )
    return UniversalPromotionResult(
        ingestion=_ingestion_summary(session, ingestion),
        created_records=counts["created"],
        revised_records=counts["revised"],
        unchanged_records=counts["unchanged"],
        withheld_duplicate_rows=counts["withheld_duplicate"],
        replayed=False,
        note=(
            "Canonical promotion retains source-scoped evidence and immutable revisions. "
            "It neither merges entities nor creates Core Financials facts."
        ),
    )


def list_canonical_records(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    entity_type: str | None = None,
    source_id: UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> CanonicalRecordPage:
    access = _require_company_access(session, user, tenant_id, write=False)
    base = select(CanonicalBusinessRecord).where(
        CanonicalBusinessRecord.tenant_id == tenant_id,
        CanonicalBusinessRecord.company_id == access.company.id,
    )
    if entity_type is not None:
        if entity_type not in CANONICAL_ENTITIES:
            raise _api_error(
                422, "invalid_entity_type", "Select a supported canonical entity type."
            )
        base = base.where(CanonicalBusinessRecord.entity_type == entity_type)
    if source_id is not None:
        _company_source(session, tenant_id, access.company.id, source_id)
        base = base.where(CanonicalBusinessRecord.source_id == source_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    records = list(
        session.scalars(
            base.order_by(CanonicalBusinessRecord.updated_at.desc(), CanonicalBusinessRecord.id)
            .limit(limit)
            .offset(offset)
        )
    )
    revisions = []
    if records:
        revisions = list(
            session.scalars(
                select(CanonicalBusinessRecordRevision)
                .join(
                    CanonicalBusinessRecord,
                    and_(
                        CanonicalBusinessRecordRevision.tenant_id
                        == CanonicalBusinessRecord.tenant_id,
                        CanonicalBusinessRecordRevision.company_id
                        == CanonicalBusinessRecord.company_id,
                        CanonicalBusinessRecordRevision.record_id == CanonicalBusinessRecord.id,
                        CanonicalBusinessRecordRevision.revision
                        == CanonicalBusinessRecord.current_revision,
                    ),
                )
                .where(
                    CanonicalBusinessRecord.tenant_id == tenant_id,
                    CanonicalBusinessRecord.company_id == access.company.id,
                    CanonicalBusinessRecord.id.in_([record.id for record in records]),
                )
            )
        )
    by_record = {revision.record_id: revision for revision in revisions}
    items = []
    for record in records:
        revision = by_record.get(record.id)
        if revision is None:
            continue
        items.append(
            CanonicalRecordSummary(
                id=record.id,
                entity_type=cast(Any, record.entity_type),
                source_id=record.source_id,
                source_record_id=record.source_record_id,
                revision=revision.revision,
                attributes=revision.attributes,
                attributes_hash=revision.attributes_hash,
                source_snapshot_id=revision.snapshot_id,
                source_row_id=revision.source_row_id,
                ingestion_id=revision.ingestion_id,
                mapping_profile_version_id=revision.mapping_profile_version_id,
                source_profile_version_id=revision.source_profile_version_id,
                created_at=revision.created_at,
            )
        )
    return CanonicalRecordPage(items=items, total=total, limit=limit, offset=offset)


def list_record_lineage(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    record_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> UniversalLineagePage:
    access = _require_company_access(session, user, tenant_id, write=False)
    record = session.scalar(
        select(CanonicalBusinessRecord).where(
            CanonicalBusinessRecord.id == record_id,
            CanonicalBusinessRecord.tenant_id == tenant_id,
            CanonicalBusinessRecord.company_id == access.company.id,
        )
    )
    if record is None:
        raise _api_error(404, "canonical_record_not_found", "The canonical record was not found.")
    base = (
        select(
            CanonicalBusinessRecordRevision,
            UniversalIngestion,
            UniversalSourceSnapshot,
            UniversalMappingProfileVersion,
            CompanyDataSourceProfileVersion,
        )
        .join(
            UniversalIngestion,
            and_(
                UniversalIngestion.tenant_id == CanonicalBusinessRecordRevision.tenant_id,
                UniversalIngestion.company_id == CanonicalBusinessRecordRevision.company_id,
                UniversalIngestion.id == CanonicalBusinessRecordRevision.ingestion_id,
            ),
        )
        .join(
            UniversalSourceSnapshot,
            and_(
                UniversalSourceSnapshot.tenant_id == CanonicalBusinessRecordRevision.tenant_id,
                UniversalSourceSnapshot.company_id == CanonicalBusinessRecordRevision.company_id,
                UniversalSourceSnapshot.id == CanonicalBusinessRecordRevision.snapshot_id,
            ),
        )
        .join(
            UniversalMappingProfileVersion,
            and_(
                UniversalMappingProfileVersion.tenant_id
                == CanonicalBusinessRecordRevision.tenant_id,
                UniversalMappingProfileVersion.company_id
                == CanonicalBusinessRecordRevision.company_id,
                UniversalMappingProfileVersion.id
                == CanonicalBusinessRecordRevision.mapping_profile_version_id,
            ),
        )
        .join(
            CompanyDataSourceProfileVersion,
            and_(
                CompanyDataSourceProfileVersion.tenant_id
                == CanonicalBusinessRecordRevision.tenant_id,
                CompanyDataSourceProfileVersion.company_id
                == CanonicalBusinessRecordRevision.company_id,
                CompanyDataSourceProfileVersion.id
                == CanonicalBusinessRecordRevision.source_profile_version_id,
            ),
        )
        .where(
            CanonicalBusinessRecordRevision.tenant_id == tenant_id,
            CanonicalBusinessRecordRevision.company_id == access.company.id,
            CanonicalBusinessRecordRevision.record_id == record.id,
        )
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(CanonicalBusinessRecordRevision.revision.desc()).limit(limit).offset(offset)
    ).all()
    items: list[UniversalLineageItem] = []
    for revision, ingestion, snapshot, mapping, source_profile in rows:
        source_profile_version = source_profile.version
        items.append(
            UniversalLineageItem(
                record_id=record.id,
                entity_type=cast(Any, record.entity_type),
                source_id=record.source_id,
                source_record_id=record.source_record_id,
                revision=revision.revision,
                attributes_hash=revision.attributes_hash,
                source_snapshot_id=snapshot.id,
                input_fingerprint=snapshot.input_fingerprint,
                file_name=ingestion.file_name,
                worksheet_name=_mapping_response(session, mapping).worksheet_name,
                row_number=session.scalar(
                    select(UniversalIngestionRow.row_number).where(
                        UniversalIngestionRow.id == revision.source_row_id,
                        UniversalIngestionRow.ingestion_id == ingestion.id,
                        UniversalIngestionRow.tenant_id == tenant_id,
                        UniversalIngestionRow.company_id == access.company.id,
                    )
                )
                or 0,
                row_hash=session.scalar(
                    select(UniversalIngestionRow.row_hash).where(
                        UniversalIngestionRow.id == revision.source_row_id,
                        UniversalIngestionRow.ingestion_id == ingestion.id,
                        UniversalIngestionRow.tenant_id == tenant_id,
                        UniversalIngestionRow.company_id == access.company.id,
                    )
                )
                or "0" * 64,
                ingestion_id=ingestion.id,
                initiated_by_user_id=ingestion.initiated_by_user_id,
                promoted_by_user_id=cast(UUID, ingestion.promoted_by_user_id),
                mapping_profile_id=mapping.profile_id,
                mapping_profile_version_id=mapping.id,
                mapping_version=mapping.version,
                contract_key=UNIVERSAL_CONTRACT_KEY,
                contract_version=UNIVERSAL_CONTRACT_VERSION,
                contract_hash=mapping.contract_hash,
                validation_hash=revision.validation_hash,
                source_profile_version_id=source_profile.id,
                source_profile_version=source_profile_version,
                captured_at=snapshot.created_at,
                promoted_at=cast(datetime, ingestion.promoted_at),
            )
        )
    return UniversalLineagePage(items=items, total=total, limit=limit, offset=offset)
