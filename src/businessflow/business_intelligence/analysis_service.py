from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.business_intelligence.engine import (
    BI_CALCULATION_VERSION,
    BI_INPUT_SCHEMA_VERSION,
    BI_RESULT_SCHEMA_VERSION,
    calculate_saved_analysis,
)
from businessflow.business_intelligence.models import (
    BusinessCompanySemanticConfigVersion,
    BusinessCompanySemanticSourcePrecedence,
    BusinessIntelligenceAnalysisRun,
    BusinessMetricDefinitionVersion,
)
from businessflow.business_intelligence.registry import (
    METRIC_REGISTRY_VERSION,
    SYSTEM_METRIC_BY_KEY,
    SYSTEM_METRICS,
    system_metric_definition_hash,
    system_metric_payloads,
)
from businessflow.business_intelligence.registry_service import (
    _config_precedence_rows,
    dependency_by_key,
    get_scoped_configuration,
    get_scoped_metric_definition,
    metric_definition_requires_finance_read,
)
from businessflow.business_intelligence.registry_service import (
    definition_requires_finance_read as _definition_requires_finance_read,
)
from businessflow.business_intelligence.registry_service import (
    definition_snapshot as metric_definition_snapshot,
)
from businessflow.business_intelligence.registry_service import (
    has_finance_read_access as _has_finance_read_access,
)
from businessflow.business_intelligence.schemas import (
    MetricAnalysisCreateRequest,
    MetricAnalysisPage,
    MetricAnalysisReplayResponse,
    MetricAnalysisResponse,
    MetricDependencyAnalysisInput,
    MetricEvidencePage,
    MetricEvidenceReference,
    MetricReadinessCheck,
    MetricReadinessResponse,
)
from businessflow.business_semantic.models import BusinessSemanticRelationshipVersion
from businessflow.business_semantic.service import (
    _current_evidence_mappings,
    _is_current_relationship,
)
from businessflow.company_data.models import CompanyDataSource
from businessflow.config import Settings
from businessflow.core_financials.models import CoreFinancialRun
from businessflow.core_financials.service import (
    get_core_financial_run,
    verify_core_financial_run,
)
from businessflow.errors import ApiError
from businessflow.metrics.insight_service import _validate_metric_run
from businessflow.metrics.service import get_metric_run
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.universal_data.catalog import CANONICAL_ENTITIES
from businessflow.universal_data.models import (
    CanonicalBusinessRecord,
    CanonicalBusinessRecordRevision,
    CompanyDataSourceProfileVersion,
    UniversalIngestion,
    UniversalIngestionRow,
    UniversalSourceSnapshot,
)

MAX_BI_SOURCE_RECORDS = 10_000
MAX_BI_EVIDENCE_REFERENCES = 50_000


def _error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _error(
            409,
            "business_intelligence_evidence_invalid",
            "Saved analysis evidence failed an integrity check.",
        )
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _error(
            409,
            "business_intelligence_evidence_invalid",
            "Saved analysis evidence failed an integrity check.",
        )
    return value


def _company_scope(session: Session, user: UserAccount, tenant_id: UUID, *, write: bool = False):
    permission = "company_context:write" if write else "company_context:read"
    return require_company_context_access(session, user, tenant_id, permission)


def _system_definition(metric_key: str) -> dict[str, object] | None:
    payload = next(
        (item for item in system_metric_payloads() if item.get("metric_key") == metric_key), None
    )
    if payload is None:
        return None
    result = dict(payload)
    result["calculation_key"] = _dict(result.get("calculation")).get("kind")
    result["filters"] = []
    result["exclusions"] = []
    result["policy"] = {
        "currency_behavior": "single_currency_only"
        if result["calculation_key"] == "core_financial_measure"
        else "not_applicable",
        "source_authority_requirement": "no_authority_claim",
    }
    result["aggregation_method"] = "source_defined"
    result["precision_scale"] = 2
    result["rounding_mode"] = "half_even"
    result["period_boundary"] = "calendar_period"
    result["required_relationships"] = []
    result["dependencies"] = []
    result["required_source_datasets"] = []
    result["required_fields"] = []
    if result["calculation_key"] == "m5_metric":
        metric_id = _dict(result.get("calculation")).get("metric_id")
        is_weekly = metric_id != "recorded_skill_inventory_coverage"
        result["dimensions"] = ["week"] if is_weekly else []
        result["time_granularity"] = "week" if is_weekly else "none"
    else:
        result["dimensions"] = []
        result["time_granularity"] = "none"
    system_metric = SYSTEM_METRIC_BY_KEY.get(metric_key)
    if system_metric is None:
        raise _error(
            409,
            "system_metric_registry_invalid",
            "The requested system metric is not present in the versioned registry.",
        )
    result["definition_hash"] = system_metric_definition_hash(system_metric)
    return result


def _resolve_definition(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    metric_key: str,
    version: int | None,
) -> tuple[dict[str, object], BusinessMetricDefinitionVersion | None, str, int | None, str | None]:
    system = _system_definition(metric_key)
    if system is not None:
        if version is not None:
            raise _error(
                422,
                "system_metric_version_invalid",
                "System metric versions are pinned by the registry catalog.",
            )
        return (
            system,
            None,
            str(system["definition_hash"]),
            None,
            str(system["version"]),
        )
    row = get_scoped_metric_definition(
        session,
        tenant_id=tenant_id,
        company_id=company_id,
        metric_key=metric_key,
        version=version,
    )
    if row is None or row.status != "published":
        raise _error(
            404,
            "published_metric_definition_not_found",
            "A published metric definition with the requested key and version was not found.",
        )
    snapshot = metric_definition_snapshot(row)
    return snapshot, row, row.definition_hash, row.version, None


def _configuration_snapshot(
    session: Session, row: BusinessCompanySemanticConfigVersion
) -> tuple[dict[str, object], dict[str, object]]:
    source_snapshot = [
        {
            "dataset_type": source.dataset_type,
            "source_id": str(source.id),
            "source_name": source.display_name,
            "source_profile_version_id": str(profile.id),
            "source_profile_version": profile.version,
            "source_status": profile.source_status,
            "authority_classification": profile.authority_classification,
            "freshness_max_age_hours": profile.freshness_max_age_hours,
            "priority": precedence.priority,
        }
        for precedence, source, profile in _config_precedence_rows(session, row)
    ]
    payload = {
        "company_id": str(row.company_id),
        "base_currency": row.base_currency,
        "timezone_name": row.timezone_name,
        "fiscal_year_start_month": row.fiscal_year_start_month,
        "fiscal_year_label": row.fiscal_year_label,
        "week_start_day": row.week_start_day,
        "fallback_policy": row.fallback_policy,
        "source_precedence": source_snapshot,
        "semantic_policy": row.semantic_policy,
    }
    if content_hash(payload) != row.configuration_hash:
        raise _error(
            409,
            "company_semantic_config_integrity_error",
            "The published Company configuration failed an integrity check.",
        )
    snapshot = {
        **payload,
        "configuration_version_id": str(row.id),
        "configuration_version": row.version,
        "configuration_hash": row.configuration_hash,
    }
    return snapshot, payload


def _source_last_ingestion(
    session: Session, tenant_id: UUID, company_id: UUID, source_id: UUID
) -> tuple[UniversalIngestion | None, UniversalIngestion | None]:
    latest = session.scalar(
        select(UniversalIngestion)
        .where(
            UniversalIngestion.tenant_id == tenant_id,
            UniversalIngestion.company_id == company_id,
            UniversalIngestion.source_id == source_id,
        )
        .order_by(UniversalIngestion.created_at.desc(), UniversalIngestion.id.desc())
        .limit(1)
    )
    latest_promoted = session.scalar(
        select(UniversalIngestion)
        .where(
            UniversalIngestion.tenant_id == tenant_id,
            UniversalIngestion.company_id == company_id,
            UniversalIngestion.source_id == source_id,
            UniversalIngestion.status == "promoted",
        )
        .order_by(UniversalIngestion.promoted_at.desc(), UniversalIngestion.id.desc())
        .limit(1)
    )
    return latest, latest_promoted


