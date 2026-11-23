from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    localcontext,
)
from pathlib import PurePosixPath
from typing import BinaryIO, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.calculation_core.decimal import (
    decimal_text,
    parse_decimal_text,
    validate_currency_code,
)
from businessflow.calculation_core.verification import reconcile_values
from businessflow.company_data.quality import evaluate_freshness
from businessflow.config import Settings
from businessflow.data_imports.parsers import (
    ImportFileError,
    ParsedSheet,
    parse_file,
    read_limited_upload,
)
from businessflow.errors import ApiError
from businessflow.finance_sources.contracts import (
    FINANCE_SOURCE_CONTRACT_DEFINITION,
    FINANCE_SOURCE_CONTRACT_HASH,
    FINANCE_SOURCE_CONTRACT_KEY,
    FINANCE_SOURCE_CONTRACT_VERSION,
    FINANCE_SOURCE_ENGINE_VERSION,
    FINANCE_SOURCE_GOVERNANCE_STATUS,
    FINANCE_SOURCE_RECONCILIATION_METHOD,
    FINANCE_SOURCE_SNAPSHOT_SCHEMA_VERSION,
)
from businessflow.finance_sources.models import (
    FinanceSourceContract,
    FinanceSourceIntake,
    FinanceSourceMapping,
    FinanceSourceRowEvidence,
)
from businessflow.finance_sources.schemas import (
    FINANCE_FIELDS,
    FinanceField,
    FinanceReconciliationControl,
    FinanceSourceContractResponse,
    FinanceSourceGovernanceStatus,
    FinanceSourceIntakeMetadata,
    FinanceSourceIntakePage,
    FinanceSourceIntakeResponse,
    FinanceSourceRowEvidencePage,
    FinanceSourceRowEvidenceResponse,
)
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._~:-]{16,128}$")
MAX_PAGE_SIZE = 100
MAX_PAGE_OFFSET = 100_000
RECONCILIATION_PRECISION = 256

GroupKey = tuple[str, date, date, str]
RowStatus = Literal["valid", "duplicate", "invalid"]


def ensure_finance_source_intake_enabled(settings: Settings | None) -> None:
    """Fail closed unless explicitly enabled for a non-production process."""
    if (
        getattr(settings, "finance_source_intake_enabled", None) is not True
        or getattr(settings, "app_env", None) == "production"
    ):
        raise ApiError(
            503,
            "finance_source_intake_disabled",
            "Finance source intake is disabled by deployment configuration.",
        )


@dataclass(frozen=True, slots=True)
class _ValidatedRow:
    row_number: int
    source_row_hash: str
    source_record_identity_hash: str
    reconciliation_group_hash: str | None
    group_key: GroupKey | None
    amount: Decimal | None
    row_status: RowStatus
    issues: list[dict[str, object]]


def _safe_file_name(filename: str | None) -> str:
    if not filename:
        raise ApiError(422, "invalid_file_name", "A CSV file name is required.")
    leaf = filename.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[\x00-\x1f\x7f]", "_", leaf).strip(" .")
    if not safe or len(safe) > 255:
        raise ApiError(422, "invalid_file_name", "The file name is invalid or too long.")
    if PurePosixPath(safe).suffix.casefold() != ".csv":
        raise ApiError(
            415, "unsupported_file", "Finance source intake accepts UTF-8 CSV files only."
        )
    return safe


def _lock_for_key(
    session: Session,
    *,
    namespace: str,
    tenant_id: UUID,
    company_id: UUID,
    key: str,
) -> None:
    material = b"\0".join(
        (namespace.encode("utf-8"), tenant_id.bytes, company_id.bytes, key.encode("utf-8"))
    )
    lock_key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _published_contract(session: Session) -> FinanceSourceContract:
    contract = session.get(
        FinanceSourceContract,
        (FINANCE_SOURCE_CONTRACT_KEY, FINANCE_SOURCE_CONTRACT_VERSION),
    )
    if (
        contract is None
        or contract.definition_hash != FINANCE_SOURCE_CONTRACT_HASH
        or contract.definition != FINANCE_SOURCE_CONTRACT_DEFINITION
        or content_hash(contract.definition) != contract.definition_hash
    ):
        raise ApiError(
            503,
            "finance_source_contract_unavailable",
            "The published finance source contract could not be verified.",
        )
    return contract


def get_finance_source_contracts(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    settings: Settings,
) -> list[FinanceSourceContractResponse]:
    ensure_finance_source_intake_enabled(settings)
    require_company_context_access(session, user, tenant_id, "finance:read")
    _published_contract(session)
    rows = session.scalars(
        select(FinanceSourceContract).order_by(
            FinanceSourceContract.contract_key, FinanceSourceContract.version
        )
    ).all()
    return [
        FinanceSourceContractResponse(
            contract_key=row.contract_key,
            version=row.version,
            definition=row.definition,
            definition_hash=row.definition_hash,
        )
        for row in rows
    ]


def _validate_mapping(sheet: ParsedSheet, metadata: FinanceSourceIntakeMetadata) -> None:
    headers = set(sheet.headers)
    mapped_headers = set(metadata.field_mapping.values())
    ignored_headers = {item.header for item in metadata.ignored_columns}
    if len(mapped_headers) != len(metadata.field_mapping):
        raise ApiError(
            422,
            "invalid_finance_mapping",
            "Each finance contract field must map to a distinct source header.",
        )
    if not mapped_headers.issubset(headers) or not ignored_headers.issubset(headers):
        raise ApiError(
            422,
            "invalid_finance_mapping",
            "The mapping references a source header that is not present in the CSV file.",
        )
    if mapped_headers & ignored_headers or mapped_headers | ignored_headers != headers:
        raise ApiError(
            422,
            "invalid_finance_mapping",
            "Every source header must be mapped exactly once or ignored with a rationale.",
        )


