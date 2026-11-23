from __future__ import annotations

import contextlib
import hmac
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.calculation_core.decimal import (
    decimal_text,
    parse_decimal_text,
    validate_currency_code,
)
from businessflow.config import Settings
from businessflow.errors import ApiError
from businessflow.finance_sources.commitments import (
    AMOUNT_COMMITMENT_KEY_VERSION,
    finance_amount_commitment,
)
from businessflow.finance_sources.contracts import (
    FINANCE_SOURCE_CONTRACT_HASH,
    FINANCE_SOURCE_CONTRACT_KEY,
    FINANCE_SOURCE_CONTRACT_VERSION,
)
from businessflow.finance_sources.models import (
    FinanceSourceContract,
    FinanceSourceMapping,
    FinanceSourceRowEvidence,
)
from businessflow.finance_sources.schemas import FinanceSourceIntakeResponse
from businessflow.finance_sources.service import (
    _verify_mapping_record,
    get_finance_source_intake,
)
from businessflow.financial_calculations.calculation import (
    calculate_financial_calculation,
    financial_calculation_formula_hash,
    verify_financial_calculation_replay,
)
from businessflow.financial_calculations.catalog import (
    FINANCIAL_CALCULATION_CATALOG_VERSION,
    FINANCIAL_FORMULA_CATALOG,
    SOURCE_GROUP_SUBTOTAL_ID,
    SOURCE_GROUP_SUBTOTAL_VERSION,
)
from businessflow.financial_calculations.contracts import (
    FinanceSourceEvidenceReference,
    FinanceSourceIntakeReference,
    FinancialAmountObservation,
    FinancialAmountRequestRow,
    FinancialCalculationCreateResponse,
    FinancialCalculationFormulaCatalogResponse,
    FinancialCalculationInputSnapshot,
    FinancialCalculationReplayVerificationReport,
    FinancialCalculationRequest,
    FinancialCalculationRunResponse,
    FinancialReconciliationGroupReference,
    InputStatus,
    SourceEvidenceIntegrityStatus,
)
from businessflow.financial_calculations.models import FinancialCalculationRun
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access

MAX_SOURCE_GROUP_QUERY_ROWS = 10_000


def ensure_financial_calculations_enabled(settings: Settings | None) -> None:
    """Fail closed unless both non-production M14 and M15 operator flags are enabled."""
    if (
        getattr(settings, "financial_calculations_enabled", None) is not True
        or getattr(settings, "finance_source_intake_enabled", None) is not True
        or getattr(settings, "app_env", None) == "production"
    ):
        raise ApiError(
            503,
            "financial_calculations_disabled",
            "Deterministic financial calculations are disabled by deployment configuration.",
        )