def _load_source_records(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    entity_type: str,
    period_start: str | None,
    period_end: str | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
    statement = (
        select(
            CanonicalBusinessRecord,
            CanonicalBusinessRecordRevision,
            UniversalIngestion,
            UniversalSourceSnapshot,
            UniversalIngestionRow,
        )
        .join(
            CanonicalBusinessRecordRevision,
            and_(
                CanonicalBusinessRecordRevision.tenant_id == CanonicalBusinessRecord.tenant_id,
                CanonicalBusinessRecordRevision.company_id == CanonicalBusinessRecord.company_id,
                CanonicalBusinessRecordRevision.record_id == CanonicalBusinessRecord.id,
                CanonicalBusinessRecordRevision.revision
                == CanonicalBusinessRecord.current_revision,
            ),
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
            UniversalIngestionRow,
            and_(
                UniversalIngestionRow.tenant_id == CanonicalBusinessRecordRevision.tenant_id,
                UniversalIngestionRow.company_id == CanonicalBusinessRecordRevision.company_id,
                UniversalIngestionRow.ingestion_id == CanonicalBusinessRecordRevision.ingestion_id,
                UniversalIngestionRow.id == CanonicalBusinessRecordRevision.source_row_id,
            ),
        )
        .where(
            CanonicalBusinessRecord.tenant_id == tenant_id,
            CanonicalBusinessRecord.company_id == company_id,
            CanonicalBusinessRecord.source_id == source_id,
            CanonicalBusinessRecord.entity_type == entity_type,
            UniversalIngestion.status == "promoted",
        )
        .order_by(CanonicalBusinessRecord.id, CanonicalBusinessRecord.current_revision)
        .limit(MAX_BI_SOURCE_RECORDS + 1)
    )
    date_field = {
        "sales_transaction": "transaction_date",
        "expense": "expense_date",
        "business_activity": "activity_date",
        "operational_record": "record_date",
    }.get(entity_type)
    if date_field is not None and period_start is not None and period_end is not None:
        source_date = CanonicalBusinessRecordRevision.attributes.op("->>")(date_field)
        statement = statement.where(source_date >= period_start, source_date <= period_end)
    rows = list(session.execute(statement).all())
    if len(rows) > MAX_BI_SOURCE_RECORDS:
        return [], [], True
    records: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    for record, revision, ingestion, snapshot, source_row in rows:
        lineage = {
            "source_id": str(record.source_id),
            "record_id": str(record.id),
            "revision": revision.revision,
            "ingestion_id": str(ingestion.id),
            "source_snapshot_id": str(snapshot.id),
            "source_row_id": str(source_row.id),
            "mapping_profile_version_id": str(revision.mapping_profile_version_id),
            "source_profile_version_id": str(revision.source_profile_version_id),
            "row_hash": source_row.row_hash,
            "attributes_hash": revision.attributes_hash,
        }
        evidence.append(lineage)
        records.append(
            {
                **lineage,
                "entity_type": record.entity_type,
                "attributes": revision.attributes,
            }
        )
    return records, evidence, False


def _count_source_records(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    source_id: UUID,
    entity_type: str,
    period_start: str | None,
    period_end: str | None,
) -> int:
    statement = (
        select(CanonicalBusinessRecord.id)
        .join(
            CanonicalBusinessRecordRevision,
            and_(
                CanonicalBusinessRecordRevision.tenant_id == CanonicalBusinessRecord.tenant_id,
                CanonicalBusinessRecordRevision.company_id == CanonicalBusinessRecord.company_id,
                CanonicalBusinessRecordRevision.record_id == CanonicalBusinessRecord.id,
                CanonicalBusinessRecordRevision.revision
                == CanonicalBusinessRecord.current_revision,
            ),
        )
        .join(
            UniversalIngestion,
            and_(
                UniversalIngestion.tenant_id == CanonicalBusinessRecordRevision.tenant_id,
                UniversalIngestion.company_id == CanonicalBusinessRecordRevision.company_id,
                UniversalIngestion.id == CanonicalBusinessRecordRevision.ingestion_id,
            ),
        )
        .where(
            CanonicalBusinessRecord.tenant_id == tenant_id,
            CanonicalBusinessRecord.company_id == company_id,
            CanonicalBusinessRecord.source_id == source_id,
            CanonicalBusinessRecord.entity_type == entity_type,
            UniversalIngestion.status == "promoted",
        )
    )
    date_field = {
        "sales_transaction": "transaction_date",
        "expense": "expense_date",
        "business_activity": "activity_date",
        "operational_record": "record_date",
    }.get(entity_type)
    if date_field is not None and period_start is not None and period_end is not None:
        source_date = CanonicalBusinessRecordRevision.attributes.op("->>")(date_field)
        statement = statement.where(source_date >= period_start, source_date <= period_end)
    bounded_records = statement.limit(MAX_BI_SOURCE_RECORDS + 1).subquery()
    return session.scalar(select(func.count()).select_from(bounded_records)) or 0


def _source_authority_requirement_failure(
    authority_requirement: str,
    pinned_classification: str,
    current_classification: str,
) -> tuple[str, str] | None:
    if authority_requirement == "no_authority_claim":
        return None
    if authority_requirement == "owner_asserted_primary":
        if (
            pinned_classification == "owner_asserted_primary"
            and current_classification == "owner_asserted_primary"
        ):
            return None
        return (
            "source_authority_requirement_unsatisfied",
            "The metric requires an owner-asserted primary source classification in both the "
            "pinned and current source profiles; this classification remains an assertion only.",
        )
    return (
        "source_authority_requirement_unsupported",
        "This metric requests a source-authority requirement that Business Intelligence cannot "
        "verify; source classification and precedence metadata are not independent policy review.",
    )


def _source_candidate_state(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    precedence: BusinessCompanySemanticSourcePrecedence,
    source: CompanyDataSource,
    pinned_profile: CompanyDataSourceProfileVersion,
    entity_type: str,
    period_start: str | None,
    period_end: str | None,
    authority_requirement: str,
    now: datetime,
) -> tuple[dict[str, object], bool]:
    latest_profile = session.scalar(
        select(CompanyDataSourceProfileVersion)
        .where(
            CompanyDataSourceProfileVersion.tenant_id == tenant_id,
            CompanyDataSourceProfileVersion.company_id == company_id,
            CompanyDataSourceProfileVersion.source_id == source.id,
        )
        .order_by(CompanyDataSourceProfileVersion.version.desc())
        .limit(1)
    )
    latest_ingestion, latest_promoted = _source_last_ingestion(
        session, tenant_id, company_id, source.id
    )
    warnings: list[dict[str, object]] = []
    blockers: list[dict[str, object]] = []
    status = "ready"
    if pinned_profile.source_status != "enabled":
        status = "blocked"
        blockers.append(
            {
                "check_key": "source_disabled",
                "message": "The source profile pinned by this configuration is disabled.",
            }
        )
    if latest_profile is not None and latest_profile.id != pinned_profile.id:
        warnings.append(
            {
                "check_key": "source_profile_version_changed",
                "message": (
                    "A newer source profile exists; publish a new Company configuration to pin it."
                ),
            }
        )
        if latest_profile.source_status != "enabled":
            status = "blocked"
            blockers.append(
                {
                    "check_key": "current_source_disabled",
                    "message": (
                        "The latest source profile is disabled; update Company configuration "
                        "before analysis."
                    ),
                }
            )
    if latest_promoted is None:
        status = "blocked"
        blockers.append(
            {
                "check_key": "no_promoted_ingestion",
                "message": (
                    "Promote a validated Universal Data ingestion before analyzing this source."
                ),
            }
        )
        last_promoted_at = None
    else:
        last_promoted_at = latest_promoted.promoted_at
        if latest_ingestion is not None and latest_ingestion.id != latest_promoted.id:
            warnings.append(
                {
                    "check_key": "newer_ingestion_not_promoted",
                    "message": (
                        "A newer ingestion is not promoted; analysis uses the last "
                        "promoted canonical records."
                    ),
                }
            )
        freshness_limit = pinned_profile.freshness_max_age_hours
        if freshness_limit is None:
            warnings.append(
                {
                    "check_key": "freshness_policy_unset",
                    "message": (
                        "No source freshness threshold is configured; freshness remains unknown."
                    ),
                }
            )
        elif (
            last_promoted_at is None
            or (now - last_promoted_at).total_seconds() > freshness_limit * 3600
        ):
            status = "blocked"
            blockers.append(
                {
                    "check_key": "source_data_stale",
                    "message": (
                        "The last promoted Universal Data ingestion is older than the "
                        "pinned freshness threshold."
                    ),
                }
            )
    record_count = _count_source_records(
        session,
        tenant_id=tenant_id,
        company_id=company_id,
        source_id=source.id,
        entity_type=entity_type,
        period_start=period_start,
        period_end=period_end,
    )
    if record_count > MAX_BI_SOURCE_RECORDS:
        status = "blocked"
        blockers.append(
            {
                "check_key": "source_record_limit_exceeded",
                "message": (
                    "This analysis exceeds the bounded 10,000-record input limit; "
                    "narrow the period or metric scope."
                ),
            }
        )
    elif record_count == 0:
        status = "blocked"
        blockers.append(
            {
                "check_key": "no_canonical_records",
                "message": (
                    f"No promoted canonical {entity_type} records are available from this source."
                ),
            }
        )
    current_authority = (
        latest_profile.authority_classification
        if latest_profile is not None
        else pinned_profile.authority_classification
    )
    authority_failure = _source_authority_requirement_failure(
        authority_requirement,
        pinned_profile.authority_classification,
        current_authority,
    )
    if authority_failure is not None:
        status = "blocked"
        blockers.append({"check_key": authority_failure[0], "message": authority_failure[1]})
    if pinned_profile.authority_classification == "unclassified":
        warnings.append(
            {
                "check_key": "source_authority_unclassified",
                "message": (
                    "This source is not classified as an authoritative source; results "
                    "are source observations only."
                ),
            }
        )
    candidate = {
        "dataset_type": precedence.dataset_type,
        "source_id": str(source.id),
        "source_name": source.display_name,
        "source_profile_version_id": str(pinned_profile.id),
        "source_profile_version": pinned_profile.version,
        "source_profile_status": pinned_profile.source_status,
        "authority_classification": pinned_profile.authority_classification,
        "freshness_max_age_hours": pinned_profile.freshness_max_age_hours,
        "last_promoted_ingestion_id": str(latest_promoted.id)
        if latest_promoted is not None
        else None,
        "last_promoted_at": last_promoted_at.isoformat() if last_promoted_at is not None else None,
        "latest_ingestion_id": str(latest_ingestion.id) if latest_ingestion is not None else None,
        "latest_ingestion_status": latest_ingestion.status
        if latest_ingestion is not None
        else None,
        "priority": precedence.priority,
        "status": status,
        "record_count": record_count,
        "reason_keys": [str(item["check_key"]) for item in blockers + warnings],
    }
    return candidate, status == "ready"


def _universal_input_snapshot(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    definition: dict[str, object],
    config_row: BusinessCompanySemanticConfigVersion,
    period_start: str | None,
    period_end: str | None,
) -> tuple[dict[str, object], list[MetricReadinessCheck], list[str], list[str]]:
    calculation = _dict(definition.get("calculation"))
    policy = _dict(definition.get("policy", {}))
    entity_type = str(calculation.get("entity_type"))
    policy_blockers = (
        ["reconciliation_policy_unsupported"]
        if policy.get("reconciliation_required") is True
        else []
    )
    policy_warnings = (
        ["completeness_requirements_not_machine_evaluated"]
        if _list(definition.get("completeness_requirements", []))
        else []
    )
    check_model = CANONICAL_ENTITIES.get(entity_type)
    if check_model is None:
        blocker = "metric_entity_type_unsupported"
        return (
            {
                "records": [],
                "evidence": [],
                "blockers": sorted({blocker, *policy_blockers}),
                "warnings": policy_warnings,
            },
            [],
            sorted({blocker, *policy_blockers}),
            policy_warnings,
        )
    date_entities = {"sales_transaction", "expense", "business_activity", "operational_record"}
    if (period_start is not None or period_end is not None) and entity_type not in date_entities:
        blocker = "period_filter_unsupported_for_entity"
        return (
            {
                "records": [],
                "evidence": [],
                "blockers": sorted({blocker, *policy_blockers}),
                "warnings": policy_warnings,
                "source_selection": None,
            },
            [
                MetricReadinessCheck(
                    check_key=blocker,
                    status="blocked",
                    message="Choose a date-bearing canonical entity or remove the period filter.",
                )
            ],
            sorted({blocker, *policy_blockers}),
            policy_warnings,
        )
    precedence_rows = [
        item
        for item in _config_precedence_rows(session, config_row)
        if item[0].dataset_type == "universal_records"
    ]
    if not precedence_rows:
        blocker = "universal_source_precedence_missing"
        return (
            {
                "records": [],
                "evidence": [],
                "blockers": sorted({blocker, *policy_blockers}),
                "warnings": policy_warnings,
                "source_selection": None,
            },
            [
                MetricReadinessCheck(
                    check_key=blocker,
                    status="blocked",
                    message=(
                        "Configure at least one Universal Data source in Company source "
                        "precedence before using this metric."
                    ),
                )
            ],
            sorted({blocker, *policy_blockers}),
            policy_warnings,
        )

    now = datetime.now(UTC)
    candidates: list[tuple[dict[str, object], bool]] = []
    for precedence, source, profile in precedence_rows:
        candidates.append(
            _source_candidate_state(
                session,
                tenant_id=tenant_id,
                company_id=company_id,
                precedence=precedence,
                source=source,
                pinned_profile=profile,
                entity_type=entity_type,
                period_start=period_start,
                period_end=period_end,
                authority_requirement=str(
                    _dict(definition.get("policy", {})).get(
                        "source_authority_requirement", "no_authority_claim"
                    )
                ),
                now=now,
            )
        )
    selected_index: int | None = None
    if config_row.fallback_policy == "strict_primary":
        if candidates and candidates[0][1]:
            selected_index = 0
    else:
        selected_index = next(
            (index for index, candidate in enumerate(candidates) if candidate[1]), None
        )

    blockers: list[str] = list(policy_blockers)
    warnings: list[str] = list(policy_warnings)
    checks: list[MetricReadinessCheck] = []
    non_blocking_source_reasons = {
        "source_profile_version_changed",
        "newer_ingestion_not_promoted",
        "freshness_policy_unset",
        "source_authority_unclassified",
    }
    for index, (candidate, ready) in enumerate(candidates):
        candidate_status = str(candidate["status"])
        reason_keys = cast(list[str], candidate["reason_keys"])
        source_warning_reasons = sorted(non_blocking_source_reasons.intersection(reason_keys))
        checks.append(
            MetricReadinessCheck(
                check_key=f"source:{candidate['source_id']}",
                status=(
                    "warning"
                    if ready and source_warning_reasons
                    else "ready"
                    if ready
                    else "blocked"
                ),
                message=(
                    f"{candidate['source_name']} is eligible at precedence {candidate['priority']} "
                    f"with warnings: {', '.join(source_warning_reasons)}."
                    if ready and source_warning_reasons
                    else (
                        f"{candidate['source_name']} is eligible at precedence "
                        f"{candidate['priority']}."
                    )
                    if ready
                    else f"{candidate['source_name']} is not ready: {', '.join(reason_keys)}."
                ),
                evidence={
                    "source_id": candidate["source_id"],
                    "source_profile_version_id": candidate["source_profile_version_id"],
                    "source_profile_version": candidate["source_profile_version"],
                    "priority": candidate["priority"],
                    "status": candidate_status,
                    "record_count": candidate["record_count"],
                    "last_promoted_ingestion_id": candidate["last_promoted_ingestion_id"],
                },
            )
        )
        relevant_candidate = (
            index == selected_index
            if selected_index is not None
            else config_row.fallback_policy == "next_ready_source" or index == 0
        )
        if not relevant_candidate:
            continue
        for reason in reason_keys:
            if reason in non_blocking_source_reasons:
                warnings.append(reason)
            elif not ready:
                blockers.append(reason)

    if selected_index is None:
        blockers.append(
            "primary_universal_source_not_ready"
            if config_row.fallback_policy == "strict_primary"
            else "no_ready_universal_source"
        )
        selected_records: list[dict[str, object]] = []
        selected_evidence: list[dict[str, object]] = []
        selected_source: dict[str, object] | None = None
    else:
        selected_source, _ready = candidates[selected_index]
        _precedence, selected_company_source, _pinned_profile = precedence_rows[selected_index]
        selected_records, selected_evidence, exceeded_limit = _load_source_records(
            session,
            tenant_id=tenant_id,
            company_id=company_id,
            source_id=selected_company_source.id,
            entity_type=entity_type,
            period_start=period_start,
            period_end=period_end,
        )
        if exceeded_limit or not selected_records:
            raise _error(
                409,
                "business_intelligence_source_snapshot_changed",
                "The selected source changed while its bounded input snapshot was being read; "
                "retry readiness and analysis.",
            )
        if selected_index > 0:
            warnings.append("source_precedence_fallback_used")
        if selected_source.get("authority_classification") == "unclassified":
            warnings.append("source_authority_unclassified")

    if len(selected_evidence) > MAX_BI_EVIDENCE_REFERENCES:
        blockers.append("analysis_evidence_limit_exceeded")
        selected_records = []
        selected_evidence = []
    if selected_records:
        required_fields = [_dict(item) for item in _list(definition.get("required_fields", []))]
        if required_fields:
            for item in required_fields:
                if item.get("entity_type") != entity_type:
                    blockers.append("required_field_entity_mismatch")
                    continue
                field = str(item.get("field"))
                missing_count = sum(
                    _dict(record.get("attributes")).get(field) in {None, ""}
                    for record in selected_records
                )
                if missing_count:
                    warnings.append(f"required_field_missing:{field}")
                    checks.append(
                        MetricReadinessCheck(
                            check_key=f"field:{field}",
                            status="warning",
                            message=(
                                f"{missing_count} of {len(selected_records)} current records "
                                f"do not contain the required {field} field."
                            ),
                            evidence={
                                "missing_records": missing_count,
                                "record_count": len(selected_records),
                            },
                        )
                    )
                else:
                    checks.append(
                        MetricReadinessCheck(
                            check_key=f"field:{field}",
                            status="ready",
                            message=f"All {len(selected_records)} current records contain {field}.",
                            evidence={"record_count": len(selected_records)},
                        )
                    )
    input_data = {
        "records": selected_records,
        "evidence": selected_evidence,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "source_selection": selected_source,
        "candidate_sources": [item[0] for item in candidates],
        "semantic_relationships": [],
    }
    return input_data, checks, sorted(set(blockers)), sorted(set(warnings))


def _relationship_snapshots(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    relationship_types: list[str],
    max_evidence: int = MAX_BI_EVIDENCE_REFERENCES,
) -> tuple[list[dict[str, object]], list[str]]:
    snapshots: list[dict[str, object]] = []
    missing: list[str] = []
    for relationship_type in sorted(set(relationship_types)):
        latest_versions = (
            select(
                BusinessSemanticRelationshipVersion.relationship_key_hash.label("key_hash"),
                func.max(BusinessSemanticRelationshipVersion.version).label("latest_version"),
            )
            .where(
                BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
                BusinessSemanticRelationshipVersion.company_id == company_id,
                BusinessSemanticRelationshipVersion.relationship_type == relationship_type,
            )
            .group_by(BusinessSemanticRelationshipVersion.relationship_key_hash)
            .subquery()
        )
        rows = list(
            session.scalars(
                select(BusinessSemanticRelationshipVersion)
                .join(
                    latest_versions,
                    and_(
                        BusinessSemanticRelationshipVersion.relationship_key_hash
                        == latest_versions.c.key_hash,
                        BusinessSemanticRelationshipVersion.version
                        == latest_versions.c.latest_version,
                    ),
                )
                .where(
                    BusinessSemanticRelationshipVersion.tenant_id == tenant_id,
                    BusinessSemanticRelationshipVersion.company_id == company_id,
                    BusinessSemanticRelationshipVersion.relationship_type == relationship_type,
                )
                .order_by(BusinessSemanticRelationshipVersion.relationship_key_hash)
                .limit(max_evidence + 1)
            )
        )
        if len(rows) > max_evidence:
            raise _error(
                409,
                "business_intelligence_evidence_limit_exceeded",
                "Required semantic relationship evidence exceeds the bounded analysis limit.",
            )
        usable = [
            row
            for row in rows
            if _is_current_relationship(session, tenant_id, company_id, row)
            and _current_evidence_mappings(session, tenant_id, company_id, row)
        ]
        if not usable:
            missing.append(relationship_type)
        else:
            snapshots.extend(
                {
                    "relationship_version_id": str(row.id),
                    "relationship_type": row.relationship_type,
                    "version": row.version,
                    "status": row.status,
                    "evidence_kind": row.evidence_kind,
                    "subject_entity_id": str(row.subject_entity_id),
                    "object_entity_id": str(row.object_entity_id),
                    "subject_mapping_version_id": str(row.subject_mapping_version_id),
                    "object_mapping_version_id": str(row.object_mapping_version_id),
                }
                for row in usable
            )
        if len(snapshots) > max_evidence:
            raise _error(
                409,
                "business_intelligence_evidence_limit_exceeded",
                "Required semantic relationship evidence exceeds the bounded analysis limit.",
            )
    return snapshots, missing


def _readiness_response(
    *,
    definition: dict[str, object],
    definition_hash: str,
    metric_version: int | None,
    configuration_version: int | None,
    calculation_supported: bool,
    checks: list[MetricReadinessCheck],
    blockers: list[str],
    warnings: list[str],
) -> MetricReadinessResponse:
    if not calculation_supported and "calculation_not_supported" not in blockers:
        blockers.append("calculation_not_supported")
    if blockers:
        status: str = "blocked"
    elif warnings:
        status = "conditional"
    elif any(check.status == "unknown" for check in checks):
        status = "unknown"
    else:
        status = "ready"
    return MetricReadinessResponse(
        metric_key=str(definition.get("metric_key")),
        metric_version=metric_version,
        definition_hash=definition_hash,
        configuration_version=configuration_version,
        readiness_status=cast(Any, status),
        calculation_supported=calculation_supported,
        checks=checks,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        limitations=[str(item) for item in _list(definition.get("limitations", []))],
    )


def assess_metric_readiness(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    metric_key: str,
    *,
    version: int | None,
    configuration_version: int | None,
    settings: Settings,
) -> MetricReadinessResponse:
    access = _company_scope(session, user, tenant_id)
    definition, metric_row, definition_hash, metric_version, _system_version = _resolve_definition(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        metric_key=metric_key,
        version=version,
    )
    finance_sensitive = (
        metric_definition_requires_finance_read(session, metric_row)
        if metric_row is not None
        else _definition_requires_finance_read(definition)
    )
    if finance_sensitive:
        require_company_context_access(session, user, tenant_id, "finance:read")
    checks = [
        MetricReadinessCheck(
            check_key="metric_definition_published",
            status="ready",
            message="The selected metric definition is immutable and published.",
            evidence={
                "metric_key": metric_key,
                "version": metric_version or definition.get("version"),
                "definition_hash": definition_hash,
            },
        )
    ]
    calculation_key = str(definition.get("calculation_key"))
    calculation_supported = calculation_key in {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
        "m5_metric",
        "core_financial_measure",
        "custom_formula",
    }
    config_row = (
        get_scoped_configuration(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            version=configuration_version or 0,
        )
        if configuration_version is not None
        else session.scalar(
            select(BusinessCompanySemanticConfigVersion)
            .where(
                BusinessCompanySemanticConfigVersion.tenant_id == tenant_id,
                BusinessCompanySemanticConfigVersion.company_id == access.company.id,
                BusinessCompanySemanticConfigVersion.status == "published",
            )
            .order_by(BusinessCompanySemanticConfigVersion.version.desc())
            .limit(1)
        )
    )
    if config_row is None:
        checks.append(
            MetricReadinessCheck(
                check_key="company_semantic_configuration",
                status="blocked",
                message=(
                    "Publish a Company semantic configuration before running Business "
                    "Intelligence analyses."
                ),
            )
        )
        return _readiness_response(
            definition=definition,
            definition_hash=definition_hash,
            metric_version=metric_version,
            configuration_version=None,
            calculation_supported=calculation_supported,
            checks=checks,
            blockers=["company_semantic_configuration_missing"],
            warnings=[],
        )
    checks.append(
        MetricReadinessCheck(
            check_key="company_semantic_configuration",
            status="ready",
            message="A published, version-pinned Company configuration is available.",
            evidence={
                "version": config_row.version,
                "configuration_hash": config_row.configuration_hash,
            },
        )
    )
    blockers: list[str] = []
    warnings: list[str] = []
    if calculation_key in {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    }:
        input_data, source_checks, source_blockers, source_warnings = _universal_input_snapshot(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            definition=definition,
            config_row=config_row,
            period_start=None,
            period_end=None,
        )
        del input_data
        checks.extend(source_checks)
        blockers.extend(source_blockers)
        warnings.extend(source_warnings)
        relationship_types = [
            str(item) for item in _list(definition.get("required_relationships", []))
        ]
        if relationship_types:
            _relationships, missing = _relationship_snapshots(
                session,
                tenant_id=tenant_id,
                company_id=access.company.id,
                relationship_types=relationship_types,
            )
            if missing:
                blockers.append("required_semantic_relationship_missing")
                checks.append(
                    MetricReadinessCheck(
                        check_key="required_semantic_relationships",
                        status="blocked",
                        message=(
                            "Current, analytically usable reviewed relationship versions "
                            "are missing: " + ", ".join(missing)
                        ),
                        evidence={"missing_relationship_types": missing},
                    )
                )
            else:
                checks.append(
                    MetricReadinessCheck(
                        check_key="required_semantic_relationships",
                        status="ready",
                        message=(
                            "Required relationships have current source-verified mappings; "
                            "source references remain source-scoped."
                        ),
                        evidence={"relationship_types": relationship_types},
                    )
                )
    elif calculation_key == "m5_metric":
        checks.append(
            MetricReadinessCheck(
                check_key="saved_m5_run_required",
                status="warning",
                message=(
                    "Select an existing saved M5 run for analysis. No new capacity "
                    "calculation is created here."
                ),
            )
        )
        warnings.extend(["saved_m5_run_required", "m5_implementation_draft"])
    elif calculation_key == "core_financial_measure":
        if settings.core_financials_enabled is not True or settings.app_env == "production":
            blockers.append("core_financials_disabled")
            checks.append(
                MetricReadinessCheck(
                    check_key="core_financials_enabled",
                    status="blocked",
                    message=(
                        "Core Financials is default-disabled or unavailable in production; "
                        "existing policy remains illustrative."
                    ),
                )
            )
        else:
            warnings.append("saved_core_financial_run_required")
            checks.append(
                MetricReadinessCheck(
                    check_key="saved_core_financial_run_required",
                    status="warning",
                    message=(
                        "Select an existing, verified Core Financials run. This registry "
                        "does not replace its calculation or policy."
                    ),
                )
            )
    else:
        warnings.append("saved_dependency_analyses_required")
        checks.append(
            MetricReadinessCheck(
                check_key="saved_dependency_analyses_required",
                status="warning",
                message=(
                    "Provide exact saved analyses for every pinned formula dependency "
                    "before calculating this metric."
                ),
                evidence={"dependencies": definition.get("dependencies", [])},
            )
        )
    return _readiness_response(
        definition=definition,
        definition_hash=definition_hash,
        metric_version=metric_version,
        configuration_version=config_row.version,
        calculation_supported=calculation_supported,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
    )


def _analysis_response(
    row: BusinessIntelligenceAnalysisRun,
) -> MetricAnalysisResponse:
    calculation_inputs = _dict(row.input_snapshot.get("calculation_inputs", {}))
    evidence_count = (
        len(_list(calculation_inputs.get("evidence", [])))
        + len(_list(calculation_inputs.get("semantic_relationships", [])))
        + len(_list(calculation_inputs.get("external_evidence", [])))
    )
    return MetricAnalysisResponse(
        id=row.id,
        metric_key=row.metric_key,
        metric_version=row.metric_version,
        system_metric_version=row.system_metric_version,
        definition_hash=row.definition_hash,
        configuration_version=row.configuration_version,
        configuration_hash=str(row.configuration_snapshot.get("configuration_hash", "")),
        period_start=row.period_start,
        period_end=row.period_end,
        status=cast(Any, row.status),
        calculation_version=row.calculation_version,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        result_snapshot=row.result_snapshot,
        evidence_count=evidence_count,
        created_at=row.created_at,
    )


def _validate_dependency_analyses(
    session: Session,
    user: UserAccount,
    settings: Settings,
    *,
    tenant_id: UUID,
    company_id: UUID,
    definition: dict[str, object],
    config_row: BusinessCompanySemanticConfigVersion,
    period_start: date | None,
    period_end: date | None,
    dimensions: list[str],
    provided: list[MetricDependencyAnalysisInput],
) -> list[dict[str, object]]:
    expected = dependency_by_key(
        [cast(dict[str, object], item) for item in _list(definition.get("dependencies", []))]
    )
    supplied = {item.metric_key: item.analysis_id for item in provided}
    if set(supplied) != set(expected):
        raise _error(
            422,
            "metric_dependency_analyses_mismatch",
            "Supply exactly one saved analysis for each pinned formula dependency.",
        )
    result: list[dict[str, object]] = []
    for metric_key in sorted(expected):
        dependency = expected[metric_key]
        analysis = session.scalar(
            select(BusinessIntelligenceAnalysisRun).where(
                BusinessIntelligenceAnalysisRun.id == supplied[metric_key],
                BusinessIntelligenceAnalysisRun.tenant_id == tenant_id,
                BusinessIntelligenceAnalysisRun.company_id == company_id,
            )
        )
        if analysis is None:
            raise _error(
                404,
                "metric_dependency_analysis_not_found",
                "A requested dependency analysis was not found in this Company.",
            )
        _require_analysis_integrity(analysis)
        if analysis.metric_key != metric_key:
            raise _error(
                409,
                "metric_dependency_metric_mismatch",
                "The saved analysis does not match the dependency metric key.",
            )
        if (
            analysis.configuration_version_id != config_row.id
            or analysis.configuration_version != config_row.version
        ):
            raise _error(
                409,
                "metric_dependency_analysis_integrity_error",
                "A dependency analysis is not valid for this exact Company configuration.",
            )
        dep_input = _dict(analysis.input_snapshot)
        _require_analysis_sensitive_scope(session, user, tenant_id, analysis, settings=settings)
        sensitivity_scope = str(dep_input.get("sensitivity_scope"))
        dep_dimensions = [str(item) for item in _list(dep_input.get("dimensions", []))]
        dep_period = _dict(dep_input.get("period", {}))
        requested_start = period_start.isoformat() if period_start else None
        requested_end = period_end.isoformat() if period_end else None
        if (
            dep_dimensions != dimensions
            or dep_period.get("start") != requested_start
            or dep_period.get("end") != requested_end
        ):
            raise _error(
                422,
                "metric_dependency_analysis_scope_mismatch",
                "Formula dependency analyses must use the same dimensions, period, and "
                "Company configuration.",
            )
        if dependency.get("version_id") is not None:
            if (
                str(analysis.metric_definition_version_id) != str(dependency.get("version_id"))
                or analysis.metric_version != dependency.get("version")
                or analysis.definition_hash != dependency.get("definition_hash")
            ):
                raise _error(
                    409,
                    "metric_dependency_definition_mismatch",
                    "A dependency analysis does not match the exact metric version "
                    "pinned in this formula.",
                )
        elif analysis.system_metric_version != dependency.get(
            "system_version"
        ) or analysis.definition_hash != dependency.get("definition_hash"):
            raise _error(
                409,
                "metric_dependency_definition_mismatch",
                "A dependency analysis does not match the system metric version pinned "
                "in this formula.",
            )
        result.append(
            {
                "metric_key": metric_key,
                "metric_version": analysis.metric_version,
                "system_metric_version": analysis.system_metric_version,
                "definition_hash": analysis.definition_hash,
                "analysis_id": str(analysis.id),
                "input_hash": analysis.input_hash,
                "result_hash": analysis.result_hash,
                "status": analysis.status,
                "sensitivity_scope": sensitivity_scope,
                "core_financials_used": analysis.input_snapshot.get("core_financials_used") is True,
                "result_snapshot": analysis.result_snapshot,
            }
        )
    return result


def _system_metric_reference_for_calculation(
    calculation: dict[str, object],
) -> tuple[str, str] | None:
    kind = calculation.get("kind")
    if kind == "m5_metric":
        identifier = calculation.get("metric_id")
        key = f"m5.{identifier}" if isinstance(identifier, str) else ""
    elif kind == "core_financial_measure":
        identifier = calculation.get("measure")
        key = f"core_financials.{identifier}" if isinstance(identifier, str) else ""
    else:
        return None
    metric = SYSTEM_METRIC_BY_KEY.get(key)
    if metric is None:
        return None
    return key, metric.version


def _validate_direct_m5_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    company_id: UUID,
    run_id: UUID,
    metric_id: str,
) -> dict[str, object]:
    run = get_metric_run(session, user, tenant_id, run_id)
    if run.company_id != company_id:
        raise _error(
            404, "metric_run_not_found", "The requested M5 run was not found in this Company."
        )
    _validate_metric_run(run, tenant_id, company_id)
    metric_versions = _dict(run.result_snapshot.get("metric_versions", {}))
    expected = next(
        (item.version for item in SYSTEM_METRICS if item.metric_key == f"m5.{metric_id}"), None
    )
    if expected is None or metric_versions.get(metric_id) != expected.rsplit(":", 1)[-1]:
        raise _error(
            409,
            "m5_metric_version_mismatch",
            "The saved M5 run does not contain the exact metric version expected by the registry.",
        )
    return {
        "run_id": str(run.id),
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
        "metric_catalog_version": run.metric_catalog_version,
        "calculation_version": run.calculation_version,
        "reliability_status": run.reliability_status,
        "start_week_date": run.start_week_date.isoformat(),
        "end_week_date": run.end_week_date.isoformat(),
        "week_count": run.week_count,
        "result_snapshot": run.result_snapshot,
    }