def _canonical_mapping(
    metadata: FinanceSourceIntakeMetadata,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    field_mapping: dict[str, str] = {
        field: metadata.field_mapping[field] for field in FINANCE_FIELDS
    }
    ignored_columns = sorted(
        (item.model_dump(mode="json") for item in metadata.ignored_columns),
        key=lambda item: cast(str, item["header"]),
    )
    return field_mapping, ignored_columns


def _mapping_hash(
    *,
    source_key: str,
    field_mapping: dict[str, str],
    ignored_columns: list[dict[str, str]],
    contract_hash: str,
) -> str:
    return content_hash(
        {
            "source_key": source_key,
            "contract_key": FINANCE_SOURCE_CONTRACT_KEY,
            "contract_version": FINANCE_SOURCE_CONTRACT_VERSION,
            "contract_hash": contract_hash,
            "field_mapping": field_mapping,
            "ignored_columns": ignored_columns,
        }
    )


def _verify_mapping_record(mapping: FinanceSourceMapping) -> None:
    expected_hash = _mapping_hash(
        source_key=mapping.source_key,
        field_mapping=mapping.field_mapping,
        ignored_columns=mapping.ignored_columns,
        contract_hash=mapping.contract_hash,
    )
    if (
        mapping.contract_key != FINANCE_SOURCE_CONTRACT_KEY
        or mapping.contract_version != FINANCE_SOURCE_CONTRACT_VERSION
        or mapping.contract_hash != FINANCE_SOURCE_CONTRACT_HASH
        or mapping.mapping_hash != expected_hash
    ):
        raise ApiError(
            500,
            "finance_source_mapping_integrity_error",
            "Saved finance source mapping evidence could not be verified.",
        )


def _get_or_create_mapping(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    user_id: UUID,
    source_key: str,
    field_mapping: dict[str, str],
    ignored_columns: list[dict[str, str]],
    contract_hash: str,
) -> FinanceSourceMapping:
    _lock_for_key(
        session,
        namespace="finance-source-mapping",
        tenant_id=tenant_id,
        company_id=company_id,
        key=source_key,
    )
    mapping_hash = _mapping_hash(
        source_key=source_key,
        field_mapping=field_mapping,
        ignored_columns=ignored_columns,
        contract_hash=contract_hash,
    )
    latest = session.scalar(
        select(FinanceSourceMapping)
        .where(
            FinanceSourceMapping.tenant_id == tenant_id,
            FinanceSourceMapping.company_id == company_id,
            FinanceSourceMapping.source_key == source_key,
        )
        .order_by(FinanceSourceMapping.mapping_version.desc())
        .limit(1)
    )
    if latest is not None:
        _verify_mapping_record(latest)
        if latest.mapping_hash == mapping_hash:
            if latest.field_mapping != field_mapping or latest.ignored_columns != ignored_columns:
                raise ApiError(
                    500,
                    "finance_source_mapping_integrity_error",
                    "Saved finance source mapping evidence could not be verified.",
                )
            return latest
    mapping = FinanceSourceMapping(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=company_id,
        source_key=source_key,
        mapping_version=(latest.mapping_version + 1) if latest is not None else 1,
        contract_key=FINANCE_SOURCE_CONTRACT_KEY,
        contract_version=FINANCE_SOURCE_CONTRACT_VERSION,
        contract_hash=contract_hash,
        mapping_hash=mapping_hash,
        field_mapping=field_mapping,
        ignored_columns=ignored_columns,
        created_by_user_id=user_id,
    )
    session.add(mapping)
    session.flush()
    return mapping


def _parse_date(value: str | None) -> date | None:
    if value is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _valid_opaque_value(value: str | None, *, maximum_length: int) -> bool:
    return (
        value is not None
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= maximum_length
    )


def _identity_hash(
    *,
    secret: bytes,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    source_key: str,
    source_record_id: str | None,
    row_number: int,
) -> str:
    identity = source_record_id if source_record_id is not None else f"invalid-row:{row_number}"
    material = "\0".join(
        (str(tenant_id), str(company_id), str(intake_id), source_key, identity)
    ).encode("utf-8")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def _group_key_hash(source_key: str, group_key: GroupKey) -> str:
    reconciliation_key, period_start, period_end, currency = group_key
    return content_hash(
        {
            "source_key": source_key,
            "reconciliation_key": reconciliation_key,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "currency": currency,
        }
    )


def _source_row_hash(sheet_name: str, row_number: int, values: list[str | None]) -> str:
    return content_hash({"worksheet": sheet_name, "row_number": row_number, "values": values})


def _validate_rows(
    sheet: ParsedSheet,
    metadata: FinanceSourceIntakeMetadata,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    identity_secret: bytes,
) -> list[_ValidatedRow]:
    header_positions = {header: index for index, header in enumerate(sheet.headers)}
    field_positions = {
        field: header_positions[metadata.field_mapping[field]] for field in FINANCE_FIELDS
    }

    def raw_value(row_values: list[str | None], field: FinanceField) -> str | None:
        return row_values[field_positions[field]]

    identity_values: list[str | None] = []
    for source_row in sheet.rows:
        value = raw_value(source_row.values, "source_record_id")
        identity_values.append(value if _valid_opaque_value(value, maximum_length=256) else None)
    identity_counts = Counter(value for value in identity_values if value is not None)
    duplicate_identities = {value for value, count in identity_counts.items() if count > 1}

    results: list[_ValidatedRow] = []
    for source_row, source_record_id in zip(sheet.rows, identity_values, strict=True):
        values = source_row.values
        issues: list[dict[str, object]] = []
        if source_record_id is None:
            issues.append(
                {
                    "code": "source_record_id_invalid",
                    "severity": "error",
                    "field": "source_record_id",
                }
            )
        elif source_record_id in duplicate_identities:
            issues.append(
                {
                    "code": "duplicate_source_record_id",
                    "severity": "error",
                    "field": "source_record_id",
                }
            )

        reconciliation_key = raw_value(values, "reconciliation_key")
        if not _valid_opaque_value(reconciliation_key, maximum_length=256):
            issues.append(
                {
                    "code": "reconciliation_key_invalid",
                    "severity": "error",
                    "field": "reconciliation_key",
                }
            )
            reconciliation_key = None

        start_raw = raw_value(values, "period_start")
        end_raw = raw_value(values, "period_end")
        period_start = _parse_date(start_raw)
        period_end = _parse_date(end_raw)
        if period_start is None:
            issues.append(
                {"code": "period_start_invalid", "severity": "error", "field": "period_start"}
            )
        if period_end is None:
            issues.append(
                {"code": "period_end_invalid", "severity": "error", "field": "period_end"}
            )
        if period_start is not None and period_end is not None and period_end < period_start:
            issues.append(
                {"code": "period_bounds_invalid", "severity": "error", "field": "period_end"}
            )
            period_start = None
            period_end = None

        currency_raw = raw_value(values, "currency")
        currency: str | None = None
        if currency_raw is not None:
            try:
                currency = validate_currency_code(currency_raw)
            except ValueError:
                currency = None
        if currency is None:
            issues.append({"code": "currency_invalid", "severity": "error", "field": "currency"})

        amount_raw = raw_value(values, "amount")
        amount: Decimal | None = None
        if amount_raw is not None:
            try:
                amount = parse_decimal_text(amount_raw)
            except ValueError:
                amount = None
        if amount is None:
            issues.append({"code": "amount_invalid", "severity": "error", "field": "amount"})

        group_key: GroupKey | None = None
        group_hash: str | None = None
        if (
            reconciliation_key is not None
            and period_start is not None
            and period_end is not None
            and currency is not None
        ):
            group_key = (reconciliation_key, period_start, period_end, currency)
            group_hash = _group_key_hash(metadata.source_key, group_key)

        has_duplicate = any(item["code"] == "duplicate_source_record_id" for item in issues)
        has_nonduplicate_issue = any(
            item["code"] != "duplicate_source_record_id" for item in issues
        )
        if has_duplicate:
            row_status: RowStatus = "duplicate"
        elif has_nonduplicate_issue:
            row_status = "invalid"
        else:
            row_status = "valid"

        results.append(
            _ValidatedRow(
                row_number=source_row.row_number,
                source_row_hash=_source_row_hash(sheet.name, source_row.row_number, values),
                source_record_identity_hash=_identity_hash(
                    secret=identity_secret,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    intake_id=intake_id,
                    source_key=metadata.source_key,
                    source_record_id=source_record_id,
                    row_number=source_row.row_number,
                ),
                reconciliation_group_hash=group_hash,
                group_key=group_key,
                amount=amount,
                row_status=row_status,
                issues=issues,
            )
        )
    return results


def _profile_columns(
    sheet: ParsedSheet,
    metadata: FinanceSourceIntakeMetadata,
) -> list[dict[str, object]]:
    field_by_header = {header: field for field, header in metadata.field_mapping.items()}
    ignored_by_header = {item.header: item.rationale for item in metadata.ignored_columns}
    profiles: list[dict[str, object]] = []
    for index, header in enumerate(sheet.headers):
        values = [row.values[index] for row in sheet.rows]
        nonblank = [value for value in values if value is not None and value.strip()]
        profile: dict[str, object] = {
            "header": header,
            "mapped_field": field_by_header.get(header),
            "ignored_rationale": ignored_by_header.get(header),
            "nonblank_count": len(nonblank),
            "blank_count": len(values) - len(nonblank),
            "distinct_nonblank_count": len(set(nonblank)),
        }
        profiles.append(profile)
    return profiles


def _sum_exact_amounts(amounts: list[Decimal]) -> str:
    context = Context(
        prec=RECONCILIATION_PRECISION,
        Emin=-999_999,
        Emax=999_999,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )
    with localcontext(context) as active_context:
        total = Decimal(0)
        for amount in amounts:
            total += amount
        if active_context.flags[Inexact] or active_context.flags[Rounded]:
            raise ValueError("source-group sum exceeded supported exact Decimal precision")
    return decimal_text(total)


def _control_group_key(control: FinanceReconciliationControl) -> GroupKey:
    return (
        control.reconciliation_key,
        control.period_start,
        control.period_end,
        control.currency,
    )


def _canonical_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _build_reconciliation_results(
    *,
    rows: list[_ValidatedRow],
    metadata: FinanceSourceIntakeMetadata,
    file_hash: str,
) -> tuple[list[dict[str, object]], str, int, int, int]:
    grouped_rows: dict[GroupKey, list[_ValidatedRow]] = defaultdict(list)
    row_hashes = {row.source_row_hash for row in rows}
    row_locators = {f"csv:CSV:row:{row.row_number}" for row in rows}
    for row in rows:
        if row.group_key is not None:
            grouped_rows[row.group_key].append(row)

    controls_by_group = {_control_group_key(control): control for control in metadata.controls}
    control_results: list[dict[str, object]] = []
    failed_count = 0
    not_verifiable_count = 0

    for control in metadata.controls:
        group_key = _control_group_key(control)
        group_hash = _group_key_hash(metadata.source_key, group_key)
        source_rows = grouped_rows.get(group_key, [])
        source = control.source
        overlapping_control = (
            source.source_hash == file_hash
            or (source.record_hash is not None and source.record_hash in row_hashes)
            or (source.source_id == metadata.source_key and source.record_locator in row_locators)
        )
        reason_codes: list[str] = []
        actual_amount: str | None = None
        report: dict[str, object] | None = None
        if overlapping_control:
            state = "not_verifiable"
            reason_codes.append("expected_source_overlaps_input_evidence")
        elif not source_rows:
            state = "not_verifiable"
            reason_codes.append("source_group_missing")
        elif any(row.row_status != "valid" or row.amount is None for row in source_rows):
            state = "not_verifiable"
            reason_codes.append("source_group_contains_invalid_or_duplicate_rows")
        else:
            try:
                actual_amount = _sum_exact_amounts(
                    [cast(Decimal, row.amount) for row in source_rows]
                )
                reconciliation = reconcile_values(
                    expected_value=control.expected_amount,
                    actual_value=actual_amount,
                    absolute_tolerance=control.absolute_tolerance,
                    relative_tolerance=control.relative_tolerance,
                    currency_code=control.currency,
                )
                state = reconciliation.state
                report = reconciliation.model_dump(mode="json")
            except ValueError:
                state = "not_verifiable"
                reason_codes.append("decimal_reconciliation_unavailable")

        if state == "failed":
            failed_count += 1
        elif state == "not_verifiable":
            not_verifiable_count += 1
        result: dict[str, object] = {
            "control_id": control.control_id,
            "comparison_method": FINANCE_SOURCE_RECONCILIATION_METHOD,
            "control_origin": "caller_supplied",
            "group_hash": group_hash,
            "period_start": control.period_start.isoformat(),
            "period_end": control.period_end.isoformat(),
            "currency": control.currency,
            "source_row_count": len(source_rows),
            "state": state,
            "reason_codes": reason_codes,
            "expected_amount": control.expected_amount,
            "actual_amount": actual_amount,
            "control_source_hash": source.source_hash,
        }
        if report is not None:
            result["variance"] = report["variance"]
            result["absolute_variance"] = report["absolute_variance"]
            result["effective_tolerance"] = report["effective_tolerance"]
            result["absolute_tolerance"] = report["absolute_tolerance"]
            result["relative_tolerance"] = report["relative_tolerance"]
        control_results.append(result)

    unmatched_source_groups = [key for key in grouped_rows if key not in controls_by_group]
    for group_key in unmatched_source_groups:
        source_rows = grouped_rows[group_key]
        control_results.append(
            {
                "control_id": None,
                "comparison_method": "not_performed_no_caller_control",
                "control_origin": "not_supplied",
                "group_hash": _group_key_hash(metadata.source_key, group_key),
                "period_start": group_key[1].isoformat(),
                "period_end": group_key[2].isoformat(),
                "currency": group_key[3],
                "source_row_count": len(source_rows),
                "state": "not_verifiable",
                "reason_codes": ["independent_control_missing"],
                "expected_amount": None,
                "actual_amount": None,
                "control_source_hash": None,
            }
        )
        not_verifiable_count += 1

    unmatched_control_count = sum(1 for key in controls_by_group if key not in grouped_rows)
    unassigned_invalid_row_count = sum(
        1 for row in rows if row.group_key is None and row.row_status != "valid"
    )
    if unassigned_invalid_row_count:
        not_verifiable_count += unassigned_invalid_row_count

    if (
        unmatched_source_groups
        or unmatched_control_count
        or unassigned_invalid_row_count
        or not_verifiable_count
    ):
        reconciliation_status = "not_verifiable"
    elif failed_count:
        reconciliation_status = "failed"
    else:
        reconciliation_status = "passed"

    control_results.sort(
        key=lambda item: (
            str(item["period_start"]),
            str(item["currency"]),
            str(item["control_id"] or ""),
            str(item["group_hash"]),
        )
    )
    return (
        control_results,
        reconciliation_status,
        len(unmatched_source_groups),
        unmatched_control_count,
        unassigned_invalid_row_count,
    )


def _input_control(
    control: FinanceReconciliationControl,
    tenant_id: UUID,
    company_id: UUID,
) -> dict[str, object]:
    payload = cast(dict[str, object], control.model_dump(mode="json"))
    source = cast(dict[str, object], payload["source"])
    source["tenant_id"] = str(tenant_id)
    source["company_id"] = str(company_id)
    return payload


def _reconciliation_semantics_match(result_snapshot: dict[str, object]) -> bool:
    method_present = "reconciliation_method" in result_snapshot
    governance_present = "governance_status" in result_snapshot
    if method_present != governance_present:
        return False
    groups = result_snapshot.get("reconciliation_groups")
    if not isinstance(groups, list):
        return False
    if not method_present:
        # Preserve only the complete legacy shape: old snapshots have neither the top-level
        # disclosures nor per-group method/origin fields.
        return all(
            isinstance(group, dict)
            and "comparison_method" not in group
            and "control_origin" not in group
            for group in groups
        )
    if (
        result_snapshot.get("reconciliation_method") != FINANCE_SOURCE_RECONCILIATION_METHOD
        or result_snapshot.get("governance_status") != FINANCE_SOURCE_GOVERNANCE_STATUS
    ):
        return False
    for group in groups:
        if not isinstance(group, dict):
            return False
        if group.get("control_id") is None:
            expected_method = "not_performed_no_caller_control"
            expected_origin = "not_supplied"
        else:
            expected_method = FINANCE_SOURCE_RECONCILIATION_METHOD
            expected_origin = "caller_supplied"
        if (
            group.get("comparison_method") != expected_method
            or group.get("control_origin") != expected_origin
        ):
            return False
    return True


def _verify_snapshot(row: FinanceSourceIntake) -> None:
    contract = row.input_snapshot.get("contract")
    mapping = row.input_snapshot.get("mapping")
    source = row.input_snapshot.get("source")
    file_evidence = row.input_snapshot.get("file")
    profile = row.input_snapshot.get("profile")
    counts = row.result_snapshot.get("counts")

    definition = contract.get("definition") if isinstance(contract, dict) else None
    contract_definition_matches = (
        isinstance(definition, dict) and content_hash(definition) == row.contract_hash
    )
    field_mapping = mapping.get("field_mapping") if isinstance(mapping, dict) else None
    ignored_columns = mapping.get("ignored_columns") if isinstance(mapping, dict) else None
    mapping_is_valid = (
        isinstance(field_mapping, dict)
        and set(field_mapping) == set(FINANCE_FIELDS)
        and all(
            isinstance(field, str) and isinstance(header, str)
            for field, header in field_mapping.items()
        )
        and isinstance(ignored_columns, list)
        and all(
            isinstance(item, dict)
            and set(item) == {"header", "rationale"}
            and isinstance(item.get("header"), str)
            and isinstance(item.get("rationale"), str)
            for item in ignored_columns
        )
    )
    mapping_hash_matches = (
        mapping_is_valid
        and isinstance(mapping, dict)
        and _mapping_hash(
            source_key=row.source_key,
            field_mapping=cast(dict[str, str], field_mapping),
            ignored_columns=cast(list[dict[str, str]], ignored_columns),
            contract_hash=row.contract_hash,
        )
        == row.mapping_hash
    )
    consistent = (
        row.input_snapshot.get("snapshot_schema_version") == FINANCE_SOURCE_SNAPSHOT_SCHEMA_VERSION
        and row.input_snapshot.get("engine_version") == FINANCE_SOURCE_ENGINE_VERSION
        and row.input_snapshot.get("intake_id") == str(row.id)
        and row.input_snapshot.get("tenant_id") == str(row.tenant_id)
        and row.input_snapshot.get("company_id") == str(row.company_id)
        and row.input_snapshot.get("requested_by_user_id") == str(row.requested_by_user_id)
        and row.input_snapshot.get("created_at") == _canonical_datetime(row.created_at)
        and row.result_snapshot.get("snapshot_schema_version")
        == FINANCE_SOURCE_SNAPSHOT_SCHEMA_VERSION
        and row.result_snapshot.get("engine_version") == FINANCE_SOURCE_ENGINE_VERSION
        and row.result_snapshot.get("intake_id") == str(row.id)
        and isinstance(contract, dict)
        and contract.get("contract_key") == row.contract_key
        and contract.get("version") == row.contract_version
        and contract.get("definition_hash") == row.contract_hash
        and contract_definition_matches
        and isinstance(mapping, dict)
        and mapping.get("mapping_version") == row.mapping_version
        and mapping.get("mapping_hash") == row.mapping_hash
        and mapping_hash_matches
        and isinstance(source, dict)
        and source.get("source_key") == row.source_key
        and source.get("source_as_of_at") == _canonical_datetime(row.source_as_of_at)
        and source.get("freshness_max_age_hours") == row.freshness_max_age_hours
        and isinstance(file_evidence, dict)
        and file_evidence.get("file_name") == row.file_name
        and file_evidence.get("format") == row.file_format
        and file_evidence.get("file_hash") == row.file_hash
        and file_evidence.get("file_size_bytes") == row.file_size_bytes
        and file_evidence.get("row_count") == row.total_rows
        and isinstance(profile, dict)
        and profile.get("total_rows") == row.total_rows
        and profile.get("valid_rows") == row.valid_rows
        and profile.get("invalid_rows") == row.invalid_rows
        and profile.get("duplicate_rows") == row.duplicate_rows
        and isinstance(counts, dict)
        and counts.get("total_rows") == row.total_rows
        and counts.get("valid_rows") == row.valid_rows
        and counts.get("invalid_rows") == row.invalid_rows
        and counts.get("duplicate_rows") == row.duplicate_rows
        and _reconciliation_semantics_match(row.result_snapshot)
        and row.result_snapshot.get("input_hash") == row.input_hash
        and row.result_snapshot.get("quality_status") == row.quality_status
        and row.result_snapshot.get("freshness_status") == row.freshness_status
        and row.result_snapshot.get("freshness_evaluated_at") == _canonical_datetime(row.created_at)
        and row.result_snapshot.get("reconciliation_status") == row.reconciliation_status
    )
    if (
        not consistent
        or content_hash(row.input_snapshot) != row.input_hash
        or content_hash(row.result_snapshot) != row.result_hash
    ):
        raise ApiError(
            500,
            "finance_source_evidence_integrity_error",
            "Saved finance source evidence could not be verified.",
        )


def _intake_response(
    row: FinanceSourceIntake,
    *,
    replayed: bool = False,
) -> FinanceSourceIntakeResponse:
    _verify_snapshot(row)
    return FinanceSourceIntakeResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        requested_by_user_id=row.requested_by_user_id,
        source_key=row.source_key,
        file_name=row.file_name,
        file_format=cast(Literal["csv"], row.file_format),
        file_hash=row.file_hash,
        file_size_bytes=row.file_size_bytes,
        contract_key=row.contract_key,
        contract_version=row.contract_version,
        contract_hash=row.contract_hash,
        mapping_version=row.mapping_version,
        mapping_hash=row.mapping_hash,
        source_as_of_at=row.source_as_of_at,
        freshness_max_age_hours=row.freshness_max_age_hours,
        freshness_status=cast(
            Literal["fresh", "stale", "policy_missing", "source_as_of_missing", "future_as_of"],
            row.freshness_status,
        ),
        quality_status=cast(Literal["passed", "warning", "failed"], row.quality_status),
        reconciliation_status=cast(
            Literal["passed", "failed", "not_verifiable"], row.reconciliation_status
        ),
        reconciliation_method=FINANCE_SOURCE_RECONCILIATION_METHOD,
        governance_status=FinanceSourceGovernanceStatus.model_validate(
            FINANCE_SOURCE_GOVERNANCE_STATUS
        ),
        total_rows=row.total_rows,
        valid_rows=row.valid_rows,
        invalid_rows=row.invalid_rows,
        duplicate_rows=row.duplicate_rows,
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        input_snapshot=row.input_snapshot,
        result_snapshot=row.result_snapshot,
        created_at=row.created_at,
        replayed=replayed,
    )


