from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

QualityStatus = Literal["passed", "warning", "failed"]
IngestionStatus = Literal["succeeded", "succeeded_with_warnings", "blocked"]
FreshnessStatus = Literal[
    "fresh", "stale", "policy_missing", "source_as_of_missing", "future_as_of"
]


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    status: QualityStatus
    ingestion_status: IngestionStatus
    findings: tuple[dict[str, object], ...]


def evaluate_freshness(
    *,
    max_age_hours: int | None,
    source_as_of_at: datetime | None,
    evaluated_at: datetime,
) -> FreshnessStatus:
    """Apply only the freshness limit explicitly configured for this source."""
    evaluated = _as_utc(evaluated_at)
    if max_age_hours is None:
        return "policy_missing"
    if source_as_of_at is None:
        return "source_as_of_missing"
    as_of = _as_utc(source_as_of_at)
    if as_of > evaluated:
        return "future_as_of"
    if as_of + timedelta(hours=max_age_hours) < evaluated:
        return "stale"
    return "fresh"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def assess_quality(
    *,
    import_status: str,
    invalid_rows: int,
    warning_rows: int,
    duplicate_rows: int,
    skipped_rows: int,
    mapping_version: int,
    validation_hash: str | None,
    lineage_count: int,
    mapping_findings: list[dict[str, object]],
    issue_counts: Counter[tuple[str, str]],
) -> QualityAssessment:
    findings: list[dict[str, object]] = list(mapping_findings)
    findings.extend(
        {
            "code": "m4_row_issue",
            "issue_code": issue_code,
            "severity": severity,
            "count": count,
            "message": (
                "M4 recorded source-row validation findings; inspect the M4 report and row lineage."
            ),
        }
        for (severity, issue_code), count in sorted(issue_counts.items())
        if count > 0 and severity in {"error", "warning"}
    )

    if invalid_rows:
        findings.append(
            {
                "code": "m4_invalid_rows",
                "severity": "error",
                "count": invalid_rows,
                "message": "The M4 import contains invalid rows.",
            }
        )
    if not validation_hash or mapping_version < 1:
        findings.append(
            {
                "code": "m4_validation_evidence_missing",
                "severity": "error",
                "message": "The M4 import lacks a versioned mapping or validation hash.",
            }
        )
    if lineage_count == 0:
        findings.append(
            {
                "code": "m4_lineage_missing",
                "severity": "error",
                "message": "The applied M4 import has no canonical source-row links.",
            }
        )

    warning_issue_count = sum(
        count for (severity, _), count in issue_counts.items() if severity == "warning"
    )
    if (
        warning_rows or duplicate_rows or skipped_rows or import_status == "applied_with_warnings"
    ) and (warning_issue_count == 0):
        findings.append(
            {
                "code": "m4_warning_unclassified",
                "severity": "warning",
                "count": warning_rows + duplicate_rows + skipped_rows,
                "message": (
                    "M4 reported warning, duplicate, or skipped rows without a classified "
                    "warning code."
                ),
            }
        )
    elif warning_rows or duplicate_rows or skipped_rows or import_status == "applied_with_warnings":
        findings.append(
            {
                "code": "m4_warning_rows",
                "severity": "info",
                "warning_rows": warning_rows,
                "duplicate_rows": duplicate_rows,
                "skipped_rows": skipped_rows,
                "message": "M4 applied the import with warnings, duplicates, or skipped rows.",
            }
        )

    if import_status not in {"applied", "applied_with_warnings"}:
        findings.append(
            {
                "code": "m4_import_not_applied",
                "severity": "error",
                "message": "The M4 source is not in an applied state.",
            }
        )

    has_error = any(item.get("severity") == "error" for item in findings)
    has_warning = any(item.get("severity") == "warning" for item in findings)
    if has_error:
        status: QualityStatus = "failed"
        ingestion_status: IngestionStatus = "blocked"
    elif has_warning:
        status = "warning"
        ingestion_status = "succeeded_with_warnings"
    else:
        status = "passed"
        ingestion_status = "succeeded"
    return QualityAssessment(status, ingestion_status, tuple(findings))


def m10_warning_codes_are_compatible(
    dataset_type: str, findings: tuple[dict[str, object], ...] | list[dict[str, object]]
) -> bool:
    """Allow only the duplicate notices that M10 independently permits for roster imports."""
    if dataset_type not in {"employees", "projects"}:
        return False
    warning_findings = [item for item in findings if item.get("severity") == "warning"]
    return bool(warning_findings) and all(
        item.get("issue_code") in {"duplicate_existing", "duplicate_in_file"}
        for item in warning_findings
    )
