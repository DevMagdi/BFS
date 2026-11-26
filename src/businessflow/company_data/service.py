from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_data.contracts import validate_import_mapping
from businessflow.company_data.models import (
    CompanyDataContract,
    CompanyDataIngestion,
    CompanyDataSource,
)
from businessflow.company_data.quality import (
    QualityAssessment,
    assess_quality,
    evaluate_freshness,
    m10_warning_codes_are_compatible,
)
from businessflow.company_data.schemas import (
    CompanyDataContractListResponse,
    CompanyDataContractResponse,
    CompanyDataContractVersionSummary,
    CompanyDataCoverageFreshnessCounts,
    CompanyDataCoverageIngestionCounts,
    CompanyDataCoverageQualityCounts,
    CompanyDataCoverageResponse,
    CompanyDataDatasetCoverage,
    CompanyDataDatasetType,
    CompanyDataFreshnessStatus,
    CompanyDataIngestionCreateRequest,
    CompanyDataIngestionListResponse,
    CompanyDataIngestionResponse,
    CompanyDataIngestionStatus,
    CompanyDataIngestionSummary,
    CompanyDataLineageItem,
    CompanyDataLineagePage,
    CompanyDataQualityStatus,
    CompanyDataSourceCreateRequest,
    CompanyDataSourceListResponse,
    CompanyDataSourceSummary,
)
from businessflow.data_imports.catalog import DATASETS
from businessflow.data_imports.models import DataImport, DataImportRecordLink, DataImportRow
from businessflow.data_imports.schemas import ImportDataset
from businessflow.errors import ApiError
from businessflow.metrics.snapshot import content_hash
from businessflow.models import TenantMembership, UserAccount
from businessflow.services.company_context_access import require_company_context_access


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat(timespec="microseconds")


def _source_query(session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID):
    return session.scalar(
        select(CompanyDataSource).where(
            CompanyDataSource.id == source_id,
            CompanyDataSource.tenant_id == tenant_id,
            CompanyDataSource.company_id == company_id,
        )
    )


def _contract_snapshot(contract: CompanyDataContract) -> dict[str, object]:
    if content_hash(contract.definition) != contract.definition_hash:
        raise ApiError(
            409,
            "company_data_contract_integrity_error",
            "The registered schema contract failed its content-hash check.",
        )
    return {
        "contract_key": contract.contract_key,
        "version": contract.version,
        "dataset_type": contract.dataset_type,
        "definition": contract.definition,
        "definition_hash": contract.definition_hash,
    }


def _current_freshness(
    ingestion: CompanyDataIngestion, *, now: datetime | None = None
) -> CompanyDataFreshnessStatus:
    return evaluate_freshness(
        max_age_hours=ingestion.freshness_max_age_hours_snapshot,
        source_as_of_at=ingestion.source_as_of_at,
        evaluated_at=now or datetime.now(UTC),
    )


def _summary(
    ingestion: CompanyDataIngestion,
) -> CompanyDataIngestionSummary:
    return CompanyDataIngestionSummary(
        id=ingestion.id,
        tenant_id=ingestion.tenant_id,
        company_id=ingestion.company_id,
        source_id=ingestion.source_id,
        import_id=ingestion.import_id,
        ingested_by_user_id=ingestion.ingested_by_user_id,
        contract_key=ingestion.contract_key,
        contract_version=ingestion.contract_version,
        ingestion_status=cast(CompanyDataIngestionStatus, ingestion.ingestion_status),
        quality_status=cast(CompanyDataQualityStatus, ingestion.quality_status),
        freshness_status=cast(CompanyDataFreshnessStatus, ingestion.freshness_status),
        current_freshness_status=_current_freshness(ingestion),
        source_as_of_at=ingestion.source_as_of_at,
        freshness_max_age_hours=ingestion.freshness_max_age_hours_snapshot,
        freshness_evaluated_at=ingestion.freshness_evaluated_at,
        contract_hash=ingestion.contract_hash,
        file_hash=ingestion.file_hash,
        validation_hash=ingestion.validation_hash,
        mapping_version=ingestion.mapping_version,
        total_rows=ingestion.total_rows,
        snapshot_hash=ingestion.snapshot_hash,
        created_at=ingestion.created_at,
    )


