from __future__ import annotations

import contextlib
import hmac
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.calculation_core.decimal import decimal_text, parse_decimal_text
from businessflow.config import Settings
from businessflow.direct_cash_forecasts.calculation import (
    calculate_direct_cash_forecast,
    direct_cash_input_snapshot_hash,
    direct_cash_result_snapshot_hash,
)
from businessflow.direct_cash_forecasts.catalog import (
    DIRECT_CASH_FORECAST_CATALOG_VERSION,
    DIRECT_CASH_FORECAST_CONTRACT_DEFINITION,
    DIRECT_CASH_FORECAST_CONTRACT_KEY,
    DIRECT_CASH_FORECAST_CONTRACT_VERSION,
    DIRECT_CASH_FORECAST_ENGINE_VERSION,
    DIRECT_CASH_FORECAST_FORMULA_HASH,
    DIRECT_CASH_FORECAST_FORMULA_ID,
    DIRECT_CASH_FORECAST_FORMULA_VERSION,
    DIRECT_CASH_FORECAST_HORIZON_WEEKS,
    DIRECT_CASH_FORECAST_MAX_GROUP_ROWS,
)
from businessflow.direct_cash_forecasts.commitments import (
    DIRECT_CASH_REQUEST_COMMITMENT_KEY_VERSION,
    direct_cash_amount_input_fingerprint,
)
from businessflow.direct_cash_forecasts.contracts import (
    DirectCashForecastContractResponse,
    DirectCashForecastCreateResponse,
    DirectCashForecastInputItem,
    DirectCashForecastInputSnapshot,
    DirectCashForecastReplayVerificationReport,
    DirectCashForecastRequest,
    DirectCashForecastResultSnapshot,
    DirectCashForecastRunResponse,
    DirectCashForecastTrace,
    DirectCashMovementRequest,
    DirectCashOpeningRequest,
)
from businessflow.direct_cash_forecasts.models import DirectCashForecastRun
from businessflow.errors import ApiError
from businessflow.finance_sources.commitments import (
    AMOUNT_COMMITMENT_KEY_VERSION,
    finance_amount_commitment,
)
from businessflow.finance_sources.models import FinanceSourceRowEvidence
from businessflow.finance_sources.schemas import FinanceSourceIntakeResponse
from businessflow.finance_sources.service import get_finance_source_intake
from businessflow.financial_calculations.contracts import (
    FinanceSourceEvidenceReference,
    FinanceSourceIntakeReference,
)
from businessflow.financial_calculations.service import (
    _group_reference,
    _supported_source_contract,
)
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access


def ensure_direct_cash_forecasts_enabled(settings: Settings | None) -> None:
    """Fail closed unless the M14, M15, and dedicated M16 test flags are explicitly enabled."""
    if (
        getattr(settings, "direct_cash_forecasts_enabled", None) is not True
        or getattr(settings, "financial_calculations_enabled", None) is not True
        or getattr(settings, "finance_source_intake_enabled", None) is not True
        or getattr(settings, "app_env", None) == "production"
    ):
        raise ApiError(
            503,
            "direct_cash_forecasts_disabled",
            "Direct cash forecasts are disabled by deployment configuration.",
        )


SubmittedCashItem = DirectCashOpeningRequest | DirectCashMovementRequest
SubmittedCashInput = tuple[
    str,
    Literal["opening_cash", "movement"],
    SubmittedCashItem,
]


def _not_found() -> ApiError:
    return ApiError(404, "direct_cash_forecast_run_not_found", "Forecast run was not found.")


def _unsupported_source() -> ApiError:
    return ApiError(
        409,
        "direct_cash_forecast_source_unsupported",
        "The M14 intake or reconciliation group is not supported by this M16 contract.",
    )


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