def _validate_direct_core_financial_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    company_id: UUID,
    run_id: UUID,
    measure: str,
    settings: Settings,
) -> dict[str, object]:
    # Preserve Core Financials' stricter saved-run verification boundary explicitly.
    require_company_context_access(session, user, tenant_id, "finance:write")
    response = get_core_financial_run(session, user, tenant_id, run_id, settings=settings)
    if response.company_id != company_id:
        raise _error(
            404,
            "core_financial_run_not_found",
            "The requested Core Financials run was not found in this Company.",
        )
    run = session.scalar(
        select(CoreFinancialRun).where(
            CoreFinancialRun.id == run_id,
            CoreFinancialRun.tenant_id == tenant_id,
            CoreFinancialRun.company_id == company_id,
        )
    )
    if run is None:
        raise _error(
            404,
            "core_financial_run_not_found",
            "The requested Core Financials run was not found in this Company.",
        )
    verification = verify_core_financial_run(
        session,
        user,
        tenant_id,
        run_id,
        settings=settings,
    )
    if not verification.passed:
        raise _error(
            409,
            "core_financial_run_integrity_error",
            "The selected Core Financials run failed its persisted evidence verification.",
        )
    if [item.metric for item in response.metrics] != [measure]:
        raise _error(
            409,
            "core_financial_measure_mismatch",
            "The selected Core Financials run does not match the measure in this definition.",
        )
    return {
        "run_id": str(run.id),
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
        "trace_hash": run.trace_hash,
        "policy_id": str(run.policy_id),
        "policy_version": run.policy_version,
        "policy_hash": run.policy_hash,
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "currency_code": run.currency_code,
        "state": run.state,
        "result_snapshot": run.result_snapshot,
        "metric_response": response.model_dump(mode="json"),
    }