def _find_idempotent_intake(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    idempotency_key_hash: str,
) -> FinanceSourceIntake | None:
    return session.scalar(
        select(FinanceSourceIntake).where(
            FinanceSourceIntake.tenant_id == tenant_id,
            FinanceSourceIntake.company_id == company_id,
            FinanceSourceIntake.idempotency_key_hash == idempotency_key_hash,
        )
    )


def create_finance_source_intake(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    upload: BinaryIO,
    filename: str | None,
    idempotency_key: str,
    metadata: FinanceSourceIntakeMetadata,
    request_id: UUID,
    now: datetime,
    settings: Settings,
) -> FinanceSourceIntakeResponse:
    ensure_finance_source_intake_enabled(settings)
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        raise ApiError(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must be 16 to 128 ASCII letters, digits, or . _ ~ : - characters.",
        )
    safe_name = _safe_file_name(filename)
    try:
        content = read_limited_upload(upload, settings.import_max_file_bytes)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        parsed = parse_file(safe_name, content, settings.import_max_rows)
    except ImportFileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None
    if parsed.file_format != "csv" or len(parsed.worksheets) != 1:
        raise ApiError(
            415, "unsupported_file", "Finance source intake accepts one UTF-8 CSV sheet."
        )
    sheet = parsed.worksheets[0]
    if not sheet.rows:
        raise ApiError(422, "empty_dataset", "The CSV file contains no non-empty data rows.")

    contract = _published_contract(session)
    metadata_payload = metadata.model_dump(mode="json")
    request_fingerprint = content_hash(
        {
            "file_name": safe_name,
            "file_hash": file_hash,
            "file_size_bytes": len(content),
            "contract_hash": contract.definition_hash,
            "metadata": metadata_payload,
        }
    )
    idempotency_key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
    _lock_for_key(
        session,
        namespace="finance-source-idempotency",
        tenant_id=tenant_id,
        company_id=access.company.id,
        key=idempotency_key_hash,
    )
    prior = _find_idempotent_intake(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        idempotency_key_hash=idempotency_key_hash,
    )
    if prior is not None:
        if (
            prior.requested_by_user_id != user.id
            or prior.request_fingerprint != request_fingerprint
        ):
            raise ApiError(
                409,
                "idempotency_key_conflict",
                "This Idempotency-Key is already associated with a different finance intake.",
            )
        return _intake_response(prior, replayed=True)

    _validate_mapping(sheet, metadata)
    field_mapping, ignored_columns = _canonical_mapping(metadata)
    mapping = _get_or_create_mapping(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        user_id=user.id,
        source_key=metadata.source_key,
        field_mapping=field_mapping,
        ignored_columns=ignored_columns,
        contract_hash=contract.definition_hash,
    )

    for control in metadata.controls:
        if control.source.source_hash == file_hash:
            raise ApiError(
                422,
                "independent_control_required",
                "A control source must be distinct from the uploaded source file.",
            )

    intake_id = uuid4()
    validated_rows = _validate_rows(
        sheet,
        metadata,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake_id,
        identity_secret=settings.app_secret_key.get_secret_value().encode("utf-8"),
    )
    valid_rows = sum(row.row_status == "valid" for row in validated_rows)
    invalid_rows = sum(row.row_status == "invalid" for row in validated_rows)
    duplicate_rows = sum(
        any(issue["code"] == "duplicate_source_record_id" for issue in row.issues)
        for row in validated_rows
    )
    if invalid_rows or duplicate_rows:
        quality_status: Literal["passed", "warning", "failed"] = "failed"
    elif ignored_columns:
        quality_status = "warning"
    else:
        quality_status = "passed"

    freshness_status = evaluate_freshness(
        max_age_hours=metadata.freshness_max_age_hours,
        source_as_of_at=metadata.source_as_of_at,
        evaluated_at=now,
    )
    (
        reconciliation_results,
        reconciliation_status,
        unmatched_source_group_count,
        unmatched_control_count,
        unassigned_invalid_row_count,
    ) = _build_reconciliation_results(
        rows=validated_rows,
        metadata=metadata,
        file_hash=file_hash,
    )

    column_profiles = _profile_columns(sheet, metadata)
    quality_findings: list[dict[str, object]] = []
    if ignored_columns:
        quality_findings.append(
            {
                "code": "explicitly_ignored_columns",
                "severity": "warning",
                "count": len(ignored_columns),
            }
        )
    if invalid_rows:
        quality_findings.append(
            {"code": "invalid_source_rows", "severity": "error", "count": invalid_rows}
        )
    if duplicate_rows:
        quality_findings.append(
            {"code": "duplicate_source_record_ids", "severity": "error", "count": duplicate_rows}
        )

    input_snapshot: dict[str, object] = {
        "snapshot_schema_version": FINANCE_SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": FINANCE_SOURCE_ENGINE_VERSION,
        "intake_id": str(intake_id),
        "tenant_id": str(tenant_id),
        "company_id": str(access.company.id),
        "requested_by_user_id": str(user.id),
        "created_at": _canonical_datetime(now),
        "source": {
            "source_key": metadata.source_key,
            "source_identity_status": "caller_asserted_unregistered",
            "source_as_of_at": _canonical_datetime(metadata.source_as_of_at),
            "freshness_max_age_hours": metadata.freshness_max_age_hours,
            "freshness_policy_status": (
                "caller_supplied_unapproved"
                if metadata.freshness_max_age_hours is not None
                else "not_supplied"
            ),
        },
        "file": {
            "file_name": safe_name,
            "format": parsed.file_format,
            "file_hash": file_hash,
            "file_size_bytes": len(content),
            "worksheet_name": sheet.name,
            "row_count": len(sheet.rows),
            "blank_rows_ignored": sheet.blank_rows_ignored,
        },
        "contract": {
            "contract_key": contract.contract_key,
            "version": contract.version,
            "definition_hash": contract.definition_hash,
            "definition": contract.definition,
        },
        "mapping": {
            "mapping_version": mapping.mapping_version,
            "mapping_hash": mapping.mapping_hash,
            "field_mapping": field_mapping,
            "ignored_columns": ignored_columns,
        },
        "profile": {
            "column_profiles": column_profiles,
            "total_rows": len(sheet.rows),
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
            "duplicate_rows": duplicate_rows,
        },
        "controls": [
            _input_control(control, tenant_id, access.company.id) for control in metadata.controls
        ],
        "limitations": [
            (
                "The source key, source-as-of timestamp, and control source references are "
                "caller assertions."
            ),
            (
                "A distinct control file hash does not prove organizational independence or "
                "source authority."
            ),
            (
                "Only duplicate source_record_id values within this intake are detected; later "
                "extracts are not superseded or deduplicated."
            ),
            (
                "Source-reported periods are not assigned fiscal, effective-date, or recognition "
                "meaning."
            ),
            (
                "No uploaded file or raw source row values are retained; hashes do not "
                "authenticate the source."
            ),
            "The comparison is not evidence of accounting correctness or owner approval.",
        ],
    }
    input_hash = content_hash(input_snapshot)

    result_snapshot: dict[str, object] = {
        "snapshot_schema_version": FINANCE_SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": FINANCE_SOURCE_ENGINE_VERSION,
        "intake_id": str(intake_id),
        "input_hash": input_hash,
        "reconciliation_method": FINANCE_SOURCE_RECONCILIATION_METHOD,
        "governance_status": FINANCE_SOURCE_GOVERNANCE_STATUS,
        "quality_status": quality_status,
        "freshness_status": freshness_status,
        "freshness_evaluated_at": _canonical_datetime(now),
        "reconciliation_status": reconciliation_status,
        "counts": {
            "total_rows": len(sheet.rows),
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
            "duplicate_rows": duplicate_rows,
            "unmatched_source_groups": unmatched_source_group_count,
            "unmatched_controls": unmatched_control_count,
            "unassigned_invalid_rows": unassigned_invalid_row_count,
        },
        "quality_findings": quality_findings,
        "column_profiles": column_profiles,
        "reconciliation_groups": reconciliation_results,
        "limitations": [
            (
                "Passed and failed are arithmetic outcomes of exact Decimal comparisons against "
                "caller-supplied control amounts and tolerances only."
            ),
            (
                "The finance-source and control-source identities are caller assertions, not "
                "independently authenticated."
            ),
            (
                "Control independence is not independently verified; no finance-owner approval "
                "is established."
            ),
            (
                "Accounting correctness and production readiness are not established; "
                "production/customer data is not authorized."
            ),
            (
                "Correction and retention policies are not established; append-only evidence "
                "has no approved deletion schedule."
            ),
            (
                "No currency conversion, rounding, account classification, causal explanation, "
                "or financial conclusion is produced."
            ),
            (
                "A missing, invalid, duplicate, unmatched, stale, or unsupported input is not "
                "replaced with zero."
            ),
            (
                "The result does not establish accounting correctness, independent-control "
                "governance, or source authority."
            ),
        ],
    }
    result_hash = content_hash(result_snapshot)

    intake = FinanceSourceIntake(
        id=intake_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        source_key=metadata.source_key,
        file_name=safe_name,
        file_format="csv",
        file_hash=file_hash,
        file_size_bytes=len(content),
        contract_key=contract.contract_key,
        contract_version=contract.version,
        contract_hash=contract.definition_hash,
        mapping_version=mapping.mapping_version,
        mapping_hash=mapping.mapping_hash,
        idempotency_key_hash=idempotency_key_hash,
        request_fingerprint=request_fingerprint,
        source_as_of_at=metadata.source_as_of_at,
        freshness_max_age_hours=metadata.freshness_max_age_hours,
        freshness_status=freshness_status,
        quality_status=quality_status,
        reconciliation_status=reconciliation_status,
        total_rows=len(sheet.rows),
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        duplicate_rows=duplicate_rows,
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=now,
    )
    session.add(intake)
    session.flush()

    session.add_all(
        FinanceSourceRowEvidence(
            id=uuid4(),
            tenant_id=tenant_id,
            company_id=access.company.id,
            intake_id=intake_id,
            source_row_number=row.row_number,
            source_row_hash=row.source_row_hash,
            source_record_identity_hash=row.source_record_identity_hash,
            reconciliation_group_hash=row.reconciliation_group_hash,
            row_status=row.row_status,
            issues=row.issues,
        )
        for row in validated_rows
    )
    record_event(
        session,
        "finance_source.intake_recorded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="finance_source_intake",
        target_id=intake.id,
        request_id=request_id,
        details={
            "source_key": metadata.source_key,
            "file_hash": file_hash,
            "contract_hash": contract.definition_hash,
            "mapping_version": mapping.mapping_version,
            "mapping_hash": mapping.mapping_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "total_rows": len(sheet.rows),
            "quality_status": quality_status,
            "freshness_status": freshness_status,
            "reconciliation_status": reconciliation_status,
        },
    )
    session.flush()
    return _intake_response(intake)