def _not_found() -> ApiError:
    return ApiError(404, "financial_calculation_run_not_found", "Calculation run was not found.")


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _group_reference(
    intake: FinanceSourceIntakeResponse, requested_group_hash: str
) -> FinancialReconciliationGroupReference | None:
    groups = intake.result_snapshot.get("reconciliation_groups")
    if not isinstance(groups, list):
        raise ApiError(
            409,
            "finance_source_group_unverifiable",
            "The finance source reconciliation groups could not be verified.",
        )
    matches = [
        item
        for item in groups
        if isinstance(item, dict) and item.get("group_hash") == requested_group_hash
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    period_start_raw = item.get("period_start")
    period_end_raw = item.get("period_end")
    currency_raw = item.get("currency")
    source_row_count = _as_int(item.get("source_row_count"))
    if (
        not isinstance(period_start_raw, str)
        or not isinstance(period_end_raw, str)
        or not isinstance(currency_raw, str)
        or source_row_count is None
        or source_row_count < 0
    ):
        raise ApiError(
            409,
            "finance_source_group_unverifiable",
            "The finance source reconciliation group could not be verified.",
        )
    try:
        period_start = date.fromisoformat(period_start_raw)
        period_end = date.fromisoformat(period_end_raw)
        if period_start.isoformat() != period_start_raw or period_end.isoformat() != period_end_raw:
            return None
        return FinancialReconciliationGroupReference(
            schema_version=1,
            reconciliation_group_hash=requested_group_hash,
            period_start=period_start,
            period_end=period_end,
            currency_code=validate_currency_code(currency_raw),
            source_row_count=source_row_count,
        )
    except (ValueError, TypeError):
        return None


def _evidence_reference(row: FinanceSourceRowEvidence) -> FinanceSourceEvidenceReference:
    key_version = row.amount_commitment_key_version
    if key_version not in {None, AMOUNT_COMMITMENT_KEY_VERSION}:
        key_version = None
    return FinanceSourceEvidenceReference(
        schema_version=1,
        row_evidence_id=row.id,
        source_row_number=row.source_row_number,
        source_row_hash=row.source_row_hash,
        source_record_identity_hash=row.source_record_identity_hash,
        reconciliation_group_hash=row.reconciliation_group_hash,
        amount_commitment_hash=row.amount_commitment_hash,
        amount_commitment_key_version=cast(Literal["app-secret.v1"] | None, key_version),
        row_status=cast(Literal["valid", "duplicate", "invalid"], row.row_status),
    )


def _supported_source_contract(
    session: Session,
    intake: FinanceSourceIntakeResponse,
) -> None:
    if (
        intake.contract_key != FINANCE_SOURCE_CONTRACT_KEY
        or intake.contract_version != FINANCE_SOURCE_CONTRACT_VERSION
        or intake.contract_hash != FINANCE_SOURCE_CONTRACT_HASH
    ):
        raise ApiError(
            409,
            "finance_source_contract_unsupported",
            "This calculation requires the exact supported M14 source contract version.",
        )
    contract = session.get(FinanceSourceContract, (intake.contract_key, intake.contract_version))
    if (
        contract is None
        or contract.definition_hash != intake.contract_hash
        or content_hash(contract.definition) != contract.definition_hash
    ):
        raise ApiError(
            409,
            "finance_source_contract_unverifiable",
            "The referenced finance source contract could not be verified.",
        )
    mapping_snapshot = intake.input_snapshot.get("mapping")
    mapping_row = session.scalar(
        select(FinanceSourceMapping).where(
            FinanceSourceMapping.tenant_id == intake.tenant_id,
            FinanceSourceMapping.company_id == intake.company_id,
            FinanceSourceMapping.source_key == intake.source_key,
            FinanceSourceMapping.mapping_version == intake.mapping_version,
            FinanceSourceMapping.mapping_hash == intake.mapping_hash,
        )
    )
    if mapping_row is None or not isinstance(mapping_snapshot, dict):
        raise ApiError(
            409,
            "finance_source_mapping_unverifiable",
            "The referenced finance source mapping could not be verified.",
        )
    _verify_mapping_record(mapping_row)
    if (
        mapping_row.contract_key != intake.contract_key
        or mapping_row.contract_version != intake.contract_version
        or mapping_row.contract_hash != intake.contract_hash
        or mapping_snapshot.get("mapping_version") != mapping_row.mapping_version
        or mapping_snapshot.get("mapping_hash") != mapping_row.mapping_hash
        or mapping_snapshot.get("field_mapping") != mapping_row.field_mapping
        or mapping_snapshot.get("ignored_columns") != mapping_row.ignored_columns
    ):
        raise ApiError(
            409,
            "finance_source_mapping_unverifiable",
            "The referenced finance source mapping could not be verified.",
        )


def _input_status(
    item: FinancialAmountRequestRow,
    evidence: FinanceSourceEvidenceReference | None,
    *,
    intake: FinanceSourceIntakeResponse,
    group: FinancialReconciliationGroupReference | None,
    identity_secret: bytes,
) -> InputStatus:
    if item.amount_text is None:
        return "missing"
    try:
        amount = parse_decimal_text(item.amount_text)
    except ValueError:
        return "invalid"
    if evidence is None:
        return "missing"
    if evidence.row_status == "duplicate":
        return "duplicate"
    if evidence.row_status != "valid":
        return "invalid"
    if group is None or evidence.reconciliation_group_hash != group.reconciliation_group_hash:
        return "invalid"
    if (
        evidence.amount_commitment_hash is None
        or evidence.amount_commitment_key_version != AMOUNT_COMMITMENT_KEY_VERSION
    ):
        return "invalid"
    # Commitments use canonical fixed-point Decimal text; do not serialize through JSON numbers.
    expected = finance_amount_commitment(
        secret=identity_secret,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        intake_id=intake.id,
        source_key=intake.source_key,
        file_hash=intake.file_hash,
        source_row_number=evidence.source_row_number,
        source_row_hash=evidence.source_row_hash,
        source_record_identity_hash=evidence.source_record_identity_hash,
        reconciliation_group_hash=group.reconciliation_group_hash,
        contract_key=intake.contract_key,
        contract_version=intake.contract_version,
        contract_hash=intake.contract_hash,
        mapping_version=intake.mapping_version,
        mapping_hash=intake.mapping_hash,
        canonical_amount_text=decimal_text(amount),
    )
    return "valid" if hmac.compare_digest(expected, evidence.amount_commitment_hash) else "invalid"


def _make_input_snapshot(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    request: FinancialCalculationRequest,
    captured_at: datetime,
    *,
    settings: Settings,
) -> FinancialCalculationInputSnapshot:
    ensure_financial_calculations_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    intake = get_finance_source_intake(session, user, tenant_id, intake_id, settings=settings)
    if intake.company_id != access.company.id:
        raise _not_found()
    _supported_source_contract(session, intake)
    group = _group_reference(intake, request.reconciliation_group_hash)

    filters = (
        FinanceSourceRowEvidence.tenant_id == tenant_id,
        FinanceSourceRowEvidence.company_id == access.company.id,
        FinanceSourceRowEvidence.intake_id == intake_id,
    )
    all_row_count = (
        session.scalar(select(func.count()).select_from(FinanceSourceRowEvidence).where(*filters))
        or 0
    )
    group_row_count = 0
    group_rows: list[FinanceSourceRowEvidence] = []
    if group is not None:
        group_filters = (
            *filters,
            FinanceSourceRowEvidence.reconciliation_group_hash == group.reconciliation_group_hash,
        )
        group_row_count = (
            session.scalar(
                select(func.count()).select_from(FinanceSourceRowEvidence).where(*group_filters)
            )
            or 0
        )
        if group_row_count <= MAX_SOURCE_GROUP_QUERY_ROWS:
            group_rows = list(
                session.scalars(
                    select(FinanceSourceRowEvidence)
                    .where(*group_filters)
                    .order_by(FinanceSourceRowEvidence.source_row_number)
                )
            )

    requested_numbers = sorted({item.source_row_number for item in request.observations})
    requested_rows: list[FinanceSourceRowEvidence] = []
    if requested_numbers:
        requested_rows = list(
            session.scalars(
                select(FinanceSourceRowEvidence).where(
                    *filters,
                    FinanceSourceRowEvidence.source_row_number.in_(requested_numbers),
                )
            )
        )
    row_by_number = {row.source_row_number: row for row in requested_rows}

    evidence_integrity: SourceEvidenceIntegrityStatus = "verified"
    if all_row_count != intake.total_rows:
        evidence_integrity = "incomplete"
    if group is not None and group_row_count != group.source_row_count:
        evidence_integrity = "incomplete"
    if (
        group is not None
        and group_row_count <= MAX_SOURCE_GROUP_QUERY_ROWS
        and len(group_rows) != group_row_count
    ):
        evidence_integrity = "incomplete"

    duplicate_numbers: dict[int, list[FinancialAmountRequestRow]] = defaultdict(list)
    for item in request.observations:
        duplicate_numbers[item.source_row_number].append(item)

    observations: list[FinancialAmountObservation] = []
    for item in request.observations:
        database_row = row_by_number.get(item.source_row_number)
        evidence = _evidence_reference(database_row) if database_row is not None else None
        row_inputs = duplicate_numbers[item.source_row_number]
        status: InputStatus
        if len(row_inputs) > 1:
            parsed_values = set()
            for repeated in row_inputs:
                if repeated.amount_text is None:
                    continue
                with contextlib.suppress(ValueError):
                    parsed_values.add(parse_decimal_text(repeated.amount_text))
            status = "conflicting" if len(parsed_values) > 1 else "duplicate"
        else:
            status = _input_status(
                item,
                evidence,
                intake=intake,
                group=group,
                identity_secret=settings.app_secret_key.get_secret_value().encode("utf-8"),
            )
        observations.append(
            FinancialAmountObservation(
                schema_version=1,
                source_row_number=item.source_row_number,
                amount_text=item.amount_text,
                status=status,
                evidence=evidence,
            )
        )

    requested_set = set(requested_numbers)
    if group_row_count <= MAX_SOURCE_GROUP_QUERY_ROWS:
        for row in group_rows:
            if row.source_row_number not in requested_set:
                observations.append(
                    FinancialAmountObservation(
                        schema_version=1,
                        source_row_number=row.source_row_number,
                        amount_text=None,
                        status="missing",
                        evidence=_evidence_reference(row),
                    )
                )

    observations.sort(
        key=lambda item: (
            item.source_row_number,
            item.amount_text or "",
            str(item.evidence.row_evidence_id) if item.evidence is not None else "",
        )
    )
    intake_reference = FinanceSourceIntakeReference(
        schema_version=1,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        intake_id=intake.id,
        source_key=intake.source_key,
        file_hash=intake.file_hash,
        input_hash=intake.input_hash,
        result_hash=intake.result_hash,
        contract_key=intake.contract_key,
        contract_version=intake.contract_version,
        contract_hash=intake.contract_hash,
        mapping_version=intake.mapping_version,
        mapping_hash=intake.mapping_hash,
        quality_status=intake.quality_status,
        freshness_status=intake.freshness_status,
        source_as_of_at=(
            intake.source_as_of_at.astimezone(UTC) if intake.source_as_of_at is not None else None
        ),
        freshness_max_age_hours=intake.freshness_max_age_hours,
        reconciliation_status=intake.reconciliation_status,
    )
    return FinancialCalculationInputSnapshot(
        schema_version=1,
        snapshot_id=request.snapshot_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        captured_at=captured_at,
        intake=intake_reference,
        requested_group_hash=request.reconciliation_group_hash,
        source_evidence_integrity_status=evidence_integrity,
        group=group,
        observations=tuple(observations),
    )


def _redacted_evidence_snapshot(trace) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": str(trace.run_id),
        "tenant_id": str(trace.input_snapshot.tenant_id),
        "company_id": str(trace.input_snapshot.company_id),
        "captured_at": trace.input_snapshot.captured_at.isoformat(),
        "intake": trace.input_snapshot.intake.model_dump(mode="json"),
        "requested_group_hash": trace.input_snapshot.requested_group_hash,
        "source_evidence_integrity_status": trace.input_snapshot.source_evidence_integrity_status,
        "group": (
            trace.input_snapshot.group.model_dump(mode="json")
            if trace.input_snapshot.group is not None
            else None
        ),
        "observations": [
            {
                "source_row_number": item.source_row_number,
                "status": item.status,
                "evidence": item.evidence.model_dump(mode="json") if item.evidence else None,
            }
            for item in trace.input_snapshot.observations
        ],
        "formula_id": trace.formula.formula_id,
        "formula_version": trace.formula.formula_version,
        "engine_version": trace.engine_version,
        "catalog_version": trace.catalog_version,
        "state": trace.state,
        "reason_codes": list(trace.reason_codes),
        "formula_hash": trace.formula_hash,
        "input_hash": trace.input_hash,
        "result_hash": trace.result_hash,
        "trace_hash": trace.trace_hash,
    }