def _check_analysis_request(
    definition: dict[str, object],
    body: MetricAnalysisCreateRequest,
) -> None:
    calculation_key = str(definition.get("calculation_key"))
    allowed_dimensions = {str(item) for item in _list(definition.get("dimensions", []))}
    if not set(body.dimensions).issubset(allowed_dimensions):
        raise _error(
            422,
            "metric_dimension_unsupported",
            "Choose only dimensions declared by the pinned metric definition.",
        )
    if calculation_key in {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    }:
        entity_type = str(_dict(definition.get("calculation")).get("entity_type"))
        if body.source_run_id or body.core_financial_run_id or body.dependency_analyses:
            raise _error(
                422,
                "metric_analysis_input_invalid",
                "Universal Data metrics use the version-pinned Company source "
                "configuration, not external run IDs.",
            )
        if any(item.entity_type != entity_type for item in body.filters):
            raise _error(
                422,
                "metric_filter_entity_mismatch",
                "Every analysis filter must use the metric's declared canonical entity.",
            )
    elif calculation_key == "m5_metric":
        if not body.source_run_id:
            raise _error(
                422, "m5_saved_run_required", "Select an existing saved M5 run for this metric."
            )
        metric_id = str(_dict(definition.get("calculation")).get("metric_id"))
        if metric_id == "recorded_skill_inventory_coverage" and body.period_start is not None:
            raise _error(
                422,
                "m5_metric_period_unsupported",
                "Recorded skill inventory is a non-periodic structured observation.",
            )
        if body.core_financial_run_id or body.dependency_analyses or body.filters:
            raise _error(
                422,
                "metric_analysis_input_invalid",
                "M5 analyses accept only the exact saved M5 run and the supported week dimension.",
            )
        if set(body.dimensions) - {"week"}:
            raise _error(
                422,
                "metric_dimension_unsupported",
                "Existing M5 metric runs expose only their saved weekly series.",
            )
    elif calculation_key == "core_financial_measure":
        if not body.core_financial_run_id:
            raise _error(
                422,
                "core_financial_run_required",
                "Select an existing verified Core Financials run for this measure.",
            )
        if body.source_run_id or body.dependency_analyses or body.dimensions or body.filters:
            raise _error(
                422,
                "metric_analysis_input_invalid",
                "Core Financials measures remain bound to their existing run period and "
                "cannot be regrouped here.",
            )
    elif calculation_key == "custom_formula":
        if body.source_run_id or body.core_financial_run_id or body.filters:
            raise _error(
                422,
                "metric_analysis_input_invalid",
                "Formula analyses use exact saved dependency analyses only.",
            )
    else:
        raise _error(
            422,
            "metric_calculation_unsupported",
            "The selected metric calculation is not supported.",
        )