def _verify_ingestion_snapshot(ingestion: CompanyDataIngestion) -> None:
    contract_snapshot = ingestion.contract_snapshot
    freshness_snapshot = ingestion.snapshot.get("freshness")
    if not isinstance(freshness_snapshot, dict):
        freshness_snapshot = {}
    m4_counts_snapshot = ingestion.snapshot.get("m4_counts")
    if not isinstance(m4_counts_snapshot, dict):
        m4_counts_snapshot = {}
    if (
        content_hash(ingestion.snapshot) != ingestion.snapshot_hash
        or content_hash(contract_snapshot.get("definition")) != ingestion.contract_hash
        or contract_snapshot.get("contract_key") != ingestion.contract_key
        or contract_snapshot.get("version") != ingestion.contract_version
        or contract_snapshot.get("definition_hash") != ingestion.contract_hash
        or contract_snapshot.get("dataset_type") != ingestion.snapshot.get("dataset_type")
        or ingestion.snapshot.get("snapshot_schema_version") != 1
        or ingestion.snapshot.get("ingestion_id") != str(ingestion.id)
        or ingestion.snapshot.get("tenant_id") != str(ingestion.tenant_id)
        or ingestion.snapshot.get("company_id") != str(ingestion.company_id)
        or ingestion.snapshot.get("source_id") != str(ingestion.source_id)
        or ingestion.snapshot.get("import_id") != str(ingestion.import_id)
        or ingestion.snapshot.get("contract_key") != ingestion.contract_key
        or ingestion.snapshot.get("contract_version") != ingestion.contract_version
        or ingestion.snapshot.get("contract_hash") != ingestion.contract_hash
        or ingestion.snapshot.get("contract_snapshot") != ingestion.contract_snapshot
        or m4_counts_snapshot.get("total_rows") != ingestion.total_rows
        or ingestion.snapshot.get("recorded_at") != _iso(ingestion.created_at)
        or ingestion.snapshot.get("file_hash") != ingestion.file_hash
        or ingestion.snapshot.get("validation_hash") != ingestion.validation_hash
        or ingestion.snapshot.get("mapping_version") != ingestion.mapping_version
        or ingestion.snapshot.get("ingestion_status") != ingestion.ingestion_status
        or ingestion.snapshot.get("quality_status") != ingestion.quality_status
        or ingestion.snapshot.get("quality_findings") != ingestion.quality_findings
        or freshness_snapshot.get("status") != ingestion.freshness_status
        or freshness_snapshot.get("reported_source_as_of_at") != _iso(ingestion.source_as_of_at)
        or freshness_snapshot.get("max_age_hours") != ingestion.freshness_max_age_hours_snapshot
        or freshness_snapshot.get("evaluated_at") != _iso(ingestion.freshness_evaluated_at)
    ):
        raise ApiError(
            409,
            "company_data_snapshot_integrity_error",
            "The immutable company-data ingestion snapshot failed its content-hash check.",
        )


def _response(ingestion: CompanyDataIngestion, *, replayed: bool = False):
    _verify_ingestion_snapshot(ingestion)
    return CompanyDataIngestionResponse(
        ingestion=_summary(ingestion),
        quality_findings=ingestion.quality_findings,
        contract_snapshot=ingestion.contract_snapshot,
        snapshot=ingestion.snapshot,
        replayed=replayed,
    )


def list_contracts(
    session: Session, user: UserAccount, tenant_id: UUID
) -> CompanyDataContractListResponse:
    require_company_context_access(session, user, tenant_id, "company_context:read")
    contracts = session.scalars(
        select(CompanyDataContract).order_by(
            CompanyDataContract.dataset_type,
            CompanyDataContract.contract_key,
            CompanyDataContract.version,
        )
    )
    items: list[CompanyDataContractResponse] = []
    for contract in contracts:
        _contract_snapshot(contract)
        items.append(
            CompanyDataContractResponse(
                contract_key=contract.contract_key,
                version=contract.version,
                dataset_type=contract.dataset_type,
                definition=contract.definition,
                definition_hash=contract.definition_hash,
                created_at=contract.created_at,
            )
        )
    return CompanyDataContractListResponse(items=items)


def _latest_by_source(
    session: Session, sources: list[CompanyDataSource]
) -> tuple[dict[UUID, CompanyDataIngestion], dict[UUID, CompanyDataIngestion]]:
    if not sources:
        return {}, {}
    rows = session.scalars(
        select(CompanyDataIngestion)
        .where(
            CompanyDataIngestion.source_id.in_([source.id for source in sources]),
            CompanyDataIngestion.tenant_id == sources[0].tenant_id,
            CompanyDataIngestion.company_id == sources[0].company_id,
        )
        .order_by(CompanyDataIngestion.created_at.desc(), CompanyDataIngestion.id.desc())
    )
    latest: dict[UUID, CompanyDataIngestion] = {}
    successful: dict[UUID, CompanyDataIngestion] = {}
    for row in rows:
        latest.setdefault(row.source_id, row)
        if row.ingestion_status in {"succeeded", "succeeded_with_warnings"}:
            successful.setdefault(row.source_id, row)
    return latest, successful