def _run_response(row: FinancialCalculationRun) -> FinancialCalculationRunResponse:
    _verify_run_record(row)
    return FinancialCalculationRunResponse(
        schema_version=1,
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        requested_by_user_id=row.requested_by_user_id,
        intake_id=row.intake_id,
        source_key=row.source_key,
        file_hash=row.file_hash,
        contract_key=row.contract_key,
        contract_version=row.contract_version,
        contract_hash=row.contract_hash,
        mapping_version=row.mapping_version,
        mapping_hash=row.mapping_hash,
        reconciliation_group_hash=row.reconciliation_group_hash,
        formula_id=row.formula_id,
        formula_version=row.formula_version,
        engine_version=row.engine_version,
        catalog_version=row.catalog_version,
        state=cast(Literal["calculated", "incomplete"], row.state),
        reason_codes=tuple(row.reason_codes),
        formula_hash=row.formula_hash,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        trace_hash=row.trace_hash,
        evidence_snapshot_hash=row.evidence_snapshot_hash,
        evidence_snapshot=row.evidence_snapshot,
        created_at=row.created_at,
    )


def _verify_run_record(row: FinancialCalculationRun) -> None:
    try:
        snapshot = row.evidence_snapshot
        intake_snapshot = snapshot.get("intake")
        if not isinstance(intake_snapshot, dict):
            raise ValueError("run evidence is missing its intake reference")
        valid = (
            content_hash(snapshot) == row.evidence_snapshot_hash
            and snapshot.get("run_id") == str(row.id)
            and snapshot.get("tenant_id") == str(row.tenant_id)
            and snapshot.get("company_id") == str(row.company_id)
            and snapshot.get("captured_at") == row.captured_at.astimezone(UTC).isoformat()
            and intake_snapshot.get("intake_id") == str(row.intake_id)
            and intake_snapshot.get("source_key") == row.source_key
            and intake_snapshot.get("file_hash") == row.file_hash
            and intake_snapshot.get("contract_key") == row.contract_key
            and intake_snapshot.get("contract_version") == row.contract_version
            and intake_snapshot.get("contract_hash") == row.contract_hash
            and intake_snapshot.get("mapping_version") == row.mapping_version
            and intake_snapshot.get("mapping_hash") == row.mapping_hash
            and snapshot.get("requested_group_hash") == row.reconciliation_group_hash
            and snapshot.get("formula_id") == row.formula_id
            and snapshot.get("formula_version") == row.formula_version
            and snapshot.get("engine_version") == row.engine_version
            and snapshot.get("catalog_version") == row.catalog_version
            and snapshot.get("state") == row.state
            and snapshot.get("reason_codes") == row.reason_codes
            and snapshot.get("formula_hash") == row.formula_hash
            and snapshot.get("input_hash") == row.input_hash
            and snapshot.get("result_hash") == row.result_hash
            and snapshot.get("trace_hash") == row.trace_hash
            and row.formula_hash == financial_calculation_formula_hash()
            and row.formula_id == SOURCE_GROUP_SUBTOTAL_ID
            and row.formula_version == SOURCE_GROUP_SUBTOTAL_VERSION
        )
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ApiError(
            409,
            "financial_calculation_evidence_integrity_error",
            "Saved financial calculation evidence could not be verified.",
        )


