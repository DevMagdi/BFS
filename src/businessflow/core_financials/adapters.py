from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import cast

from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    parse_decimal_text,
    parse_output_decimal_text,
    validate_currency_code,
)
from businessflow.core_financials.contracts import (
    BANK_FIELDS,
    LEDGER_FIELDS,
    CoreFinancialPolicyCreateRequest,
    FinancialCategory,
    ImportRowStatus,
    SourceType,
    TransactionKind,
    parse_iso_date,
)
from businessflow.data_imports.parsers import ParsedSheet
from businessflow.metrics.snapshot import content_hash


@dataclass(frozen=True, slots=True)
class PriorCanonicalRecord:
    source_record_id: str
    category: str | None
    currency_code: str
    signed_amount: str
    account_code: str | None
    event_type: str
    reverses_record_id: str | None
    policy_id: str


@dataclass(frozen=True, slots=True)
class NormalizedSourceRow:
    row_number: int
    source_record_id: str | None
    transaction_date: date | None
    currency_code: str | None
    account_code: str | None
    category: FinancialCategory | None
    event_type: TransactionKind | None
    signed_amount: str | None
    reverses_record_id: str | None
    source_row_hash: str
    mapped_source_values: dict[str, str | None]
    row_status: ImportRowStatus
    issues: tuple[str, ...]


def _exact_difference(left: Decimal, right: Decimal) -> Decimal:
    """Subtract signed source amounts without inheriting the process Decimal context."""
    return exact_decimal_sum((left, right.copy_negate()))


def validate_source_headers(
    sheet: ParsedSheet,
    *,
    field_mapping: Mapping[str, str],
    ignored_columns: tuple[dict[str, str], ...],
    source_type: SourceType,
) -> None:
    required_fields = set(LEDGER_FIELDS if source_type == "general_ledger" else BANK_FIELDS)
    if set(field_mapping) != required_fields:
        raise ValueError("source mapping does not match the selected source adapter")
    mapped_headers = set(field_mapping.values())
    ignored_headers = {item["header"] for item in ignored_columns}
    if mapped_headers.intersection(ignored_headers):
        raise ValueError("a source header cannot be both mapped and ignored")
    if len(ignored_headers) != len(ignored_columns):
        raise ValueError("ignored source headers must be unique")
    if set(sheet.headers) != mapped_headers | ignored_headers:
        raise ValueError("every source column must be mapped or explicitly ignored")