def _source_summary(
    source: CompanyDataSource,
    latest: CompanyDataIngestion | None,
    successful: CompanyDataIngestion | None,
) -> CompanyDataSourceSummary:
    return CompanyDataSourceSummary(
        id=source.id,
        tenant_id=source.tenant_id,
        company_id=source.company_id,
        source_key=source.source_key,
        display_name=source.display_name,
        dataset_type=cast(CompanyDataDatasetType, source.dataset_type),
        contract_key=source.contract_key,
        contract_version=source.contract_version,
        owner_user_id=source.owner_user_id,
        registered_by_user_id=source.registered_by_user_id,
        freshness_max_age_hours=source.freshness_max_age_hours,
        created_at=source.created_at,
        latest_ingestion_id=latest.id if latest else None,
        latest_ingestion_at=latest.created_at if latest else None,
        latest_ingestion_status=(
            cast(CompanyDataIngestionStatus, latest.ingestion_status) if latest else None
        ),
        latest_quality_status=(
            cast(CompanyDataQualityStatus, latest.quality_status) if latest else None
        ),
        latest_freshness_status=_current_freshness(latest) if latest else None,
        latest_recorded_freshness_status=(
            cast(CompanyDataFreshnessStatus, latest.freshness_status) if latest else None
        ),
        last_successful_ingestion_id=successful.id if successful else None,
        last_successful_ingestion_at=successful.created_at if successful else None,
    )


@dataclass(slots=True)
class _DatasetCoverageAccumulator:
    registered_source_count: int = 0
    sources_without_ingestion_count: int = 0
    succeeded: int = 0
    succeeded_with_warnings: int = 0
    blocked: int = 0
    quality_passed: int = 0
    quality_warning: int = 0
    quality_failed: int = 0
    freshness_fresh: int = 0
    freshness_stale: int = 0
    freshness_policy_missing: int = 0
    freshness_source_as_of_missing: int = 0
    freshness_future_as_of: int = 0
    latest_ingestion_at: datetime | None = None