def create_metric_analysis(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: MetricAnalysisCreateRequest,
    request_id: UUID,
    *,
    settings: Settings,
) -> MetricAnalysisResponse:
    access = _company_scope(session, user, tenant_id)
    definition, metric_row, definition_hash, metric_version, system_version = _resolve_definition(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        metric_key=body.metric_key,
        version=body.metric_version,
    )
    finance_sensitive = (
        metric_definition_requires_finance_read(session, metric_row)
        if metric_row is not None
        else _definition_requires_finance_read(definition)
    )
    if finance_sensitive:
        require_company_context_access(session, user, tenant_id, "finance:read")
    _check_analysis_request(definition, body)
    config_row = get_scoped_configuration(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        version=body.configuration_version,
    )
    if config_row is None:
        raise _error(
            404,
            "company_semantic_config_not_found",
            "The requested published Company configuration was not found.",
        )
    configuration_snapshot, _configuration_hash_payload = _configuration_snapshot(
        session, config_row
    )

    period_start = body.period_start
    period_end = body.period_end
    calculation_key = str(definition.get("calculation_key"))
    effective_dimensions: list[str] = [str(item) for item in body.dimensions]
    time_granularity = str(definition.get("time_granularity", "none"))
    if time_granularity != "none" and time_granularity not in effective_dimensions:
        if len(effective_dimensions) >= 2:
            raise _error(
                422,
                "metric_dimension_limit_exceeded",
                "The declared time granularity plus requested dimensions exceeds the "
                "supported group limit.",
            )
        effective_dimensions.append(time_granularity)
    calculation_inputs: dict[str, object] = {
        "records": [],
        "evidence": [],
        "blockers": [],
        "warnings": [],
        "source_selection": None,
        "semantic_relationships": [],
    }
    source_selection: dict[str, object] | None = None
    dependency_analyses: list[dict[str, object]] = []
    external_evidence_refs: list[dict[str, object]] = []

    if calculation_key in {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    }:
        universal_inputs, _checks, _blockers, _warnings = _universal_input_snapshot(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            definition=definition,
            config_row=config_row,
            period_start=period_start.isoformat() if period_start else None,
            period_end=period_end.isoformat() if period_end else None,
        )
        calculation_inputs = universal_inputs
        source_selection = cast(dict[str, object] | None, universal_inputs.get("source_selection"))
        relationship_types = [
            str(item) for item in _list(definition.get("required_relationships", []))
        ]
        if relationship_types:
            relationship_refs, missing_relationships = _relationship_snapshots(
                session,
                tenant_id=tenant_id,
                company_id=access.company.id,
                relationship_types=relationship_types,
                max_evidence=MAX_BI_EVIDENCE_REFERENCES
                - len(_list(calculation_inputs.get("evidence", []))),
            )
            calculation_inputs["semantic_relationships"] = relationship_refs
            if missing_relationships:
                blockers = [str(item) for item in _list(calculation_inputs.get("blockers", []))]
                blockers.append("required_semantic_relationship_missing")
                calculation_inputs["blockers"] = sorted(set(blockers))
    elif calculation_key == "m5_metric":
        calculation = _dict(definition.get("calculation"))
        metric_id = str(calculation.get("metric_id"))
        m5_run = _validate_direct_m5_run(
            session,
            user,
            tenant_id,
            access.company.id,
            cast(UUID, body.source_run_id),
            metric_id,
        )
        m5_result = _dict(m5_run.get("result_snapshot"))
        m5_period = _dict(m5_result.get("period"))
        if m5_period.get("company_timezone") != configuration_snapshot.get("timezone_name"):
            raise _error(
                422,
                "m5_run_timezone_mismatch",
                "The saved M5 run timezone must match the pinned Company configuration.",
            )
        run_start = date.fromisoformat(str(m5_run["start_week_date"]))
        run_end = date.fromisoformat(str(m5_run["end_week_date"]))
        if metric_id == "recorded_skill_inventory_coverage":
            period_start = period_end = None
        elif period_start is None and period_end is None:
            period_start, period_end = run_start, run_end
        elif (
            period_start is None
            or period_end is None
            or period_start < run_start
            or period_end > run_end
        ):
            raise _error(
                422,
                "m5_run_period_mismatch",
                "The selected M5 run must cover the requested period.",
            )
        if metric_id != "recorded_skill_inventory_coverage" and (
            period_start is None
            or period_end is None
            or period_start.isoweekday() != 1
            or period_end.isoweekday() != 7
        ):
            raise _error(
                422,
                "m5_run_period_boundary_invalid",
                "A weekly M5 analysis period must start on Monday and end on Sunday.",
            )
        calculation_inputs["m5_run"] = m5_run
        run_metric_reference = _system_metric_reference_for_calculation(
            _dict(definition.get("calculation"))
        )
        if run_metric_reference is None:
            raise _error(
                409,
                "system_metric_registry_invalid",
                "The selected M5 metric is not present in the versioned system registry.",
            )
        external_evidence_refs.append(
            {
                "evidence_kind": "m5_metric_run",
                "run_id": m5_run["run_id"],
                "metric_key": run_metric_reference[0],
                "system_metric_version": run_metric_reference[1],
                "input_hash": m5_run["input_hash"],
                "result_hash": m5_run["result_hash"],
                "metric_catalog_version": m5_run["metric_catalog_version"],
                "calculation_version": m5_run["calculation_version"],
            }
        )
    elif calculation_key == "core_financial_measure":
        core_run = _validate_direct_core_financial_run(
            session,
            user,
            tenant_id,
            access.company.id,
            cast(UUID, body.core_financial_run_id),
            str(_dict(definition.get("calculation")).get("measure")),
            settings,
        )
        run_start = date.fromisoformat(str(core_run["period_start"]))
        run_end = date.fromisoformat(str(core_run["period_end"]))
        if period_start is None and period_end is None:
            period_start, period_end = run_start, run_end
        elif period_start != run_start or period_end != run_end:
            raise _error(
                422,
                "core_financial_run_period_mismatch",
                "The analysis period must match the selected Core Financials run exactly.",
            )
        calculation_inputs["core_financial_run"] = core_run
        run_metric_reference = _system_metric_reference_for_calculation(
            _dict(definition.get("calculation"))
        )
        if run_metric_reference is None:
            raise _error(
                409,
                "system_metric_registry_invalid",
                "The selected Core Financials measure is not in the versioned system registry.",
            )
        external_evidence_refs.append(
            {
                "evidence_kind": "core_financial_run",
                "run_id": core_run["run_id"],
                "metric_key": run_metric_reference[0],
                "system_metric_version": run_metric_reference[1],
                "input_hash": core_run["input_hash"],
                "result_hash": core_run["result_hash"],
                "trace_hash": core_run["trace_hash"],
                "policy_id": core_run["policy_id"],
                "policy_version": core_run["policy_version"],
                "policy_hash": core_run["policy_hash"],
            }
        )
    else:
        dependency_analyses = _validate_dependency_analyses(
            session,
            user,
            settings,
            tenant_id=tenant_id,
            company_id=access.company.id,
            definition=definition,
            config_row=config_row,
            period_start=period_start,
            period_end=period_end,
            dimensions=effective_dimensions,
            provided=body.dependency_analyses,
        )
        calculation_inputs["dependency_analyses"] = dependency_analyses
        external_evidence_refs.extend(
            {
                "evidence_kind": "dependency_analysis",
                "analysis_id": item["analysis_id"],
                "metric_key": item["metric_key"],
                "metric_version": item["metric_version"],
                "system_metric_version": item["system_metric_version"],
                "input_hash": item["input_hash"],
                "result_hash": item["result_hash"],
                "definition_hash": item["definition_hash"],
            }
            for item in dependency_analyses
        )

    calculation_inputs["external_evidence"] = external_evidence_refs
    effective_period = {
        "start": period_start.isoformat() if period_start else None,
        "end": period_end.isoformat() if period_end else None,
    }
    core_financials_used = calculation_key == "core_financial_measure" or any(
        item.get("core_financials_used") is True for item in dependency_analyses
    )
    sensitivity_scope = (
        "finance"
        if _definition_requires_finance_read(definition)
        or any(item.get("sensitivity_scope") == "finance" for item in dependency_analyses)
        else "company_context"
    )
    input_snapshot: dict[str, object] = {
        "schema_version": BI_INPUT_SCHEMA_VERSION,
        "sensitivity_scope": sensitivity_scope,
        "core_financials_used": core_financials_used,
        "tenant_id": str(tenant_id),
        "company_id": str(access.company.id),
        "metric_key": body.metric_key,
        "metric_version": metric_version,
        "system_metric_version": system_version,
        "definition_hash": definition_hash,
        "definition_snapshot_hash": content_hash(definition),
        "registry_version": METRIC_REGISTRY_VERSION,
        "configuration_version_id": str(config_row.id),
        "configuration_version": config_row.version,
        "configuration_hash": config_row.configuration_hash,
        "configuration_snapshot_hash": content_hash(configuration_snapshot),
        "period": effective_period,
        "dimensions": effective_dimensions,
        "analysis_filters": [
            item.model_dump(mode="json", exclude_none=True) for item in body.filters
        ],
        "source_run_id": str(body.source_run_id) if body.source_run_id else None,
        "core_financial_run_id": str(body.core_financial_run_id)
        if body.core_financial_run_id
        else None,
        "dependency_analyses": dependency_analyses,
        "source_selection": source_selection,
        "calculation_inputs": calculation_inputs,
    }
    try:
        result_snapshot = calculate_saved_analysis(
            definition, configuration_snapshot, input_snapshot
        )
    except (ArithmeticError, TypeError, ValueError, KeyError):
        raise _error(
            409,
            "business_intelligence_calculation_abstained",
            "The selected metric could not be calculated from its exact saved evidence.",
        ) from None
    status = result_snapshot.get("status")
    if status not in {"complete", "partial", "abstained"}:
        raise _error(
            409,
            "business_intelligence_result_invalid",
            "The deterministic calculation produced an invalid status.",
        )
    run = BusinessIntelligenceAnalysisRun(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        configuration_version_id=config_row.id,
        configuration_version=config_row.version,
        metric_key=body.metric_key,
        metric_definition_version_id=metric_row.id if metric_row is not None else None,
        metric_version=metric_version,
        system_metric_version=system_version,
        definition_hash=definition_hash,
        definition_snapshot=definition,
        configuration_snapshot=configuration_snapshot,
        period_start=period_start,
        period_end=period_end,
        status=str(status),
        calculation_version=BI_CALCULATION_VERSION,
        input_hash=content_hash(input_snapshot),
        result_hash=content_hash(result_snapshot),
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
    )
    session.add(run)
    session.flush()
    record_event(
        session,
        "business_intelligence.analysis_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_intelligence_analysis",
        target_id=run.id,
        request_id=request_id,
        details={
            "metric_key": run.metric_key,
            "metric_version": run.metric_version,
            "system_metric_version": run.system_metric_version,
            "configuration_version": run.configuration_version,
            "status": run.status,
            "input_hash": run.input_hash,
            "result_hash": run.result_hash,
        },
    )
    return _analysis_response(run)