def list_financial_calculation_formulas(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    settings: Settings,
) -> FinancialCalculationFormulaCatalogResponse:
    ensure_financial_calculations_enabled(settings)
    require_company_context_access(session, user, tenant_id, "finance:read")
    return FinancialCalculationFormulaCatalogResponse(
        schema_version=1,
        catalog_version=FINANCIAL_CALCULATION_CATALOG_VERSION,
        items=FINANCIAL_FORMULA_CATALOG,
    )


def create_financial_calculation_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    request: FinancialCalculationRequest,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> FinancialCalculationCreateResponse:
    ensure_financial_calculations_enabled(settings)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("calculation capture time must include a timezone")
    now = now.astimezone(UTC)
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    run_id = uuid4()
    input_snapshot = _make_input_snapshot(
        session,
        user,
        tenant_id,
        intake_id,
        request,
        now,
        settings=settings,
    )
    trace = calculate_financial_calculation(
        run_id,
        input_snapshot,
        identity_secret=settings.app_secret_key.get_secret_value().encode("utf-8"),
    )
    evidence_snapshot = _redacted_evidence_snapshot(trace)
    intake_reference = trace.input_snapshot.intake
    row = FinancialCalculationRun(
        id=run_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        intake_id=intake_id,
        source_key=intake_reference.source_key,
        file_hash=intake_reference.file_hash,
        contract_key=intake_reference.contract_key,
        contract_version=intake_reference.contract_version,
        contract_hash=intake_reference.contract_hash,
        mapping_version=intake_reference.mapping_version,
        mapping_hash=intake_reference.mapping_hash,
        reconciliation_group_hash=trace.input_snapshot.requested_group_hash,
        formula_id=SOURCE_GROUP_SUBTOTAL_ID,
        formula_version=SOURCE_GROUP_SUBTOTAL_VERSION,
        engine_version=trace.engine_version,
        catalog_version=trace.catalog_version,
        state=trace.state,
        reason_codes=list(trace.reason_codes),
        formula_hash=trace.formula_hash,
        input_hash=trace.input_hash,
        result_hash=trace.result_hash,
        trace_hash=trace.trace_hash,
        evidence_snapshot_hash=content_hash(evidence_snapshot),
        evidence_snapshot=evidence_snapshot,
        captured_at=now,
        created_at=now,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "financial_calculation.run_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="financial_calculation_run",
        target_id=run_id,
        request_id=request_id,
        details={
            "intake_id": intake_id,
            "formula_id": SOURCE_GROUP_SUBTOTAL_ID,
            "formula_version": SOURCE_GROUP_SUBTOTAL_VERSION,
            "state": trace.state,
            "formula_hash": trace.formula_hash,
            "input_hash": trace.input_hash,
            "result_hash": trace.result_hash,
            "trace_hash": trace.trace_hash,
        },
    )
    return FinancialCalculationCreateResponse(
        schema_version=1,
        run=_run_response(row),
        trace=trace,
    )