def list_sources(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
) -> CompanyDataSourceListResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = (
        CompanyDataSource.tenant_id == tenant_id,
        CompanyDataSource.company_id == access.company.id,
    )
    total = session.scalar(select(func.count()).select_from(CompanyDataSource).where(*filters)) or 0
    sources = list(
        session.scalars(
            select(CompanyDataSource)
            .where(*filters)
            .order_by(CompanyDataSource.created_at.desc(), CompanyDataSource.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    latest, successful = _latest_by_source(session, sources)
    return CompanyDataSourceListResponse(
        items=[
            _source_summary(source, latest.get(source.id), successful.get(source.id))
            for source in sources
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_company_data_coverage(
    session: Session, user: UserAccount, tenant_id: UUID
) -> CompanyDataCoverageResponse:
    """Summarize registered M12 evidence without implying business-data completeness."""
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    evaluated_at = datetime.now(UTC)
    accumulators = {dataset: _DatasetCoverageAccumulator() for dataset in DATASETS}
    contract_versions: dict[ImportDataset, list[CompanyDataContractVersionSummary]] = {
        dataset: [] for dataset in DATASETS
    }

    contracts = session.scalars(
        select(CompanyDataContract).order_by(
            CompanyDataContract.dataset_type,
            CompanyDataContract.contract_key,
            CompanyDataContract.version,
        )
    )
    for contract in contracts:
        _contract_snapshot(contract)
        if contract.dataset_type in contract_versions:
            contract_versions[contract.dataset_type].append(
                CompanyDataContractVersionSummary(
                    contract_key=contract.contract_key,
                    version=contract.version,
                    definition_hash=contract.definition_hash,
                )
            )

    latest_ingestions = (
        select(
            CompanyDataIngestion.source_id.label("source_id"),
            CompanyDataIngestion.ingestion_status.label("ingestion_status"),
            CompanyDataIngestion.quality_status.label("quality_status"),
            CompanyDataIngestion.source_as_of_at.label("source_as_of_at"),
            CompanyDataIngestion.freshness_max_age_hours_snapshot.label("freshness_max_age_hours"),
            CompanyDataIngestion.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=CompanyDataIngestion.source_id,
                order_by=(CompanyDataIngestion.created_at.desc(), CompanyDataIngestion.id.desc()),
            )
            .label("sequence_number"),
        )
        .where(
            CompanyDataIngestion.tenant_id == tenant_id,
            CompanyDataIngestion.company_id == access.company.id,
        )
        .subquery("latest_company_data_ingestions")
    )
    source_states = session.execute(
        select(
            CompanyDataSource.dataset_type,
            latest_ingestions.c.ingestion_status,
            latest_ingestions.c.quality_status,
            latest_ingestions.c.source_as_of_at,
            latest_ingestions.c.freshness_max_age_hours,
            latest_ingestions.c.created_at.label("latest_ingestion_at"),
        )
        .select_from(CompanyDataSource)
        .outerjoin(
            latest_ingestions,
            and_(
                CompanyDataSource.id == latest_ingestions.c.source_id,
                latest_ingestions.c.sequence_number == 1,
            ),
        )
        .where(
            CompanyDataSource.tenant_id == tenant_id,
            CompanyDataSource.company_id == access.company.id,
            CompanyDataSource.dataset_type.in_(tuple(DATASETS)),
        )
    ).yield_per(500)

    for row in source_states:
        dataset_type = cast(ImportDataset, row.dataset_type)
        accumulator = accumulators[dataset_type]
        accumulator.registered_source_count += 1
        if row.ingestion_status is None:
            accumulator.sources_without_ingestion_count += 1
            continue

        if row.ingestion_status == "succeeded":
            accumulator.succeeded += 1
        elif row.ingestion_status == "succeeded_with_warnings":
            accumulator.succeeded_with_warnings += 1
        elif row.ingestion_status == "blocked":
            accumulator.blocked += 1

        if row.quality_status == "passed":
            accumulator.quality_passed += 1
        elif row.quality_status == "warning":
            accumulator.quality_warning += 1
        elif row.quality_status == "failed":
            accumulator.quality_failed += 1

        freshness = evaluate_freshness(
            max_age_hours=row.freshness_max_age_hours,
            source_as_of_at=row.source_as_of_at,
            evaluated_at=evaluated_at,
        )
        if freshness == "fresh":
            accumulator.freshness_fresh += 1
        elif freshness == "stale":
            accumulator.freshness_stale += 1
        elif freshness == "policy_missing":
            accumulator.freshness_policy_missing += 1
        elif freshness == "source_as_of_missing":
            accumulator.freshness_source_as_of_missing += 1
        else:
            accumulator.freshness_future_as_of += 1

        latest_at = row.latest_ingestion_at
        if latest_at is not None and (
            accumulator.latest_ingestion_at is None or latest_at > accumulator.latest_ingestion_at
        ):
            accumulator.latest_ingestion_at = latest_at

    datasets = [
        CompanyDataDatasetCoverage(
            dataset_type=dataset_type,
            contract_versions=contract_versions[dataset_type],
            registered_source_count=accumulator.registered_source_count,
            sources_without_ingestion_count=accumulator.sources_without_ingestion_count,
            latest_ingestion_counts=CompanyDataCoverageIngestionCounts(
                succeeded=accumulator.succeeded,
                succeeded_with_warnings=accumulator.succeeded_with_warnings,
                blocked=accumulator.blocked,
            ),
            latest_quality_counts=CompanyDataCoverageQualityCounts(
                passed=accumulator.quality_passed,
                warning=accumulator.quality_warning,
                failed=accumulator.quality_failed,
            ),
            current_freshness_counts=CompanyDataCoverageFreshnessCounts(
                fresh=accumulator.freshness_fresh,
                stale=accumulator.freshness_stale,
                policy_missing=accumulator.freshness_policy_missing,
                source_as_of_missing=accumulator.freshness_source_as_of_missing,
                future_as_of=accumulator.freshness_future_as_of,
            ),
            latest_ingestion_at=accumulator.latest_ingestion_at,
        )
        for dataset_type, accumulator in accumulators.items()
    ]
    return CompanyDataCoverageResponse(
        tenant_id=tenant_id,
        company_id=access.company.id,
        evaluated_at=evaluated_at,
        note=(
            "Counts describe only registered M12 sources and each source's latest ingestion "
            "for the six supported M4 workforce/project datasets. No registered source means "
            "not tracked in BusinessFlow, not proof that the business has no data. Source-as-of "
            "timestamps are user-reported assertions. This inventory does not establish source "
            "authority, business completeness, accounting correctness, or readiness for any "
            "analysis."
        ),
        datasets=datasets,
    )


def get_source(
    session: Session, user: UserAccount, tenant_id: UUID, source_id: UUID
) -> CompanyDataSourceSummary:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    source = _source_query(session, tenant_id, access.company.id, source_id)
    if source is None:
        raise ApiError(
            404, "company_data_source_not_found", "The company data source was not found."
        )
    latest, successful = _latest_by_source(session, [source])
    return _source_summary(source, latest.get(source.id), successful.get(source.id))


def create_source(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: CompanyDataSourceCreateRequest,
    request_id: UUID,
) -> CompanyDataSourceSummary:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    if body.dataset_type == "customer_commitments":
        raise ApiError(
            409,
            "manual_customer_commitment_source_managed",
            "The manual customer-commitment source is registered by the guided workflow.",
        )
    contract = session.scalar(
        select(CompanyDataContract).where(
            CompanyDataContract.dataset_type == body.dataset_type,
            CompanyDataContract.version == body.contract_version,
        )
    )
    if contract is None:
        raise ApiError(
            422,
            "company_data_contract_not_found",
            "Select a published contract version for this M4 dataset.",
        )
    _contract_snapshot(contract)
    owner_user_id = body.owner_user_id or user.id
    owner_membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == owner_user_id,
            TenantMembership.status == "active",
        )
    )
    if owner_membership is None:
        raise ApiError(
            404,
            "company_data_owner_not_found",
            "The selected source owner is not an active tenant member.",
        )
    duplicate = session.scalar(
        select(CompanyDataSource.id).where(
            CompanyDataSource.tenant_id == tenant_id,
            CompanyDataSource.company_id == access.company.id,
            CompanyDataSource.source_key == body.source_key,
        )
    )
    if duplicate is not None:
        raise ApiError(
            409,
            "company_data_source_key_conflict",
            "A source with this key is already registered in the Company.",
        )
    source = CompanyDataSource(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_key=body.source_key,
        display_name=body.display_name,
        dataset_type=body.dataset_type,
        contract_key=contract.contract_key,
        contract_version=contract.version,
        owner_user_id=owner_user_id,
        registered_by_user_id=user.id,
        freshness_max_age_hours=body.freshness_max_age_hours,
    )
    session.add(source)
    session.flush()
    record_event(
        session,
        "company_data.source_registered",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="company_data_source",
        target_id=source.id,
        request_id=request_id,
        details={
            "source_key": source.source_key,
            "dataset_type": source.dataset_type,
            "contract_key": source.contract_key,
            "contract_version": source.contract_version,
            "owner_user_id": str(source.owner_user_id),
            "freshness_max_age_hours": source.freshness_max_age_hours,
        },
    )
    return _source_summary(source, None, None)


def _m4_issue_counts(session: Session, import_record: DataImport) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    rows = session.scalars(
        select(DataImportRow.issues).where(
            DataImportRow.tenant_id == import_record.tenant_id,
            DataImportRow.company_id == import_record.company_id,
            DataImportRow.import_id == import_record.id,
        )
    ).yield_per(500)
    for raw_issues in rows:
        for issue in raw_issues if isinstance(raw_issues, list) else []:
            if not isinstance(issue, dict):
                continue
            severity = issue.get("severity")
            code = issue.get("code")
            if severity in {"error", "warning"} and isinstance(code, str):
                counts[(severity, code)] += 1
    return counts


def _quality_assessment(
    session: Session,
    import_record: DataImport,
    contract: CompanyDataContract,
) -> QualityAssessment:
    mapping_findings = validate_import_mapping(import_record, contract.definition)
    lineage_count = (
        session.scalar(
            select(func.count())
            .select_from(DataImportRecordLink)
            .where(
                DataImportRecordLink.tenant_id == import_record.tenant_id,
                DataImportRecordLink.company_id == import_record.company_id,
                DataImportRecordLink.import_id == import_record.id,
            )
        )
        or 0
    )
    return assess_quality(
        import_status=import_record.status,
        invalid_rows=import_record.invalid_rows,
        warning_rows=import_record.warning_rows,
        duplicate_rows=import_record.duplicate_rows,
        skipped_rows=import_record.skipped_rows,
        mapping_version=import_record.mapping_version,
        validation_hash=import_record.validation_hash,
        lineage_count=lineage_count,
        mapping_findings=mapping_findings,
        issue_counts=_m4_issue_counts(session, import_record),
    )


def create_ingestion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    body: CompanyDataIngestionCreateRequest,
    request_id: UUID,
) -> CompanyDataIngestionResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    source = session.scalar(
        select(CompanyDataSource).where(
            CompanyDataSource.id == source_id,
            CompanyDataSource.tenant_id == tenant_id,
            CompanyDataSource.company_id == access.company.id,
        )
    )
    if source is None:
        raise ApiError(
            404, "company_data_source_not_found", "The company data source was not found."
        )
    if source.dataset_type not in DATASETS:
        raise ApiError(
            409,
            "manual_source_m4_ingestion_not_supported",
            "Manual customer commitments use guided entry and cannot be promoted as M4 imports.",
        )
    import_record = session.scalar(
        select(DataImport).where(
            DataImport.id == body.import_id,
            DataImport.tenant_id == tenant_id,
            DataImport.company_id == access.company.id,
        )
    )
    if import_record is None:
        raise ApiError(404, "import_not_found", "The M4 import was not found.")
    if import_record.dataset_type != source.dataset_type:
        raise ApiError(
            409,
            "company_data_dataset_mismatch",
            "The M4 import dataset does not match the registered source.",
        )
    if import_record.status not in {"applied", "applied_with_warnings"}:
        raise ApiError(
            409,
            "company_data_import_not_applied",
            "Only an explicitly confirmed and applied M4 import can be recorded as an ingestion.",
        )

    source_as_of_at = body.source_as_of_at
    if source_as_of_at is not None:
        source_as_of_at = source_as_of_at.astimezone(UTC)
    lock_key = f"company_data_ingestion:{tenant_id}:{access.company.id}:{import_record.id}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    prior = session.scalar(
        select(CompanyDataIngestion).where(
            CompanyDataIngestion.tenant_id == tenant_id,
            CompanyDataIngestion.company_id == access.company.id,
            CompanyDataIngestion.import_id == import_record.id,
        )
    )
    if prior is not None:
        if prior.source_id != source.id or prior.source_as_of_at != source_as_of_at:
            raise ApiError(
                409,
                "company_data_import_already_bound",
                "This M4 import is already bound to a different source or as-of assertion.",
            )
        return _response(prior, replayed=True)

    contract = session.scalar(
        select(CompanyDataContract).where(
            CompanyDataContract.contract_key == source.contract_key,
            CompanyDataContract.version == source.contract_version,
        )
    )
    if contract is None:
        raise ApiError(
            409,
            "company_data_contract_unavailable",
            "The source's registered schema contract is unavailable.",
        )
    contract_snapshot = _contract_snapshot(contract)
    now = datetime.now(UTC)
    quality = _quality_assessment(session, import_record, contract)
    freshness = evaluate_freshness(
        max_age_hours=source.freshness_max_age_hours,
        source_as_of_at=source_as_of_at,
        evaluated_at=now,
    )
    validation_hash = import_record.validation_hash
    snapshot: dict[str, object] = {
        "snapshot_schema_version": 1,
        "ingestion_id": "pending",
        "tenant_id": str(tenant_id),
        "company_id": str(access.company.id),
        "source_id": str(source.id),
        "source_key": source.source_key,
        "owner_user_id": str(source.owner_user_id),
        "registered_by_user_id": str(source.registered_by_user_id),
        "import_id": str(import_record.id),
        "dataset_type": import_record.dataset_type,
        "contract_key": contract.contract_key,
        "contract_version": contract.version,
        "contract_hash": contract.definition_hash,
        "contract_snapshot": contract_snapshot,
        "file_hash": import_record.file_hash,
        "validation_hash": validation_hash,
        "mapping_version": import_record.mapping_version,
        "selected_worksheet": import_record.selected_worksheet,
        "field_mapping": import_record.field_mapping,
        "ignored_columns": import_record.ignored_columns,
        "m4_status": import_record.status,
        "m4_applied_at": _iso(import_record.applied_at),
        "m4_counts": {
            "total_rows": import_record.total_rows,
            "valid_rows": import_record.valid_rows,
            "invalid_rows": import_record.invalid_rows,
            "warning_rows": import_record.warning_rows,
            "duplicate_rows": import_record.duplicate_rows,
            "skipped_rows": import_record.skipped_rows,
            "created_rows": import_record.created_rows,
        },
        "ingestion_status": quality.ingestion_status,
        "quality_status": quality.status,
        "quality_findings": list(quality.findings),
        "freshness": {
            "status": freshness,
            "reported_source_as_of_at": _iso(source_as_of_at),
            "max_age_hours": source.freshness_max_age_hours,
            "evaluated_at": _iso(now),
            "basis": "user_reported_source_as_of_and_company_configured_max_age",
        },
        "recorded_at": _iso(now),
    }
    ingestion_id = uuid4()
    snapshot["ingestion_id"] = str(ingestion_id)
    ingestion = CompanyDataIngestion(
        id=ingestion_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        source_id=source.id,
        import_id=import_record.id,
        ingested_by_user_id=user.id,
        contract_key=contract.contract_key,
        contract_version=contract.version,
        ingestion_status=quality.ingestion_status,
        quality_status=quality.status,
        freshness_status=freshness,
        source_as_of_at=source_as_of_at,
        freshness_max_age_hours_snapshot=source.freshness_max_age_hours,
        freshness_evaluated_at=now,
        contract_hash=contract.definition_hash,
        file_hash=import_record.file_hash,
        validation_hash=validation_hash,
        mapping_version=import_record.mapping_version,
        total_rows=import_record.total_rows,
        quality_findings=list(quality.findings),
        contract_snapshot=contract_snapshot,
        snapshot=snapshot,
        snapshot_hash=content_hash(snapshot),
        created_at=now,
    )
    session.add(ingestion)
    session.flush()
    record_event(
        session,
        "company_data.ingestion_recorded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="company_data_ingestion",
        target_id=ingestion.id,
        request_id=request_id,
        outcome="failure" if quality.status == "failed" else "success",
        details={
            "source_id": str(source.id),
            "import_id": str(import_record.id),
            "contract_version": contract.version,
            "file_hash": import_record.file_hash,
            "validation_hash": validation_hash,
            "quality_status": quality.status,
            "freshness_status": freshness,
        },
    )
    return _response(ingestion)