def _intake_reference(intake: FinanceSourceIntakeResponse) -> FinanceSourceIntakeReference:
    return FinanceSourceIntakeReference(
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


def _verify_amount_submission(
    amount_text: str | None,
    row: FinanceSourceRowEvidence | None,
    *,
    intake: FinanceSourceIntakeReference,
    requested_group_hash: str,
    identity_secret: bytes,
) -> tuple[Literal["verified", "missing", "invalid", "duplicate"], Decimal | None]:
    if amount_text is None:
        return "missing", None
    try:
        amount = parse_decimal_text(amount_text)
    except ValueError:
        return "invalid", None
    if row is None:
        return "invalid", None
    if row.row_status == "duplicate":
        return "duplicate", None
    if row.row_status != "valid" or row.reconciliation_group_hash != requested_group_hash:
        return "invalid", None
    if (
        row.amount_commitment_hash is None
        or row.amount_commitment_key_version != AMOUNT_COMMITMENT_KEY_VERSION
    ):
        return "invalid", None
    expected = finance_amount_commitment(
        secret=identity_secret,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        intake_id=intake.intake_id,
        source_key=intake.source_key,
        file_hash=intake.file_hash,
        source_row_number=row.source_row_number,
        source_row_hash=row.source_row_hash,
        source_record_identity_hash=row.source_record_identity_hash,
        reconciliation_group_hash=requested_group_hash,
        contract_key=intake.contract_key,
        contract_version=intake.contract_version,
        contract_hash=intake.contract_hash,
        mapping_version=intake.mapping_version,
        mapping_hash=intake.mapping_hash,
        canonical_amount_text=decimal_text(amount),
    )
    return (
        ("verified", amount)
        if hmac.compare_digest(expected, row.amount_commitment_hash)
        else (
            "invalid",
            None,
        )
    )


def _make_input_item(
    *,
    input_id: str,
    origin: Literal["opening_cash", "movement", "missing_source_row"],
    source_row_number: int,
    amount_text: str | None,
    cash_flow_type: Literal["opening_cash", "receipt", "disbursement", "unknown", "unsupported"],
    basis: Literal["actual", "forecast", "unknown", "unsupported"] | None,
    cash_date: date | None,
    supersedes_source_row_number: int | None,
    row: FinanceSourceRowEvidence | None,
    intake: FinanceSourceIntakeReference,
    request_snapshot_id: UUID,
    requested_group_hash: str,
    duplicate_state: Literal["duplicate", "conflicting"] | None,
    issues: set[str],
    identity_secret: bytes,
) -> tuple[DirectCashForecastInputItem, Decimal | None]:
    evidence = _evidence_reference(row) if row is not None else None
    amount_status: Literal[
        "verified", "missing", "invalid", "duplicate", "conflicting", "not_submitted"
    ]
    amount: Decimal | None = None
    if duplicate_state is not None:
        amount_status = duplicate_state
    elif origin == "missing_source_row":
        amount_status = "not_submitted"
    else:
        verified_status, amount = _verify_amount_submission(
            amount_text,
            row,
            intake=intake,
            requested_group_hash=requested_group_hash,
            identity_secret=identity_secret,
        )
        # The row must also match the selected group. The caller passes this as a separate
        # issue when it does not, so a valid amount commitment cannot move across groups.
        amount_status = verified_status
        if amount_status == "missing":
            issues.add("amount_missing")
        elif amount_status == "duplicate":
            issues.add("duplicate_source_evidence")
        elif amount_status == "invalid":
            issues.add("amount_commitment_unverifiable")
    fingerprint = direct_cash_amount_input_fingerprint(
        secret=identity_secret,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        intake_id=intake.intake_id,
        snapshot_id=request_snapshot_id,
        input_id=input_id,
        source_row_number=source_row_number,
        amount_text=amount_text,
    )
    item = DirectCashForecastInputItem(
        input_id=input_id,
        origin=origin,
        source_row_number=source_row_number,
        amount_fingerprint=fingerprint,
        amount_fingerprint_key_version=DIRECT_CASH_REQUEST_COMMITMENT_KEY_VERSION,
        amount_commitment_status=amount_status,
        cash_flow_type=cash_flow_type,
        basis=basis,
        cash_date=cash_date,
        supersedes_source_row_number=supersedes_source_row_number,
        evidence=evidence,
        issue_codes=tuple(sorted(issues)),
    )
    return item, amount if amount_status == "verified" and not issues else None


def _input_snapshot(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    request: DirectCashForecastRequest,
    captured_at: datetime,
    *,
    settings: Settings,
    company_id: UUID,
    company_timezone: str,
) -> tuple[DirectCashForecastInputSnapshot, dict[str, Decimal]]:
    intake = get_finance_source_intake(session, user, tenant_id, intake_id, settings=settings)
    if intake.company_id != company_id:
        raise _unsupported_source()
    _supported_source_contract(session, intake)
    group = _group_reference(intake, request.reconciliation_group_hash)
    intake_ref = _intake_reference(intake)

    row_filters = (
        FinanceSourceRowEvidence.tenant_id == tenant_id,
        FinanceSourceRowEvidence.company_id == company_id,
        FinanceSourceRowEvidence.intake_id == intake_id,
    )
    all_row_count = (
        session.scalar(
            select(func.count()).select_from(FinanceSourceRowEvidence).where(*row_filters)
        )
        or 0
    )
    group_rows: list[FinanceSourceRowEvidence] = []
    group_row_count = 0
    if group is not None:
        group_filters = (
            *row_filters,
            FinanceSourceRowEvidence.reconciliation_group_hash == group.reconciliation_group_hash,
        )
        group_row_count = (
            session.scalar(
                select(func.count()).select_from(FinanceSourceRowEvidence).where(*group_filters)
            )
            or 0
        )
        if group_row_count <= DIRECT_CASH_FORECAST_MAX_GROUP_ROWS:
            group_rows = list(
                session.scalars(
                    select(FinanceSourceRowEvidence)
                    .where(*group_filters)
                    .order_by(FinanceSourceRowEvidence.source_row_number)
                )
            )

    submitted: list[SubmittedCashInput] = []
    if request.opening_cash is not None:
        submitted.append(("opening-cash", "opening_cash", request.opening_cash))
    for index, movement in enumerate(request.movements):
        submitted.append((f"movement-{index:05d}", "movement", movement))

    submitted_row_numbers = [item.source_row_number for _, _, item in submitted]
    request_counts = Counter(submitted_row_numbers)
    parsed_amounts_by_row: dict[int, set[Decimal]] = defaultdict(set)
    for _, _, submitted_item in submitted:
        if submitted_item.amount_text is None:
            continue
        with contextlib.suppress(ValueError):
            parsed_amounts_by_row[submitted_item.source_row_number].add(
                parse_decimal_text(submitted_item.amount_text)
            )
    duplicate_states: dict[int, Literal["duplicate", "conflicting"]] = {
        row_number: ("conflicting" if len(parsed_amounts_by_row[row_number]) > 1 else "duplicate")
        for row_number, count in request_counts.items()
        if count > 1
    }
    requested_rows: list[FinanceSourceRowEvidence] = []
    if submitted_row_numbers:
        requested_rows = list(
            session.scalars(
                select(FinanceSourceRowEvidence).where(
                    *row_filters,
                    FinanceSourceRowEvidence.source_row_number.in_(
                        sorted(set(submitted_row_numbers))
                    ),
                )
            )
        )
    rows_by_number = {row.source_row_number: row for row in requested_rows}
    identity_secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    verified_amounts: dict[str, Decimal] = {}
    opening_snapshot: DirectCashForecastInputItem | None = None
    movement_snapshots: list[DirectCashForecastInputItem] = []
    requested_group_row_numbers: set[int] = set()
    eligible_group_hash = group.reconciliation_group_hash if group is not None else ""

    for input_id, origin, request_item in submitted:
        row_number = request_item.source_row_number
        amount_text = request_item.amount_text
        row = rows_by_number.get(row_number)
        if (
            row is not None
            and group is not None
            and row.reconciliation_group_hash == group.reconciliation_group_hash
        ):
            requested_group_row_numbers.add(row_number)
        issues: set[str] = set()
        if row is None:
            issues.add("source_row_evidence_missing")
        elif group is None or row.reconciliation_group_hash != request.reconciliation_group_hash:
            issues.add("source_group_mismatch")
        elif row.row_status == "duplicate":
            issues.add("duplicate_source_evidence")
        elif row.row_status != "valid":
            issues.add("invalid_source_evidence")

        duplicate_state = duplicate_states.get(row_number)
        if duplicate_state is not None:
            issues.add(
                "conflicting_source_row_reference"
                if duplicate_state == "conflicting"
                else "duplicate_source_row_reference"
            )

        if origin == "opening_cash":
            assert isinstance(request_item, DirectCashOpeningRequest)
            item, amount = _make_input_item(
                input_id=input_id,
                origin="opening_cash",
                source_row_number=row_number,
                amount_text=amount_text,
                cash_flow_type="opening_cash",
                basis=None,
                cash_date=None,
                supersedes_source_row_number=None,
                row=row,
                intake=intake_ref,
                request_snapshot_id=request.snapshot_id,
                requested_group_hash=eligible_group_hash,
                duplicate_state=duplicate_state,
                issues=issues,
                identity_secret=identity_secret,
            )
            opening_snapshot = item
        else:
            assert isinstance(request_item, DirectCashMovementRequest)
            item, amount = _make_input_item(
                input_id=input_id,
                origin="movement",
                source_row_number=row_number,
                amount_text=amount_text,
                cash_flow_type=request_item.cash_flow_type,
                basis=request_item.basis,
                cash_date=request_item.cash_date,
                supersedes_source_row_number=request_item.supersedes_source_row_number,
                row=row,
                intake=intake_ref,
                request_snapshot_id=request.snapshot_id,
                requested_group_hash=eligible_group_hash,
                duplicate_state=duplicate_state,
                issues=issues,
                identity_secret=identity_secret,
            )
            movement_snapshots.append(item)
        if amount is not None:
            verified_amounts[input_id] = amount

    if group is not None and group_row_count <= DIRECT_CASH_FORECAST_MAX_GROUP_ROWS:
        for row in group_rows:
            if row.source_row_number in requested_group_row_numbers:
                continue
            item, _ = _make_input_item(
                input_id=f"missing-row-{row.source_row_number}",
                origin="missing_source_row",
                source_row_number=row.source_row_number,
                amount_text=None,
                cash_flow_type="unknown",
                basis="unknown",
                cash_date=None,
                supersedes_source_row_number=None,
                row=row,
                intake=intake_ref,
                request_snapshot_id=request.snapshot_id,
                requested_group_hash=eligible_group_hash,
                duplicate_state=None,
                issues={"cash_item_missing_from_request"},
                identity_secret=identity_secret,
            )
            movement_snapshots.append(item)

    evidence_integrity: Literal["verified", "incomplete", "not_verifiable"]
    if group is None:
        evidence_integrity = "not_verifiable"
    elif (
        all_row_count != intake.total_rows
        or group_row_count != group.source_row_count
        or (
            group_row_count <= DIRECT_CASH_FORECAST_MAX_GROUP_ROWS
            and len(group_rows) != group_row_count
        )
    ):
        evidence_integrity = "incomplete"
    else:
        evidence_integrity = "verified"

    snapshot = DirectCashForecastInputSnapshot(
        schema_version=1,
        snapshot_id=request.snapshot_id,
        tenant_id=tenant_id,
        company_id=company_id,
        captured_at=captured_at,
        as_of_date=request.as_of_date,
        company_timezone=company_timezone,
        intake=intake_ref,
        requested_group_hash=request.reconciliation_group_hash,
        source_evidence_integrity_status=evidence_integrity,
        group=group,
        opening_cash=opening_snapshot,
        movements=tuple(movement_snapshots),
    )
    return snapshot, verified_amounts


def direct_cash_forecast_formula_hash() -> str:
    return content_hash(DIRECT_CASH_FORECAST_CONTRACT_DEFINITION)


def _evidence_snapshot_hash(
    *, formula_definition: dict[str, object], input_snapshot: dict[str, object]
) -> str:
    return content_hash(
        {
            "schema_version": 1,
            "formula_definition": formula_definition,
            "input_snapshot": input_snapshot,
        }
    )


def _trace_hash(
    *,
    run_id: UUID,
    formula_hash: str,
    input_hash: str,
    result_hash: str,
    calculation_state: str,
    reason_codes: list[str],
    captured_at: datetime,
) -> str:
    return content_hash(
        {
            "schema_version": 1,
            "run_id": str(run_id),
            "engine_version": DIRECT_CASH_FORECAST_ENGINE_VERSION,
            "catalog_version": DIRECT_CASH_FORECAST_CATALOG_VERSION,
            "formula_id": DIRECT_CASH_FORECAST_FORMULA_ID,
            "formula_version": DIRECT_CASH_FORECAST_FORMULA_VERSION,
            "formula_hash": formula_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "calculation_state": calculation_state,
            "forecast_completeness_status": "not_established_by_m14",
            "reason_codes": reason_codes,
            "captured_at": captured_at.astimezone(UTC).isoformat(),
        }
    )


def _input_snapshot_from_json_value(value: object) -> DirectCashForecastInputSnapshot:
    return DirectCashForecastInputSnapshot.model_validate_json(json.dumps(value))


def _result_snapshot_from_json_value(value: object) -> DirectCashForecastResultSnapshot:
    return DirectCashForecastResultSnapshot.model_validate_json(json.dumps(value))


def _trace_from_run(row: DirectCashForecastRun) -> DirectCashForecastTrace:
    return DirectCashForecastTrace(
        schema_version=1,
        run_id=row.id,
        engine_version=row.engine_version,
        catalog_version=row.catalog_version,
        formula_id=row.formula_id,
        formula_version=row.formula_version,
        formula_definition=row.formula_definition,
        formula_hash=row.formula_hash,
        input_snapshot=_input_snapshot_from_json_value(row.input_snapshot),
        input_hash=row.input_hash,
        result_snapshot=_result_snapshot_from_json_value(row.result_snapshot),
        result_hash=row.result_hash,
        calculation_state=cast(Literal["calculated", "incomplete"], row.state),
        trace_hash=row.trace_hash,
    )


def _verify_run_record(row: DirectCashForecastRun) -> None:
    try:
        input_snapshot = _input_snapshot_from_json_value(row.input_snapshot)
        result_snapshot = _result_snapshot_from_json_value(row.result_snapshot)
        formula_definition = row.formula_definition
        formula_hash_matches = (
            isinstance(formula_definition, dict)
            and content_hash(formula_definition) == row.formula_hash
        )
        formula_metadata = (
            formula_definition.get("formula") if isinstance(formula_definition, dict) else None
        )
        formula_metadata_matches = (
            isinstance(formula_metadata, dict)
            and formula_metadata.get("formula_id") == row.formula_id
            and formula_metadata.get("formula_version") == row.formula_version
        )
        input_payload = input_snapshot.model_dump(mode="json")
        input_hash = direct_cash_input_snapshot_hash(input_snapshot)
        result_hash = direct_cash_result_snapshot_hash(result_snapshot)
        evidence_hash = _evidence_snapshot_hash(
            formula_definition=formula_definition,
            input_snapshot=input_payload,
        )
        valid = (
            formula_hash_matches
            and row.formula_id == DIRECT_CASH_FORECAST_FORMULA_ID
            and row.formula_version == DIRECT_CASH_FORECAST_FORMULA_VERSION
            and formula_metadata_matches
            and row.engine_version == DIRECT_CASH_FORECAST_ENGINE_VERSION
            and row.catalog_version == DIRECT_CASH_FORECAST_CATALOG_VERSION
            and input_hash == row.input_hash
            and result_hash == row.result_hash
            and evidence_hash == row.evidence_snapshot_hash
            and row.state == result_snapshot.calculation_state
            and row.reason_codes == list(result_snapshot.reason_codes)
            and input_snapshot.tenant_id == row.tenant_id
            and input_snapshot.company_id == row.company_id
            and input_snapshot.intake.intake_id == row.intake_id
            and input_snapshot.intake.source_key == row.source_key
            and input_snapshot.intake.file_hash == row.file_hash
            and input_snapshot.intake.contract_key == row.contract_key
            and input_snapshot.intake.contract_version == row.contract_version
            and input_snapshot.intake.contract_hash == row.contract_hash
            and input_snapshot.intake.mapping_version == row.mapping_version
            and input_snapshot.intake.mapping_hash == row.mapping_hash
            and input_snapshot.requested_group_hash == row.reconciliation_group_hash
            and input_snapshot.captured_at.astimezone(UTC) == row.captured_at.astimezone(UTC)
            and input_snapshot.as_of_date == row.as_of_date
            and input_snapshot.company_timezone == row.company_timezone
            and input_snapshot.horizon_weeks == row.horizon_weeks
            and result_snapshot.currency_code == row.currency_code
            and result_snapshot.as_of_date == row.as_of_date
            and result_snapshot.company_timezone == row.company_timezone
            and _trace_hash(
                run_id=row.id,
                formula_hash=row.formula_hash,
                input_hash=row.input_hash,
                result_hash=row.result_hash,
                calculation_state=row.state,
                reason_codes=list(row.reason_codes),
                captured_at=row.captured_at,
            )
            == row.trace_hash
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ApiError(
            409,
            "direct_cash_forecast_evidence_integrity_error",
            "Saved direct-cash forecast evidence could not be verified.",
        )


def _run_response(row: DirectCashForecastRun) -> DirectCashForecastRunResponse:
    _verify_run_record(row)
    input_snapshot = _input_snapshot_from_json_value(row.input_snapshot)
    result_snapshot = _result_snapshot_from_json_value(row.result_snapshot)
    return DirectCashForecastRunResponse(
        schema_version=1,
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        requested_by_user_id=row.requested_by_user_id,
        intake_id=row.intake_id,
        source_key=row.source_key,
        source_file_hash=row.file_hash,
        contract_key=row.contract_key,
        contract_version=row.contract_version,
        contract_hash=row.contract_hash,
        mapping_version=row.mapping_version,
        mapping_hash=row.mapping_hash,
        reconciliation_group_hash=row.reconciliation_group_hash,
        formula_id=row.formula_id,
        formula_version=row.formula_version,
        formula_definition=row.formula_definition,
        formula_hash=row.formula_hash,
        engine_version=row.engine_version,
        catalog_version=row.catalog_version,
        calculation_state=cast(Literal["calculated", "incomplete"], row.state),
        forecast_completeness_status="not_established_by_m14",
        reason_codes=tuple(row.reason_codes),
        horizon_weeks=cast(Literal[13], row.horizon_weeks),
        as_of_date=row.as_of_date,
        company_timezone=row.company_timezone,
        currency_code=row.currency_code,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        trace_hash=row.trace_hash,
        evidence_snapshot_hash=row.evidence_snapshot_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        captured_at=row.captured_at,
        created_at=row.created_at,
    )


def get_direct_cash_forecast_contract(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    settings: Settings,
) -> DirectCashForecastContractResponse:
    ensure_direct_cash_forecasts_enabled(settings)
    require_company_context_access(session, user, tenant_id, "finance:read")
    return DirectCashForecastContractResponse(
        schema_version=1,
        contract_key=DIRECT_CASH_FORECAST_CONTRACT_KEY,
        contract_version=DIRECT_CASH_FORECAST_CONTRACT_VERSION,
        formula_id=DIRECT_CASH_FORECAST_FORMULA_ID,
        formula_version=DIRECT_CASH_FORECAST_FORMULA_VERSION,
        engine_version=DIRECT_CASH_FORECAST_ENGINE_VERSION,
        catalog_version=DIRECT_CASH_FORECAST_CATALOG_VERSION,
        definition=DIRECT_CASH_FORECAST_CONTRACT_DEFINITION,
        definition_hash=DIRECT_CASH_FORECAST_FORMULA_HASH,
    )


def create_direct_cash_forecast_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    request: DirectCashForecastRequest,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> DirectCashForecastCreateResponse:
    ensure_direct_cash_forecasts_enabled(settings)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("direct-cash forecast capture time must include a timezone")
    now = now.astimezone(UTC)
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    input_snapshot, verified_amounts = _input_snapshot(
        session,
        user,
        tenant_id,
        intake_id,
        request,
        now,
        settings=settings,
        company_id=access.company.id,
        company_timezone=access.company.timezone,
    )
    run_id = uuid4()
    result_snapshot = calculate_direct_cash_forecast(input_snapshot, verified_amounts)
    formula_definition = DIRECT_CASH_FORECAST_CONTRACT_DEFINITION
    formula_hash = direct_cash_forecast_formula_hash()
    input_payload = input_snapshot.model_dump(mode="json")
    result_payload = result_snapshot.model_dump(mode="json")
    input_hash = direct_cash_input_snapshot_hash(input_snapshot)
    result_hash = direct_cash_result_snapshot_hash(result_snapshot)
    evidence_hash = _evidence_snapshot_hash(
        formula_definition=formula_definition,
        input_snapshot=input_payload,
    )
    trace_hash = _trace_hash(
        run_id=run_id,
        formula_hash=formula_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        calculation_state=result_snapshot.calculation_state,
        reason_codes=list(result_snapshot.reason_codes),
        captured_at=now,
    )
    intake = input_snapshot.intake
    row = DirectCashForecastRun(
        id=run_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        intake_id=intake_id,
        source_key=intake.source_key,
        file_hash=intake.file_hash,
        contract_key=intake.contract_key,
        contract_version=intake.contract_version,
        contract_hash=intake.contract_hash,
        mapping_version=intake.mapping_version,
        mapping_hash=intake.mapping_hash,
        reconciliation_group_hash=request.reconciliation_group_hash,
        formula_id=DIRECT_CASH_FORECAST_FORMULA_ID,
        formula_version=DIRECT_CASH_FORECAST_FORMULA_VERSION,
        formula_definition=formula_definition,
        formula_hash=formula_hash,
        engine_version=DIRECT_CASH_FORECAST_ENGINE_VERSION,
        catalog_version=DIRECT_CASH_FORECAST_CATALOG_VERSION,
        state=result_snapshot.calculation_state,
        reason_codes=list(result_snapshot.reason_codes),
        horizon_weeks=DIRECT_CASH_FORECAST_HORIZON_WEEKS,
        as_of_date=request.as_of_date,
        company_timezone=access.company.timezone,
        currency_code=result_snapshot.currency_code,
        input_hash=input_hash,
        result_hash=result_hash,
        trace_hash=trace_hash,
        evidence_snapshot_hash=evidence_hash,
        input_snapshot=input_payload,
        result_snapshot=result_payload,
        captured_at=now,
        created_at=now,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "direct_cash_forecast.run_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="direct_cash_forecast_run",
        target_id=run_id,
        request_id=request_id,
        details={
            "intake_id": intake_id,
            "group_hash": request.reconciliation_group_hash,
            "formula_id": DIRECT_CASH_FORECAST_FORMULA_ID,
            "formula_version": DIRECT_CASH_FORECAST_FORMULA_VERSION,
            "formula_hash": formula_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "trace_hash": trace_hash,
            "calculation_state": result_snapshot.calculation_state,
            "forecast_completeness_status": "not_established_by_m14",
            "horizon_weeks": DIRECT_CASH_FORECAST_HORIZON_WEEKS,
            "as_of_date": request.as_of_date,
            "currency_code": result_snapshot.currency_code,
        },
    )
    session.flush()
    return DirectCashForecastCreateResponse(
        schema_version=1,
        run=_run_response(row),
        trace=_trace_from_run(row),
    )


def _get_run(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    run_id: UUID,
) -> DirectCashForecastRun:
    row = session.scalar(
        select(DirectCashForecastRun).where(
            DirectCashForecastRun.id == run_id,
            DirectCashForecastRun.tenant_id == tenant_id,
            DirectCashForecastRun.company_id == company_id,
            DirectCashForecastRun.intake_id == intake_id,
        )
    )
    if row is None:
        raise _not_found()
    return row


def get_direct_cash_forecast_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    *,
    settings: Settings,
) -> DirectCashForecastRunResponse:
    ensure_direct_cash_forecasts_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    return _run_response(
        _get_run(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            intake_id=intake_id,
            run_id=run_id,
        )
    )


def verify_direct_cash_forecast_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    request: DirectCashForecastRequest,
    *,
    settings: Settings,
    request_id: UUID | None = None,
) -> DirectCashForecastReplayVerificationReport:
    ensure_direct_cash_forecasts_enabled(settings)
    # Replay resubmits finance amounts and could otherwise act as a commitment oracle.
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    row = _get_run(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake_id,
        run_id=run_id,
    )
    _verify_run_record(row)
    saved_input = _input_snapshot_from_json_value(row.input_snapshot)
    current_compatible = (
        row.engine_version == DIRECT_CASH_FORECAST_ENGINE_VERSION
        and row.formula_hash == DIRECT_CASH_FORECAST_FORMULA_HASH
        and row.formula_version == DIRECT_CASH_FORECAST_FORMULA_VERSION
        and row.catalog_version == DIRECT_CASH_FORECAST_CATALOG_VERSION
    )
    if not current_compatible:
        raise ApiError(
            409,
            "direct_cash_forecast_replay_version_unsupported",
            (
                "This run was created by a different M16 engine or formula version and cannot "
                "be replayed here."
            ),
        )
    rebuilt_input, verified_amounts = _input_snapshot(
        session,
        user,
        tenant_id,
        intake_id,
        request,
        row.captured_at.astimezone(UTC),
        settings=settings,
        company_id=access.company.id,
        company_timezone=saved_input.company_timezone,
    )
    replayed_result = calculate_direct_cash_forecast(rebuilt_input, verified_amounts)
    replayed_input_hash = direct_cash_input_snapshot_hash(rebuilt_input)
    replayed_result_hash = direct_cash_result_snapshot_hash(replayed_result)
    replayed_evidence_hash = _evidence_snapshot_hash(
        formula_definition=DIRECT_CASH_FORECAST_CONTRACT_DEFINITION,
        input_snapshot=rebuilt_input.model_dump(mode="json"),
    )
    replayed_trace_hash = _trace_hash(
        run_id=run_id,
        formula_hash=DIRECT_CASH_FORECAST_FORMULA_HASH,
        input_hash=replayed_input_hash,
        result_hash=replayed_result_hash,
        calculation_state=replayed_result.calculation_state,
        reason_codes=list(replayed_result.reason_codes),
        captured_at=row.captured_at.astimezone(UTC),
    )
    reasons: list[str] = []
    if replayed_input_hash != row.input_hash:
        reasons.append("persisted_input_hash_mismatch")
    if replayed_evidence_hash != row.evidence_snapshot_hash:
        reasons.append("persisted_evidence_snapshot_hash_mismatch")
    if replayed_result_hash != row.result_hash:
        reasons.append("persisted_result_hash_mismatch")
    if replayed_trace_hash != row.trace_hash:
        reasons.append("persisted_trace_hash_mismatch")
    if replayed_result.calculation_state != row.state:
        reasons.append("persisted_state_mismatch")
    return DirectCashForecastReplayVerificationReport(
        schema_version=1,
        passed=not reasons,
        reason_codes=tuple(reasons),
        run_id=run_id,
        recorded_trace_hash=row.trace_hash,
        replayed_trace_hash=replayed_trace_hash,
        recorded_input_hash=row.input_hash,
        replayed_input_hash=replayed_input_hash,
        recorded_result_hash=row.result_hash,
        replayed_result_hash=replayed_result_hash,
        recorded_evidence_integrity_valid=True,
        current_formula_compatible=True,
    )