def _get_analysis(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    analysis_id: UUID,
) -> BusinessIntelligenceAnalysisRun:
    row = session.scalar(
        select(BusinessIntelligenceAnalysisRun).where(
            BusinessIntelligenceAnalysisRun.id == analysis_id,
            BusinessIntelligenceAnalysisRun.tenant_id == tenant_id,
            BusinessIntelligenceAnalysisRun.company_id == company_id,
        )
    )
    if row is None:
        raise _error(
            404, "business_intelligence_analysis_not_found", "The saved analysis was not found."
        )
    return row


def _require_analysis_sensitive_scope(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    row: BusinessIntelligenceAnalysisRun,
    *,
    settings: Settings | None,
) -> None:
    sensitivity_scope = row.input_snapshot.get("sensitivity_scope")
    if sensitivity_scope == "finance":
        require_company_context_access(session, user, tenant_id, "finance:read")
    elif sensitivity_scope != "company_context":
        raise _error(
            409,
            "business_intelligence_analysis_integrity_error",
            "The saved analysis has an unsupported sensitivity scope.",
        )
    if row.input_snapshot.get("core_financials_used") is True and (
        settings is None
        or settings.core_financials_enabled is not True
        or settings.app_env == "production"
    ):
        raise _error(
            503,
            "core_financials_disabled",
            "Core Financials is disabled by deployment configuration.",
        )