def get_ingestion(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
) -> CompanyDataIngestionResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    source = _source_query(session, tenant_id, access.company.id, source_id)
    if source is None:
        raise ApiError(
            404, "company_data_source_not_found", "The company data source was not found."
        )
    ingestion = session.scalar(
        select(CompanyDataIngestion).where(
            CompanyDataIngestion.id == ingestion_id,
            CompanyDataIngestion.tenant_id == tenant_id,
            CompanyDataIngestion.company_id == access.company.id,
            CompanyDataIngestion.source_id == source.id,
        )
    )
    if ingestion is None:
        raise ApiError(
            404,
            "company_data_ingestion_not_found",
            "The company data ingestion was not found.",
        )
    return _response(ingestion)


def list_ingestions(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    *,
    limit: int,
    offset: int,
) -> CompanyDataIngestionListResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    source = _source_query(session, tenant_id, access.company.id, source_id)
    if source is None:
        raise ApiError(
            404, "company_data_source_not_found", "The company data source was not found."
        )
    filters = (
        CompanyDataIngestion.tenant_id == tenant_id,
        CompanyDataIngestion.company_id == access.company.id,
        CompanyDataIngestion.source_id == source.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(CompanyDataIngestion).where(*filters)) or 0
    )
    rows = session.scalars(
        select(CompanyDataIngestion)
        .where(*filters)
        .order_by(CompanyDataIngestion.created_at.desc(), CompanyDataIngestion.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return CompanyDataIngestionListResponse(
        items=[_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


def list_lineage(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    *,
    limit: int,
    offset: int,
) -> CompanyDataLineagePage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    source = _source_query(session, tenant_id, access.company.id, source_id)
    if source is None:
        raise ApiError(
            404, "company_data_source_not_found", "The company data source was not found."
        )
    ingestion = session.scalar(
        select(CompanyDataIngestion).where(
            CompanyDataIngestion.id == ingestion_id,
            CompanyDataIngestion.tenant_id == tenant_id,
            CompanyDataIngestion.company_id == access.company.id,
            CompanyDataIngestion.source_id == source.id,
        )
    )
    if ingestion is None:
        raise ApiError(
            404,
            "company_data_ingestion_not_found",
            "The company data ingestion was not found.",
        )
    links_filter = (
        DataImportRecordLink.tenant_id == tenant_id,
        DataImportRecordLink.company_id == access.company.id,
        DataImportRecordLink.import_id == ingestion.import_id,
    )
    total = (
        session.scalar(select(func.count()).select_from(DataImportRecordLink).where(*links_filter))
        or 0
    )
    rows = session.execute(
        select(DataImportRecordLink, DataImportRow)
        .join(
            DataImportRow,
            (DataImportRow.tenant_id == DataImportRecordLink.tenant_id)
            & (DataImportRow.company_id == DataImportRecordLink.company_id)
            & (DataImportRow.import_id == DataImportRecordLink.import_id)
            & (DataImportRow.id == DataImportRecordLink.source_row_id),
        )
        .where(*links_filter)
        .order_by(
            DataImportRecordLink.canonical_type,
            DataImportRow.worksheet_name,
            DataImportRow.row_number,
            DataImportRecordLink.id,
        )
        .limit(limit)
        .offset(offset)
    )
    items = [
        CompanyDataLineageItem(
            import_id=link.import_id,
            source_row_id=row.id,
            worksheet_name=row.worksheet_name,
            row_number=row.row_number,
            row_hash=row.row_hash,
            row_status=row.row_status,
            canonical_type=link.canonical_type,
            canonical_id=link.canonical_id,
            canonical_fingerprint=link.canonical_fingerprint,
        )
        for link, row in rows
    ]
    return CompanyDataLineagePage(items=items, total=total, limit=limit, offset=offset)


def _company_data_evidence_payload(
    ingestion: CompanyDataIngestion,
    source: CompanyDataSource,
    contract: CompanyDataContract,
    import_record: DataImport,
    *,
    evaluated_at: datetime,
) -> dict[str, object]:
    _verify_ingestion_snapshot(ingestion)
    if (
        source.dataset_type != import_record.dataset_type
        or source.contract_key != ingestion.contract_key
        or source.contract_version != ingestion.contract_version
        or contract.dataset_type != import_record.dataset_type
        or ingestion.file_hash != import_record.file_hash
        or ingestion.validation_hash != import_record.validation_hash
        or ingestion.mapping_version != import_record.mapping_version
        or ingestion.contract_hash != contract.definition_hash
        or ingestion.contract_snapshot.get("definition") != contract.definition
        or ingestion.contract_snapshot.get("definition_hash") != contract.definition_hash
    ):
        raise ApiError(
            409,
            "company_data_binding_integrity_error",
            "The registered company-data binding does not match the selected M4 import.",
        )

    return {
        "source_id": str(source.id),
        "source_key": source.source_key,
        "dataset_type": source.dataset_type,
        "ingestion_id": str(ingestion.id),
        "import_id": str(ingestion.import_id),
        "contract_key": ingestion.contract_key,
        "contract_version": ingestion.contract_version,
        "contract_hash": ingestion.contract_hash,
        "snapshot_hash": ingestion.snapshot_hash,
        "ingestion_status": ingestion.ingestion_status,
        "quality_status": ingestion.quality_status,
        "freshness_status": _current_freshness(ingestion, now=evaluated_at),
        "current_freshness_status": _current_freshness(ingestion),
        "recorded_freshness_status": ingestion.freshness_status,
        "source_as_of_at": _iso(ingestion.source_as_of_at),
        "freshness_max_age_hours": ingestion.freshness_max_age_hours_snapshot,
        "freshness_evaluated_at": _iso(evaluated_at),
        "ingestion_freshness_evaluated_at": _iso(ingestion.freshness_evaluated_at),
        "file_hash": ingestion.file_hash,
        "validation_hash": ingestion.validation_hash,
        "mapping_version": ingestion.mapping_version,
        "quality_findings": ingestion.quality_findings,
    }


def company_data_evidence_reference(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    import_record: DataImport,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, object] | None:
    """Return verified M12 evidence bound to one M4 import at a pinned freshness time."""
    evidence = company_data_evidence_references(
        session,
        tenant_id,
        company_id,
        {import_record.id: import_record},
        evaluated_at=evaluated_at,
    )
    return evidence.get(import_record.id)


def company_data_evidence_references(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    import_records: dict[UUID, DataImport],
    *,
    evaluated_at: datetime | None = None,
) -> dict[UUID, dict[str, object]]:
    """Batch-verify M12 evidence for selected, tenant/company-scoped M4 imports."""
    if not import_records:
        return {}
    if any(
        record.tenant_id != tenant_id or record.company_id != company_id
        for record in import_records.values()
    ):
        raise ApiError(
            409,
            "company_data_binding_integrity_error",
            "The selected M4 imports do not match the authorized Company scope.",
        )
    evaluated_at = evaluated_at or datetime.now(UTC)
    rows = session.execute(
        select(CompanyDataIngestion, CompanyDataSource, CompanyDataContract)
        .join(
            CompanyDataSource,
            (CompanyDataSource.id == CompanyDataIngestion.source_id)
            & (CompanyDataSource.tenant_id == CompanyDataIngestion.tenant_id)
            & (CompanyDataSource.company_id == CompanyDataIngestion.company_id),
        )
        .join(
            CompanyDataContract,
            (CompanyDataContract.contract_key == CompanyDataIngestion.contract_key)
            & (CompanyDataContract.version == CompanyDataIngestion.contract_version),
        )
        .where(
            CompanyDataIngestion.tenant_id == tenant_id,
            CompanyDataIngestion.company_id == company_id,
            CompanyDataIngestion.import_id.in_(list(import_records)),
        )
    ).all()
    return {
        ingestion.import_id: _company_data_evidence_payload(
            ingestion,
            source,
            contract,
            import_records[ingestion.import_id],
            evaluated_at=evaluated_at,
        )
        for ingestion, source, contract in rows
    }


def m10_company_data_reference(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    import_record: DataImport,
    *,
    freshness_evaluated_at: datetime | None = None,
    require_current_freshness: bool = True,
) -> dict[str, object] | None:
    """Return a verified M10 reference when an M4 import was explicitly registered in M12."""
    freshness_evaluated_at = freshness_evaluated_at or datetime.now(UTC)
    evidence = company_data_evidence_reference(
        session,
        tenant_id,
        company_id,
        import_record,
        evaluated_at=freshness_evaluated_at,
    )
    if evidence is None:
        return None
    warning_allowed = evidence["quality_status"] == "warning" and m10_warning_codes_are_compatible(
        cast(str, import_record.dataset_type),
        cast(list[dict[str, object]], evidence["quality_findings"]),
    )
    live_freshness = evidence["current_freshness_status"]
    if (
        evidence["freshness_status"] != "fresh"
        or (require_current_freshness and live_freshness != "fresh")
        or evidence["quality_status"] not in {"passed", "warning"}
        or (evidence["quality_status"] == "warning" and not warning_allowed)
        or evidence["ingestion_status"] == "blocked"
    ):
        raise ApiError(
            409,
            "company_data_not_eligible_for_m10",
            (
                "A registered M10 source must have acceptable quality findings and "
                "explicit fresh evidence."
            ),
        )
    return {
        "source_id": evidence["source_id"],
        "source_key": evidence["source_key"],
        "ingestion_id": evidence["ingestion_id"],
        "contract_key": evidence["contract_key"],
        "contract_version": evidence["contract_version"],
        "contract_hash": evidence["contract_hash"],
        "snapshot_hash": evidence["snapshot_hash"],
        "quality_status": evidence["quality_status"],
        "freshness_status": evidence["freshness_status"],
        "ingestion_freshness_status": evidence["recorded_freshness_status"],
        "source_as_of_at": evidence["source_as_of_at"],
        "freshness_max_age_hours": evidence["freshness_max_age_hours"],
        "freshness_evaluated_at": evidence["freshness_evaluated_at"],
        "ingestion_freshness_evaluated_at": evidence["ingestion_freshness_evaluated_at"],
        "quality_findings": evidence["quality_findings"],
    }
