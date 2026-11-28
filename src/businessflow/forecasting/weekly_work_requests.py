from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.forecasting.contracts import (
    AwareDateTime,
    CodeText,
    ForecastContractModel,
    ForecastDatasetCreate,
    ForecastDatasetObservation,
    ForecastDatasetSourceLineage,
    ForecastDimensionDefinition,
    ForecastSourceReference,
    HashText,
    LabelText,
    PeriodDate,
    UUIDText,
)
from businessflow.metrics.snapshot import content_hash

MAX_WORK_REQUEST_RECEIPT_VERSIONS = 20_000
MAX_WORK_REQUEST_PERIODS = 10_000
_SOURCE_EVENT_KEY = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"


class WeeklyWorkRequestSnapshotError(ValueError):
    """Synthetic source evidence cannot support a safe weekly request target."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class WorkRequestReceiptVersion(ForecastContractModel):
    """Synthetic-only version of one stable, pseudonymous work-request receipt."""

    event_id: UUIDText
    event_version_id: UUIDText
    source_event_key: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=160, pattern=_SOURCE_EVENT_KEY),
    ]
    request_key_hash: HashText
    version: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    previous_event_version_id: UUIDText | None = None
    event_hash: HashText
    receipt_state: Literal["received", "invalidated"]
    first_received_at: AwareDateTime | None
    workflow_key: CodeText | None
    recorded_at: AwareDateTime
    correction_reason: Annotated[
        str | None, StringConstraints(strict=True, min_length=10, max_length=500)
    ] = None

    @field_validator("first_received_at", "recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("receipt timestamps must include a timezone")
        return value

    @field_validator("correction_reason")
    @classmethod
    def validate_correction_reason(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value.strip()) < 10 or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("correction_reason must be meaningful text without control characters")
        return value

    @model_validator(mode="after")
    def validate_receipt_version(self) -> WorkRequestReceiptVersion:
        if self.version == 1:
            if self.previous_event_version_id is not None or self.correction_reason is not None:
                raise ValueError(
                    "the first receipt version cannot have a predecessor or correction"
                )
            if self.receipt_state != "received":
                raise ValueError("the initial request version must be a received event")
        elif self.previous_event_version_id is None or self.correction_reason is None:
            raise ValueError("corrected receipt versions require predecessor and reason evidence")

        if self.receipt_state == "received":
            if self.first_received_at is None or self.workflow_key is None:
                raise ValueError("received requests require first_received_at and workflow_key")
            if self.first_received_at.astimezone(UTC) > self.recorded_at.astimezone(UTC):
                raise ValueError(
                    "first_received_at cannot be later than when the source recorded it"
                )
        elif self.first_received_at is not None or self.workflow_key is not None:
            raise ValueError(
                "invalidated receipt versions cannot claim a target timestamp or workflow"
            )
        return self


class WeeklyWorkRequestCoverage(ForecastContractModel):
    """Explicit synthetic evidence about whether one whole Monday-start week was observed."""

    week_start: PeriodDate
    status: Literal["complete", "incomplete", "unknown"]
    covered_through: AwareDateTime | None = None
    known_at: AwareDateTime | None = None
    evidence_hash: HashText | None = None
    reason_code: CodeText | None = None

    @field_validator("covered_through", "known_at")
    @classmethod
    def require_aware_coverage_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("coverage timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_coverage_evidence(self) -> WeeklyWorkRequestCoverage:
        if self.week_start.isoweekday() != 1:
            raise ValueError("coverage periods must start on Monday")
        if self.status == "complete":
            if self.covered_through is None or self.known_at is None or self.evidence_hash is None:
                raise ValueError(
                    "complete weeks require coverage, knowledge-time, and hash evidence"
                )
            if self.reason_code is not None:
                raise ValueError("complete weeks cannot carry an incomplete-coverage reason")
            if self.known_at.astimezone(UTC) < self.covered_through.astimezone(UTC):
                raise ValueError("complete coverage cannot be known before the covered period ends")
        elif self.reason_code is None:
            raise ValueError("incomplete or unknown weeks require an explicit reason code")
        return self


class WeeklyWorkRequestSnapshot(ForecastContractModel):
    """A synthetic-only, Company-scoped snapshot for the weekly incoming-request contract."""

    schema_version: Literal[1] = 1
    data_origin: Literal["synthetic_fixture"]
    company_id: UUIDText
    source_id: CodeText
    source_profile_version_id: UUIDText
    source_snapshot_id: UUIDText
    workflow_key: CodeText
    workflow_label: LabelText
    workflow_support_evidence_hash: HashText
    calendar_timezone: Annotated[
        str,
        StringConstraints(
            strict=True,
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+.-]+)*$",
        ),
    ]
    period_start: PeriodDate
    period_end: PeriodDate
    snapshot_as_of: AwareDateTime
    first_received_semantics: Literal["explicit_synthetic_fixture_timestamp"]
    receipt_versions: Annotated[
        list[WorkRequestReceiptVersion], Field(max_length=MAX_WORK_REQUEST_RECEIPT_VERSIONS)
    ] = Field(default_factory=list)
    period_coverage: Annotated[
        list[WeeklyWorkRequestCoverage], Field(max_length=MAX_WORK_REQUEST_PERIODS)
    ] = Field(default_factory=list)

    @field_validator("calendar_timezone")
    @classmethod
    def known_calendar_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("calendar_timezone must be a recognized IANA timezone") from exc
        return value

    @field_validator("snapshot_as_of")
    @classmethod
    def require_snapshot_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot_as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_snapshot_periods(self) -> WeeklyWorkRequestSnapshot:
        if self.period_start.isoweekday() != 1 or self.period_end.isoweekday() != 1:
            raise ValueError("weekly request snapshot boundaries must start on Monday")
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        period_count = (self.period_end - self.period_start).days // 7 + 1
        if period_count > MAX_WORK_REQUEST_PERIODS:
            raise ValueError("weekly request snapshot exceeds the supported period limit")
        if any(
            item.recorded_at.astimezone(UTC) > self.snapshot_as_of.astimezone(UTC)
            for item in self.receipt_versions
        ):
            raise ValueError("receipt versions cannot be newer than the declared source snapshot")
        if any(
            item.known_at is not None
            and item.known_at.astimezone(UTC) > self.snapshot_as_of.astimezone(UTC)
            for item in self.period_coverage
        ) or any(
            item.covered_through is not None
            and item.covered_through.astimezone(UTC) > self.snapshot_as_of.astimezone(UTC)
            for item in self.period_coverage
        ):
            raise ValueError("coverage evidence cannot be newer than the declared source snapshot")
        coverage_starts = [item.week_start for item in self.period_coverage]
        if len(coverage_starts) != len(set(coverage_starts)):
            raise ValueError("a weekly request snapshot cannot repeat a coverage period")
        if any(not self.period_start <= start <= self.period_end for start in coverage_starts):
            raise ValueError("coverage periods must fall within the declared snapshot range")
        return self


class WeeklyWorkRequestPeriodReadiness(ForecastContractModel):
    week_start: PeriodDate
    coverage_status: Literal["complete", "incomplete", "unknown"]
    status: Literal["ready", "blocked", "missing"]
    distinct_request_count: Annotated[int, Field(strict=True, ge=0)] | None = None
    correction_version_count: Annotated[int, Field(strict=True, ge=0)]
    duplicate_request_stream_count: Annotated[int, Field(strict=True, ge=0)]
    replayed_event_version_count: Annotated[int, Field(strict=True, ge=0)]
    reason_codes: list[CodeText]
    record_hash: HashText | None = None


class WeeklyWorkRequestReadiness(ForecastContractModel):
    status: Literal["ready_for_synthetic_evaluation", "blocked"]
    scope: Literal["synthetic_only_not_business_or_accuracy_evidence"]
    eligibility_for_rolling_origin: Literal["candidate", "blocked"]
    source_authority: Literal["not_established"]
    period_count: Annotated[int, Field(strict=True, ge=1)]
    complete_period_count: Annotated[int, Field(strict=True, ge=0)]
    missing_or_incomplete_period_count: Annotated[int, Field(strict=True, ge=0)]
    total_distinct_request_count: Annotated[int, Field(strict=True, ge=0)] | None
    correction_version_count: Annotated[int, Field(strict=True, ge=0)]
    corrected_request_count: Annotated[int, Field(strict=True, ge=0)]
    duplicate_request_stream_count: Annotated[int, Field(strict=True, ge=0)]
    replayed_event_version_count: Annotated[int, Field(strict=True, ge=0)]
    reason_codes: list[CodeText]
    warning_codes: list[CodeText]
    periods: list[WeeklyWorkRequestPeriodReadiness]


class WeeklyWorkRequestDatasetBuild(ForecastContractModel):
    dataset: ForecastDatasetCreate | None
    readiness: WeeklyWorkRequestReadiness


@dataclass(frozen=True, slots=True)
class _ResolvedRequest:
    request_key_hash: str
    current: WorkRequestReceiptVersion
    versions: tuple[WorkRequestReceiptVersion, ...]
    duplicate_stream_count: int


def _week_start(value: datetime, timezone: ZoneInfo) -> date:
    local_date = value.astimezone(timezone).date()
    return local_date - timedelta(days=local_date.weekday())


def _week_end_exclusive(week_start: date, timezone: ZoneInfo) -> datetime:
    return datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=timezone)


def _selected_workflow_period(
    version: WorkRequestReceiptVersion,
    *,
    workflow_key: str,
    timezone: ZoneInfo,
    period_set: set[date],
) -> date | None:
    if (
        version.receipt_state != "received"
        or version.workflow_key != workflow_key
        or version.first_received_at is None
    ):
        return None
    week_start = _week_start(version.first_received_at, timezone)
    return week_start if week_start in period_set else None


def _periods(snapshot: WeeklyWorkRequestSnapshot) -> list[date]:
    result: list[date] = []
    current = snapshot.period_start
    while current <= snapshot.period_end:
        result.append(current)
        current += timedelta(days=7)
    return result


def _canonical_source_hash(
    snapshot: WeeklyWorkRequestSnapshot,
    receipt_versions: list[WorkRequestReceiptVersion],
    replay_counts_by_version: dict[UUID, int],
) -> str:
    payload = snapshot.model_dump(mode="json")
    payload["receipt_versions"] = [
        {
            **item.model_dump(mode="json"),
            "replay_count": replay_counts_by_version.get(item.event_version_id, 0),
        }
        for item in sorted(
            receipt_versions,
            key=lambda item: (str(item.event_id), item.version, str(item.event_version_id)),
        )
    ]
    payload["period_coverage"] = [
        item.model_dump(mode="json")
        for item in sorted(snapshot.period_coverage, key=lambda item: item.week_start)
    ]
    return content_hash(payload)


def _canonicalize_replays(
    snapshot: WeeklyWorkRequestSnapshot,
) -> tuple[list[WorkRequestReceiptVersion], dict[UUID, int]]:
    by_version_id: dict[UUID, WorkRequestReceiptVersion] = {}
    replay_counts: dict[UUID, int] = defaultdict(int)
    for version in snapshot.receipt_versions:
        existing = by_version_id.get(version.event_version_id)
        if existing is None:
            by_version_id[version.event_version_id] = version
        elif existing != version:
            raise WeeklyWorkRequestSnapshotError("conflicting_event_version_replay")
        else:
            replay_counts[version.event_version_id] += 1
    return list(by_version_id.values()), dict(replay_counts)


def _event_chains(
    receipt_versions: list[WorkRequestReceiptVersion],
) -> dict[UUID, list[WorkRequestReceiptVersion]]:
    by_event_id: dict[UUID, list[WorkRequestReceiptVersion]] = defaultdict(list)
    source_keys: dict[str, UUID] = {}
    for version in receipt_versions:
        existing_event_id = source_keys.get(version.source_event_key)
        if existing_event_id is not None and existing_event_id != version.event_id:
            raise WeeklyWorkRequestSnapshotError("source_event_key_reused")
        source_keys[version.source_event_key] = version.event_id
        by_event_id[version.event_id].append(version)

    result: dict[UUID, list[WorkRequestReceiptVersion]] = {}
    for event_id, versions in by_event_id.items():
        ordered = sorted(versions, key=lambda item: item.version)
        first = ordered[0]
        if any(
            item.source_event_key != first.source_event_key
            or item.request_key_hash != first.request_key_hash
            for item in ordered
        ):
            raise WeeklyWorkRequestSnapshotError("receipt_identity_changed_across_versions")
        if [item.version for item in ordered] != list(range(1, len(ordered) + 1)):
            raise WeeklyWorkRequestSnapshotError("receipt_version_chain_has_gap_or_conflict")
        for index, item in enumerate(ordered):
            if index == 0:
                continue
            predecessor = ordered[index - 1]
            if item.previous_event_version_id != predecessor.event_version_id:
                raise WeeklyWorkRequestSnapshotError("receipt_version_predecessor_mismatch")
            if item.recorded_at.astimezone(UTC) < predecessor.recorded_at.astimezone(UTC):
                raise WeeklyWorkRequestSnapshotError("receipt_version_chain_is_not_chronological")
        result[event_id] = ordered
    return result


def _resolve_requests(
    chains: dict[UUID, list[WorkRequestReceiptVersion]],
) -> tuple[list[_ResolvedRequest], int, int]:
    by_request_key: dict[
        str, list[tuple[list[WorkRequestReceiptVersion], WorkRequestReceiptVersion]]
    ] = defaultdict(list)
    correction_version_count = 0
    for versions in chains.values():
        current = versions[-1]
        by_request_key[current.request_key_hash].append((versions, current))
        correction_version_count += sum(item.version > 1 for item in versions)

    resolved: list[_ResolvedRequest] = []
    duplicate_stream_count = 0
    for request_key_hash, streams in sorted(by_request_key.items()):
        signatures = {
            (
                current.receipt_state,
                current.first_received_at.astimezone(UTC) if current.first_received_at else None,
                current.workflow_key,
            )
            for _versions, current in streams
        }
        if len(signatures) != 1:
            raise WeeklyWorkRequestSnapshotError("conflicting_duplicate_request_identity")
        streams.sort(key=lambda item: str(item[1].event_id))
        current = streams[0][1]
        all_versions = tuple(
            sorted(
                (version for versions, _current in streams for version in versions),
                key=lambda item: (str(item.event_id), item.version),
            )
        )
        repeated_streams = len(streams) - 1
        duplicate_stream_count += repeated_streams
        resolved.append(
            _ResolvedRequest(
                request_key_hash=request_key_hash,
                current=current,
                versions=all_versions,
                duplicate_stream_count=repeated_streams,
            )
        )
    return resolved, duplicate_stream_count, correction_version_count


def _request_lineage_for_period(
    requests: list[_ResolvedRequest],
    *,
    week_start: date,
    workflow_key: str,
    timezone: ZoneInfo,
    period_set: set[date],
    replay_counts_by_version: dict[UUID, int],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for request in requests:
        if not any(
            _selected_workflow_period(
                item,
                workflow_key=workflow_key,
                timezone=timezone,
                period_set=period_set,
            )
            == week_start
            for item in request.versions
        ):
            continue
        records.append(
            {
                "request_key_hash": request.request_key_hash,
                "duplicate_request_stream_count": request.duplicate_stream_count,
                "versions": [
                    {
                        "event_id": str(item.event_id),
                        "event_version_id": str(item.event_version_id),
                        "previous_event_version_id": (
                            str(item.previous_event_version_id)
                            if item.previous_event_version_id is not None
                            else None
                        ),
                        "version": item.version,
                        "event_hash": item.event_hash,
                        "replay_count": replay_counts_by_version.get(item.event_version_id, 0),
                        "receipt_state": item.receipt_state,
                        "first_received_at": (
                            item.first_received_at.astimezone(UTC).isoformat()
                            if item.first_received_at is not None
                            else None
                        ),
                        "workflow_key": item.workflow_key,
                        "recorded_at": item.recorded_at.astimezone(UTC).isoformat(),
                    }
                    for item in request.versions
                ],
            }
        )
    return sorted(records, key=lambda item: str(item["request_key_hash"]))


def _blocked_result(
    snapshot: WeeklyWorkRequestSnapshot,
    reason_code: str,
) -> WeeklyWorkRequestDatasetBuild:
    coverage_by_week = {item.week_start: item for item in snapshot.period_coverage}
    timezone = ZoneInfo(snapshot.calendar_timezone)
    periods: list[WeeklyWorkRequestPeriodReadiness] = []
    for week_start in _periods(snapshot):
        coverage = coverage_by_week.get(week_start)
        coverage_status: Literal["complete", "incomplete", "unknown"] = (
            coverage.status if coverage is not None else "unknown"
        )
        if (
            coverage is not None
            and coverage.status == "complete"
            and coverage.covered_through is not None
            and coverage.covered_through.astimezone(UTC)
            < _week_end_exclusive(week_start, timezone).astimezone(UTC)
        ):
            coverage_status = "incomplete"
        periods.append(
            WeeklyWorkRequestPeriodReadiness(
                week_start=week_start,
                coverage_status=coverage_status,
                status="blocked",
                distinct_request_count=None,
                correction_version_count=0,
                duplicate_request_stream_count=0,
                replayed_event_version_count=0,
                reason_codes=[reason_code],
            )
        )
    complete_period_count = sum(item.coverage_status == "complete" for item in periods)
    return WeeklyWorkRequestDatasetBuild(
        dataset=None,
        readiness=WeeklyWorkRequestReadiness(
            status="blocked",
            scope="synthetic_only_not_business_or_accuracy_evidence",
            eligibility_for_rolling_origin="blocked",
            source_authority="not_established",
            period_count=len(periods),
            complete_period_count=complete_period_count,
            missing_or_incomplete_period_count=len(periods) - complete_period_count,
            total_distinct_request_count=None,
            correction_version_count=0,
            corrected_request_count=0,
            duplicate_request_stream_count=0,
            replayed_event_version_count=0,
            reason_codes=[reason_code],
            warning_codes=[],
            periods=periods,
        ),
    )


def build_weekly_work_request_dataset(
    snapshot: WeeklyWorkRequestSnapshot,
) -> WeeklyWorkRequestDatasetBuild:
    """Build a versioned M17 input only from explicit synthetic first-receipt evidence.

    A blocked build retains no guessed count: incomplete weeks are represented as missing, while
    malformed revisions and conflicting duplicate request identities produce no dataset at all.
    Point-in-time blockers remain attached to any materialized M17 snapshot and prevent its use for
    forecasting/evaluation through the M17 service guard.
    """
    timezone = ZoneInfo(snapshot.calendar_timezone)
    try:
        receipt_versions, replay_counts_by_version = _canonicalize_replays(snapshot)
        chains = _event_chains(receipt_versions)
        requests, duplicate_stream_count, correction_version_count = _resolve_requests(chains)
    except WeeklyWorkRequestSnapshotError as exc:
        return _blocked_result(snapshot, exc.reason_code)
    replayed_version_count = sum(replay_counts_by_version.values())

    periods = _periods(snapshot)
    period_set = set(periods)
    coverage_by_week = {item.week_start: item for item in snapshot.period_coverage}
    source_snapshot_hash = _canonical_source_hash(
        snapshot, receipt_versions, replay_counts_by_version
    )
    point_in_time_reasons: set[str] = set()
    warnings: set[str] = set()
    if replayed_version_count:
        warnings.add("exact_event_version_replays_deduplicated")
    if duplicate_stream_count:
        warnings.add("duplicate_request_streams_deduplicated")
    if correction_version_count:
        warnings.add("receipt_corrections_resolved_as_of_snapshot")

    requests_by_period: dict[date, list[_ResolvedRequest]] = defaultdict(list)
    selected_requests_by_period: dict[date, list[_ResolvedRequest]] = defaultdict(list)
    correction_versions_by_period: dict[date, int] = defaultdict(int)
    duplicate_streams_by_period: dict[date, int] = defaultdict(int)
    replayed_versions_by_period: dict[date, int] = defaultdict(int)
    point_in_time_reasons_by_period: dict[date, set[str]] = defaultdict(set)
    version_by_id: dict[UUID, WorkRequestReceiptVersion] = {}
    previous_version_by_id: dict[UUID, WorkRequestReceiptVersion | None] = {}

    for request in requests:
        relevant_periods = {
            period
            for version in request.versions
            if (
                period := _selected_workflow_period(
                    version,
                    workflow_key=snapshot.workflow_key,
                    timezone=timezone,
                    period_set=period_set,
                )
            )
            is not None
        }
        for period in relevant_periods:
            requests_by_period[period].append(request)
            duplicate_streams_by_period[period] += request.duplicate_stream_count
        current_period = _selected_workflow_period(
            request.current,
            workflow_key=snapshot.workflow_key,
            timezone=timezone,
            period_set=period_set,
        )
        if current_period is not None:
            selected_requests_by_period[current_period].append(request)

    for chain in chains.values():
        for index, version in enumerate(chain):
            version_by_id[version.event_version_id] = version
            previous_version_by_id[version.event_version_id] = (
                chain[index - 1] if index > 0 else None
            )
            affected_versions = [version]
            if index > 0:
                affected_versions.append(chain[index - 1])
            affected_periods = {
                period
                for affected in affected_versions
                if (
                    period := _selected_workflow_period(
                        affected,
                        workflow_key=snapshot.workflow_key,
                        timezone=timezone,
                        period_set=period_set,
                    )
                )
                is not None
            }
            if version.version > 1:
                for affected_period in affected_periods:
                    correction_versions_by_period[affected_period] += 1
            reason_code = (
                "receipt_correction_after_origin"
                if version.version > 1
                else "receipt_available_after_origin"
            )
            for affected_period in affected_periods:
                forecast_origin = _week_end_exclusive(affected_period, timezone)
                if version.recorded_at.astimezone(UTC) > forecast_origin.astimezone(UTC):
                    point_in_time_reasons_by_period[affected_period].add(reason_code)

    for version_id, replay_count in replay_counts_by_version.items():
        version = version_by_id[version_id]
        affected_versions = [version]
        previous_version = previous_version_by_id[version_id]
        if previous_version is not None:
            affected_versions.append(previous_version)
        affected_periods = {
            period
            for affected in affected_versions
            if (
                period := _selected_workflow_period(
                    affected,
                    workflow_key=snapshot.workflow_key,
                    timezone=timezone,
                    period_set=period_set,
                )
            )
            is not None
        }
        for affected_period in affected_periods:
            replayed_versions_by_period[affected_period] += replay_count

    period_readiness: list[WeeklyWorkRequestPeriodReadiness] = []
    observations: list[ForecastDatasetObservation] = []
    readiness_reasons: set[str] = set()
    complete_period_count = 0
    total_distinct_requests = 0
    for week_start in periods:
        coverage = coverage_by_week.get(week_start)
        coverage_status: Literal["complete", "incomplete", "unknown"] = (
            coverage.status if coverage is not None else "unknown"
        )
        week_end_exclusive = _week_end_exclusive(week_start, timezone)
        period_reasons: set[str] = set()
        period_point_in_time_reasons = set(point_in_time_reasons_by_period.get(week_start, set()))
        evidence_hash = coverage.evidence_hash if coverage is not None else None
        if coverage is None:
            period_reasons.add("period_coverage_evidence_missing")
            coverage_status = "unknown"
        elif coverage.status == "complete":
            if coverage.covered_through is None or coverage.covered_through.astimezone(
                UTC
            ) < week_end_exclusive.astimezone(UTC):
                period_reasons.add("period_coverage_ends_before_week_close")
                coverage_status = "incomplete"
        else:
            period_reasons.add(
                "source_period_incomplete"
                if coverage.status == "incomplete"
                else "source_period_coverage_unknown"
            )
        if (
            coverage is not None
            and coverage.known_at is not None
            and coverage.known_at.astimezone(UTC) > week_end_exclusive.astimezone(UTC)
        ):
            period_point_in_time_reasons.add("period_coverage_available_after_origin")
        point_in_time_reasons.update(period_point_in_time_reasons)
        period_reasons.update(period_point_in_time_reasons)

        if coverage_status == "complete":
            complete_period_count += 1
            period_requests = selected_requests_by_period[week_start]
            distinct_count = len(period_requests)
            total_distinct_requests += distinct_count
            target_value: str | None = str(distinct_count)
            status: Literal["ready", "blocked", "missing"] = (
                "blocked" if period_reasons else "ready"
            )
        else:
            distinct_count = None
            target_value = None
            status = "missing"
        if period_point_in_time_reasons and status == "ready":
            status = "blocked"
        readiness_reasons.update(period_reasons)
        period_lineage = _request_lineage_for_period(
            requests_by_period[week_start],
            week_start=week_start,
            workflow_key=snapshot.workflow_key,
            timezone=timezone,
            period_set=period_set,
            replay_counts_by_version=replay_counts_by_version,
        )
        coverage_lineage = coverage.model_dump(mode="json") if coverage is not None else None
        record_hash = content_hash(
            {
                "schema_version": 1,
                "source_snapshot_hash": source_snapshot_hash,
                "period_start": week_start.isoformat(),
                "calendar_timezone": snapshot.calendar_timezone,
                "workflow_key": snapshot.workflow_key,
                "coverage": coverage_lineage,
                "effective_coverage_status": coverage_status,
                "request_versions": period_lineage,
                "distinct_request_count": distinct_count,
                "reason_codes": sorted(period_reasons),
            }
        )
        period_readiness.append(
            WeeklyWorkRequestPeriodReadiness(
                week_start=week_start,
                coverage_status=coverage_status,
                status=status,
                distinct_request_count=distinct_count,
                correction_version_count=correction_versions_by_period[week_start],
                duplicate_request_stream_count=duplicate_streams_by_period[week_start],
                replayed_event_version_count=replayed_versions_by_period[week_start],
                reason_codes=sorted(period_reasons),
                record_hash=record_hash,
            )
        )
        observations.append(
            ForecastDatasetObservation(
                period_start=week_start,
                dimension_values={"workflow": snapshot.workflow_key},
                value=target_value,
                missing_reason=(";".join(sorted(period_reasons)) if target_value is None else None),
                period_coverage=coverage_status,
                coverage_evidence_hash=evidence_hash,
                source_reference=ForecastSourceReference(
                    evidence_type="synthetic_fixture",
                    source_id=snapshot.source_id,
                    source_hash=source_snapshot_hash,
                    record_locator=f"week:{week_start.isoformat()}",
                    record_hash=record_hash,
                ),
            )
        )

    readiness_reasons.update(point_in_time_reasons)
    readiness_status: Literal["ready_for_synthetic_evaluation", "blocked"] = (
        "ready_for_synthetic_evaluation"
        if not readiness_reasons and complete_period_count == len(periods)
        else "blocked"
    )
    if readiness_status == "blocked" and not readiness_reasons:
        readiness_reasons.add("weekly_request_readiness_not_established")
    point_in_time_status: Literal["eligible", "blocked"] = (
        "blocked" if point_in_time_reasons else "eligible"
    )
    lineage = ForecastDatasetSourceLineage(
        target_contract_key="operations.weekly_work_requests.first_received.v1",
        company_id=snapshot.company_id,
        source_id=snapshot.source_id,
        source_profile_version_id=snapshot.source_profile_version_id,
        source_snapshot_id=snapshot.source_snapshot_id,
        source_snapshot_hash=source_snapshot_hash,
        source_authority="synthetic_only_not_independent_source_verification",
        workflow_key=snapshot.workflow_key,
        workflow_support_evidence_hash=snapshot.workflow_support_evidence_hash,
        calendar_timezone=snapshot.calendar_timezone,
        week_start="monday",
        snapshot_as_of=snapshot.snapshot_as_of,
        first_received_semantics=snapshot.first_received_semantics,
        replayed_event_version_count=replayed_version_count,
        duplicate_request_stream_count=duplicate_stream_count,
        corrected_request_count=sum(
            any(item.version > 1 for item in request.versions) for request in requests
        ),
        readiness_status=readiness_status,
        readiness_reason_codes=sorted(readiness_reasons),
        point_in_time_status=point_in_time_status,
        point_in_time_reason_codes=sorted(point_in_time_reasons),
    )
    dataset = ForecastDatasetCreate(
        dataset_key="operations.weekly_work_requests",
        display_name="Weekly incoming work requests",
        data_origin="synthetic_fixture",
        source_lineage=lineage,
        target_key="operations.weekly_work_requests",
        target_label="Distinct work requests first received",
        unit="requests/week",
        frequency="weekly",
        dimensions=[ForecastDimensionDefinition(key="workflow", label="Workflow")],
        observations=observations,
    )
    return WeeklyWorkRequestDatasetBuild(
        dataset=dataset,
        readiness=WeeklyWorkRequestReadiness(
            status=readiness_status,
            scope="synthetic_only_not_business_or_accuracy_evidence",
            eligibility_for_rolling_origin=(
                "candidate" if readiness_status == "ready_for_synthetic_evaluation" else "blocked"
            ),
            source_authority="not_established",
            period_count=len(periods),
            complete_period_count=complete_period_count,
            missing_or_incomplete_period_count=len(periods) - complete_period_count,
            total_distinct_request_count=(
                total_distinct_requests
                if readiness_status == "ready_for_synthetic_evaluation"
                else None
            ),
            correction_version_count=correction_version_count,
            corrected_request_count=lineage.corrected_request_count,
            duplicate_request_stream_count=duplicate_stream_count,
            replayed_event_version_count=replayed_version_count,
            reason_codes=sorted(readiness_reasons),
            warning_codes=sorted(warnings),
            periods=period_readiness,
        ),
    )