def _get_intake(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
) -> FinanceSourceIntake:
    row = session.scalar(
        select(FinanceSourceIntake).where(
            FinanceSourceIntake.id == intake_id,
            FinanceSourceIntake.tenant_id == tenant_id,
            FinanceSourceIntake.company_id == company_id,
        )
    )
    if row is None:
        raise ApiError(
            404, "finance_source_intake_not_found", "Finance source intake was not found."
        )
    return row


def get_finance_source_intake(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    *,
    settings: Settings,
) -> FinanceSourceIntakeResponse:
    ensure_finance_source_intake_enabled(settings)
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    row = _get_intake(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake_id,
    )
    return _intake_response(row)


def list_finance_source_intakes(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> FinanceSourceIntakePage:
    ensure_finance_source_intake_enabled(settings)
    if not 1 <= limit <= MAX_PAGE_SIZE or not 0 <= offset <= MAX_PAGE_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    filters = (
        FinanceSourceIntake.tenant_id == tenant_id,
        FinanceSourceIntake.company_id == access.company.id,
    )
    total = (
        session.scalar(select(func.count()).select_from(FinanceSourceIntake).where(*filters)) or 0
    )
    rows = session.scalars(
        select(FinanceSourceIntake)
        .where(*filters)
        .order_by(FinanceSourceIntake.created_at.desc(), FinanceSourceIntake.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return FinanceSourceIntakePage(
        items=[_intake_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def list_finance_source_row_evidence(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    *,
    limit: int,
    offset: int,
    settings: Settings,
) -> FinanceSourceRowEvidencePage:
    ensure_finance_source_intake_enabled(settings)
    if not 1 <= limit <= MAX_PAGE_SIZE or not 0 <= offset <= MAX_PAGE_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    _get_intake(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake_id,
    )
    filters = (
        FinanceSourceRowEvidence.tenant_id == tenant_id,
        FinanceSourceRowEvidence.company_id == access.company.id,
        FinanceSourceRowEvidence.intake_id == intake_id,
    )
    total = (
        session.scalar(select(func.count()).select_from(FinanceSourceRowEvidence).where(*filters))
        or 0
    )
    rows = session.scalars(
        select(FinanceSourceRowEvidence)
        .where(*filters)
        .order_by(FinanceSourceRowEvidence.source_row_number)
        .limit(limit)
        .offset(offset)
    ).all()
    return FinanceSourceRowEvidencePage(
        items=[
            FinanceSourceRowEvidenceResponse(
                source_row_number=row.source_row_number,
                source_row_hash=row.source_row_hash,
                source_record_identity_hash=row.source_record_identity_hash,
                reconciliation_group_hash=row.reconciliation_group_hash,
                row_status=cast(Literal["valid", "duplicate", "invalid"], row.row_status),
                issues=row.issues,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