def get_financial_calculation_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    *,
    settings: Settings,
) -> FinancialCalculationRunResponse:
    ensure_financial_calculations_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    row = session.scalar(
        select(FinancialCalculationRun).where(
            FinancialCalculationRun.id == run_id,
            FinancialCalculationRun.tenant_id == tenant_id,
            FinancialCalculationRun.company_id == access.company.id,
            FinancialCalculationRun.intake_id == intake_id,
        )
    )
    if row is None:
        raise _not_found()
    return _run_response(row)


def verify_financial_calculation_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    request: FinancialCalculationRequest,
    *,
    settings: Settings,
    request_id: UUID | None = None,
) -> FinancialCalculationReplayVerificationReport:
    ensure_financial_calculations_enabled(settings)
    # Replay accepts raw amount strings and acts as a commitment-verification oracle; require
    # the stricter write capability even though this endpoint does not mutate the saved run.
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    row = session.scalar(
        select(FinancialCalculationRun).where(
            FinancialCalculationRun.id == run_id,
            FinancialCalculationRun.tenant_id == tenant_id,
            FinancialCalculationRun.company_id == access.company.id,
            FinancialCalculationRun.intake_id == intake_id,
        )
    )
    if row is None:
        raise _not_found()
    _verify_run_record(row)
    rebuilt_snapshot = _make_input_snapshot(
        session,
        user,
        tenant_id,
        intake_id,
        request,
        row.captured_at.astimezone(UTC),
        settings=settings,
    )
    replayed = calculate_financial_calculation(
        run_id,
        rebuilt_snapshot,
        identity_secret=settings.app_secret_key.get_secret_value().encode("utf-8"),
    )
    replay_report = verify_financial_calculation_replay(
        replayed,
        identity_secret=settings.app_secret_key.get_secret_value().encode("utf-8"),
    )
    reasons = list(replay_report.reason_codes)
    if replayed.formula_hash != row.formula_hash:
        reasons.append("persisted_formula_hash_mismatch")
    if replayed.input_hash != row.input_hash:
        reasons.append("persisted_input_hash_mismatch")
    if replayed.result_hash != row.result_hash:
        reasons.append("persisted_result_hash_mismatch")
    if replayed.trace_hash != row.trace_hash:
        reasons.append("persisted_trace_hash_mismatch")
    if replayed.state != row.state:
        reasons.append("persisted_state_mismatch")
    return FinancialCalculationReplayVerificationReport(
        schema_version=1,
        passed=not reasons,
        reason_codes=tuple(dict.fromkeys(reasons)),
        run_id=run_id,
        recorded_trace_hash=row.trace_hash,
        replayed_trace_hash=replayed.trace_hash,
        recorded_input_hash=row.input_hash,
        replayed_input_hash=replayed.input_hash,
        core_replay_passed=replay_report.core_replay_passed,
        recorded_evidence_integrity_valid=True,
    )