def normalize_source_rows(
    sheet: ParsedSheet,
    *,
    source_type: SourceType,
    source_key: str,
    file_hash: str,
    mapping_hash: str,
    policy_hash: str,
    field_mapping: Mapping[str, str],
    policy: CoreFinancialPolicyCreateRequest,
    policy_id: str,
    source_period_start: date,
    source_period_end: date,
    existing_record_ids: set[str] | None = None,
    prior_records: Mapping[str, PriorCanonicalRecord] | None = None,
    prior_reversed_ids: set[str] | None = None,
) -> list[NormalizedSourceRow]:
    """Adapt either journal lines or bank movements to the same signed canonical row shape."""
    if not sheet.rows:
        raise ValueError("a financial source must contain at least one data row")
    required_fields = set(LEDGER_FIELDS if source_type == "general_ledger" else BANK_FIELDS)
    if set(field_mapping) != required_fields:
        raise ValueError("source mapping does not match the selected source adapter")
    header_positions = {header: index for index, header in enumerate(sheet.headers)}
    if any(header not in header_positions for header in field_mapping.values()):
        raise ValueError("mapped source header is missing")

    mapped_values: list[dict[str, str | None]] = []
    record_ids: list[str | None] = []
    for parsed_row in sheet.rows:
        values = {
            field: parsed_row.values[header_positions[header]]
            for field, header in field_mapping.items()
        }
        mapped_values.append(values)
        raw_id = values.get("source_record_id")
        record_id = raw_id if raw_id and raw_id == raw_id.strip() and len(raw_id) <= 256 else None
        record_ids.append(record_id)

    id_counts = Counter(item for item in record_ids if item is not None)
    existing_ids = existing_record_ids or set()
    prior = prior_records or {}
    reversed_ids = prior_reversed_ids or set()
    row_records: list[NormalizedSourceRow] = []

    for parsed_row, values, record_id in zip(sheet.rows, mapped_values, record_ids, strict=True):
        issues: list[str] = []
        if record_id is None:
            issues.append("source_record_id_invalid")
        elif id_counts[record_id] > 1 or record_id in existing_ids:
            issues.append("duplicate_source_record_id")

        date_value = values.get("transaction_date")
        transaction_date = parse_iso_date(date_value)
        if transaction_date is None:
            issues.append("transaction_date_invalid")
        elif not source_period_start <= transaction_date <= source_period_end:
            issues.append("transaction_date_outside_declared_source_period")

        currency_raw = values.get("currency")
        currency_code: str | None = None
        try:
            if currency_raw is None:
                raise ValueError
            currency_code = validate_currency_code(currency_raw)
        except ValueError:
            issues.append("currency_invalid")

        kind_raw = values.get("event_type")
        event_type: TransactionKind | None = None
        allowed_event_types = {"normal", "refund", "reversal", "adjustment"}
        if source_type == "bank_activity":
            allowed_event_types.add("transfer")
        if (
            kind_raw is not None
            and kind_raw == kind_raw.strip()
            and kind_raw in allowed_event_types
        ):
            event_type = cast(TransactionKind, kind_raw)
        else:
            issues.append("event_type_invalid")

        reversal_raw = values.get("reversal_of_record_id")
        reverses_record_id: str | None = None
        if reversal_raw is not None:
            if reversal_raw and reversal_raw == reversal_raw.strip() and len(reversal_raw) <= 256:
                reverses_record_id = reversal_raw
            else:
                issues.append("reversal_reference_invalid")
        if event_type == "reversal" and reverses_record_id is None:
            issues.append("reversal_reference_required")
        elif event_type != "reversal" and reversal_raw is not None:
            issues.append("reversal_reference_not_allowed")

        account_code: str | None = None
        category: FinancialCategory | None = None
        signed_amount: Decimal | None = None
        if source_type == "general_ledger":
            raw_account = values.get("account_code")
            if (
                raw_account is None
                or not raw_account.strip()
                or raw_account != raw_account.strip()
                or len(raw_account) > 128
            ):
                issues.append("account_code_invalid")
            else:
                account_code = raw_account
                account_rule = policy.account_map.get(raw_account)
                if account_rule is None:
                    issues.append("account_code_unmapped")
                else:
                    category = account_rule.category

            debit = _parse_nonnegative_amount(values.get("debit"), "debit", issues)
            credit = _parse_nonnegative_amount(values.get("credit"), "credit", issues)
            if debit is not None and credit is not None:
                if (debit > 0) == (credit > 0):
                    issues.append("journal_line_must_have_exactly_one_positive_side")
                elif account_code is not None and category is not None:
                    account_rule = policy.account_map[account_code]
                    signed_amount = (
                        _exact_difference(debit, credit)
                        if account_rule.normal_balance == "debit"
                        else _exact_difference(credit, debit)
                    )
        else:
            raw_account = values.get("bank_account_id")
            if (
                raw_account is None
                or not raw_account.strip()
                or raw_account != raw_account.strip()
                or len(raw_account) > 128
            ):
                issues.append("bank_account_id_invalid")
            else:
                account_code = raw_account

            cash_in = _parse_nonnegative_amount(values.get("cash_in"), "cash_in", issues)
            cash_out = _parse_nonnegative_amount(values.get("cash_out"), "cash_out", issues)
            if cash_in is not None and cash_out is not None:
                if (cash_in > 0) == (cash_out > 0):
                    issues.append("bank_row_must_have_exactly_one_positive_side")
                else:
                    signed_amount = _exact_difference(cash_in, cash_out)

        status: ImportRowStatus
        if "duplicate_source_record_id" in issues:
            status = "duplicate"
        elif issues:
            status = "rejected"
        else:
            status = "accepted"

        row_hash = content_hash(
            {
                "schema_version": 1,
                "source_key": source_key,
                "source_type": source_type,
                "file_hash": file_hash,
                "mapping_hash": mapping_hash,
                "policy_hash": policy_hash,
                "source_period_start": source_period_start.isoformat(),
                "source_period_end": source_period_end.isoformat(),
                "source_row_number": parsed_row.row_number,
                "mapped_source_values": values,
            }
        )
        row_records.append(
            NormalizedSourceRow(
                row_number=parsed_row.row_number,
                source_record_id=record_id,
                transaction_date=transaction_date,
                currency_code=currency_code,
                account_code=account_code,
                category=category,
                event_type=event_type,
                signed_amount=decimal_text(signed_amount) if signed_amount is not None else None,
                reverses_record_id=reverses_record_id,
                source_row_hash=row_hash,
                mapped_source_values=values,
                row_status=status,
                issues=tuple(issues),
            )
        )

    by_id = {
        row.source_record_id: row
        for row in row_records
        if row.source_record_id is not None and row.row_status == "accepted"
    }
    prior_reversed = set(reversed_ids)
    used_in_batch: set[str] = set()
    validated: list[NormalizedSourceRow] = []
    for row in row_records:
        if row.event_type != "reversal" or row.row_status != "accepted":
            validated.append(row)
            continue
        assert row.source_record_id is not None
        assert row.reverses_record_id is not None
        original = by_id.get(row.reverses_record_id)
        if original is None:
            original_data = prior.get(row.reverses_record_id)
            if original_data is not None and original_data.policy_id == policy_id:
                original = _prior_row_as_normalized(original_data)
        reason: str | None = None
        if original is None:
            reason = "reversal_original_missing_or_policy_version_mismatch"
        elif row.reverses_record_id in prior_reversed or row.reverses_record_id in used_in_batch:
            reason = "reversal_already_recorded"
        elif (
            original.row_status != "accepted"
            or original.event_type == "reversal"
            or original.account_code != row.account_code
            or original.currency_code != row.currency_code
            or original.signed_amount is None
            or row.signed_amount is None
            or exact_decimal_sum(
                (
                    parse_output_decimal_text(original.signed_amount),
                    parse_output_decimal_text(row.signed_amount),
                )
            )
            != 0
        ):
            reason = "reversal_must_be_linked_exact_opposite_entry"
        if reason is not None:
            validated.append(replace(row, row_status="rejected", issues=(*row.issues, reason)))
        else:
            used_in_batch.add(row.reverses_record_id)
            validated.append(row)
    return validated


def _parse_nonnegative_amount(
    raw_value: str | None,
    field: str,
    issues: list[str],
) -> Decimal | None:
    if raw_value is None:
        issues.append(f"{field}_missing")
        return None
    try:
        amount = parse_decimal_text(raw_value)
    except ValueError:
        issues.append(f"{field}_invalid")
        return None
    if amount < 0:
        issues.append(f"{field}_must_be_nonnegative")
        return None
    return amount


def _prior_row_as_normalized(record: PriorCanonicalRecord) -> NormalizedSourceRow:
    return NormalizedSourceRow(
        row_number=0,
        source_record_id=record.source_record_id,
        transaction_date=None,
        currency_code=record.currency_code,
        account_code=record.account_code,
        category=cast(FinancialCategory | None, record.category),
        event_type=cast(TransactionKind, record.event_type),
        signed_amount=record.signed_amount,
        reverses_record_id=record.reverses_record_id,
        source_row_hash="0" * 64,
        mapped_source_values={},
        row_status="accepted",
        issues=(),
    )