def _require_analysis_integrity(row: BusinessIntelligenceAnalysisRun) -> None:
    input_snapshot = row.input_snapshot
    result_snapshot = row.result_snapshot
    definition_snapshot = row.definition_snapshot
    configuration_snapshot = row.configuration_snapshot
    calculation_inputs = input_snapshot.get("calculation_inputs")
    expected_period = {
        "start": row.period_start.isoformat() if row.period_start is not None else None,
        "end": row.period_end.isoformat() if row.period_end is not None else None,
    }
    if not isinstance(input_snapshot.get("core_financials_used"), bool):
        raise _error(
            409,
            "business_intelligence_analysis_integrity_error",
            "The saved analysis has an invalid feature-scope marker.",
        )
    if (
        input_snapshot.get("schema_version") != BI_INPUT_SCHEMA_VERSION
        or result_snapshot.get("schema_version") != BI_RESULT_SCHEMA_VERSION
        or content_hash(input_snapshot) != row.input_hash
        or content_hash(result_snapshot) != row.result_hash
        or input_snapshot.get("tenant_id") != str(row.tenant_id)
        or input_snapshot.get("company_id") != str(row.company_id)
        or configuration_snapshot.get("company_id") != str(row.company_id)
        or input_snapshot.get("metric_key") != row.metric_key
        or input_snapshot.get("metric_version") != row.metric_version
        or input_snapshot.get("system_metric_version") != row.system_metric_version
        or input_snapshot.get("definition_hash") != row.definition_hash
        or input_snapshot.get("definition_snapshot_hash") != content_hash(definition_snapshot)
        or definition_snapshot.get("metric_key") != row.metric_key
        or definition_snapshot.get("definition_hash") != row.definition_hash
        or (
            row.metric_definition_version_id is not None
            and definition_snapshot.get("version") != row.metric_version
        )
        or (
            row.metric_definition_version_id is None
            and definition_snapshot.get("version") != row.system_metric_version
        )
        or input_snapshot.get("configuration_version_id") != str(row.configuration_version_id)
        or input_snapshot.get("configuration_version") != row.configuration_version
        or configuration_snapshot.get("configuration_version_id")
        != str(row.configuration_version_id)
        or configuration_snapshot.get("configuration_version") != row.configuration_version
        or configuration_snapshot.get("configuration_hash")
        != input_snapshot.get("configuration_hash")
        or input_snapshot.get("configuration_snapshot_hash") != content_hash(configuration_snapshot)
        or result_snapshot.get("metric_key") != row.metric_key
        or result_snapshot.get("definition_hash") != row.definition_hash
        or result_snapshot.get("calculation_version") != row.calculation_version
        or result_snapshot.get("status") != row.status
        or input_snapshot.get("period") != expected_period
        or result_snapshot.get("period") != input_snapshot.get("period")
        or result_snapshot.get("sensitivity_scope") != input_snapshot.get("sensitivity_scope")
        or (
            row.definition_snapshot.get("calculation_key") == "core_financial_measure"
            and input_snapshot.get("core_financials_used") is not True
        )
        or (
            input_snapshot.get("core_financial_run_id") is not None
            and input_snapshot.get("core_financials_used") is not True
        )
        or not isinstance(calculation_inputs, dict)
    ):
        raise _error(
            409,
            "business_intelligence_analysis_integrity_error",
            "The saved analysis failed an integrity check.",
        )

    dependency_snapshots = calculation_inputs.get("dependency_analyses", [])
    if not isinstance(dependency_snapshots, list) or any(
        not isinstance(item, dict) for item in dependency_snapshots
    ):
        raise _error(
            409,
            "business_intelligence_analysis_integrity_error",
            "The saved analysis has invalid dependency scope evidence.",
        )
    has_finance_dependency = any(
        item.get("sensitivity_scope") == "finance" or item.get("core_financials_used") is True
        for item in dependency_snapshots
    )
    if (
        _definition_requires_finance_read(definition_snapshot)
        or input_snapshot.get("core_financials_used") is True
        or has_finance_dependency
    ) and input_snapshot.get("sensitivity_scope") != "finance":
        raise _error(
            409,
            "business_intelligence_analysis_integrity_error",
            "The saved analysis has an inconsistent finance-sensitivity scope.",
        )


def get_metric_analysis(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    analysis_id: UUID,
    *,
    settings: Settings | None = None,
) -> MetricAnalysisResponse:
    access = _company_scope(session, user, tenant_id)
    row = _get_analysis(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        analysis_id=analysis_id,
    )
    _require_analysis_integrity(row)
    _require_analysis_sensitive_scope(session, user, tenant_id, row, settings=settings)
    return _analysis_response(row)


def list_metric_analyses(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    metric_key: str | None,
    limit: int,
    offset: int,
    settings: Settings | None = None,
) -> MetricAnalysisPage:
    access = _company_scope(session, user, tenant_id)
    filters = [
        BusinessIntelligenceAnalysisRun.tenant_id == tenant_id,
        BusinessIntelligenceAnalysisRun.company_id == access.company.id,
    ]
    if not _has_finance_read_access(session, user, tenant_id):
        filters.append(
            BusinessIntelligenceAnalysisRun.input_snapshot["sensitivity_scope"].as_string()
            == "company_context"
        )
    if metric_key is not None:
        filters.append(BusinessIntelligenceAnalysisRun.metric_key == metric_key)
    total = (
        session.scalar(
            select(func.count()).select_from(BusinessIntelligenceAnalysisRun).where(*filters)
        )
        or 0
    )
    rows = list(
        session.scalars(
            select(BusinessIntelligenceAnalysisRun)
            .where(*filters)
            .order_by(
                BusinessIntelligenceAnalysisRun.created_at.desc(),
                BusinessIntelligenceAnalysisRun.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    for row in rows:
        _require_analysis_integrity(row)
        _require_analysis_sensitive_scope(session, user, tenant_id, row, settings=settings)
    return MetricAnalysisPage(
        items=[_analysis_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


def replay_metric_analysis(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    analysis_id: UUID,
    *,
    settings: Settings | None = None,
) -> MetricAnalysisReplayResponse:
    access = _company_scope(session, user, tenant_id)
    row = _get_analysis(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        analysis_id=analysis_id,
    )
    _require_analysis_integrity(row)
    _require_analysis_sensitive_scope(session, user, tenant_id, row, settings=settings)
    if row.calculation_version != BI_CALCULATION_VERSION:
        raise _error(
            409,
            "business_intelligence_calculation_version_unsupported",
            "The saved analysis uses an unsupported calculation engine version.",
        )
    try:
        replayed = calculate_saved_analysis(
            row.definition_snapshot,
            row.configuration_snapshot,
            row.input_snapshot,
        )
    except (ArithmeticError, TypeError, ValueError, KeyError):
        raise _error(
            409,
            "business_intelligence_analysis_replay_failed",
            "The saved analysis could not be deterministically replayed.",
        ) from None
    replayed_hash = content_hash(replayed)
    if replayed != row.result_snapshot or replayed_hash != row.result_hash:
        raise _error(
            409,
            "business_intelligence_analysis_replay_mismatch",
            "The deterministic replay does not match the saved result.",
        )
    return MetricAnalysisReplayResponse(
        analysis_id=row.id,
        replay_status="verified",
        input_hash=row.input_hash,
        stored_result_hash=row.result_hash,
        replayed_result_hash=replayed_hash,
        result_snapshot=replayed,
    )


def list_metric_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    analysis_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings | None = None,
) -> MetricEvidencePage:
    access = _company_scope(session, user, tenant_id)
    row = _get_analysis(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        analysis_id=analysis_id,
    )
    _require_analysis_integrity(row)
    _require_analysis_sensitive_scope(session, user, tenant_id, row, settings=settings)
    calculation_inputs = _dict(row.input_snapshot.get("calculation_inputs", {}))
    source_evidence = [
        {"evidence_kind": "universal_record", **_dict(item)}
        for item in _list(calculation_inputs.get("evidence", []))
    ]
    semantic_evidence = [
        {
            "evidence_kind": "semantic_relationship",
            "relationship_version_id": item.get("relationship_version_id"),
            "relationship_type": item.get("relationship_type"),
            "relationship_version": item.get("version"),
            "relationship_status": item.get("status"),
            "relationship_evidence_kind": item.get("evidence_kind"),
            "relationship_subject_entity_id": item.get("subject_entity_id"),
            "relationship_object_entity_id": item.get("object_entity_id"),
            "subject_mapping_version_id": item.get("subject_mapping_version_id"),
            "object_mapping_version_id": item.get("object_mapping_version_id"),
        }
        for item in (
            _dict(value) for value in _list(calculation_inputs.get("semantic_relationships", []))
        )
    ]
    external_evidence = [
        _dict(item) for item in _list(calculation_inputs.get("external_evidence", []))
    ]
    all_evidence = source_evidence + semantic_evidence + external_evidence
    items = [
        MetricEvidenceReference.model_validate({"evidence_index": index + offset, **item})
        for index, item in enumerate(all_evidence[offset : offset + limit])
    ]
    return MetricEvidencePage(
        analysis_id=row.id,
        items=items,
        total=len(all_evidence),
        limit=limit,
        offset=offset,
    )
