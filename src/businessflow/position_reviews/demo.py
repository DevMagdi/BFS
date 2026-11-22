from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

from businessflow.metrics.snapshot import content_hash

DEMO_SEED_VERSION = "businessflow.m10-demo.v1"
DEMO_SYNTHETIC_LABEL = "SYNTHETIC DEMO DATA — NOT CUSTOMER DATA"
DEMO_TENANT_NAME = "M10 Synthetic Demo"
DEMO_COMPANY_NAME = "Synthetic Demo Company"
DEMO_EMPLOYEES = (
    {
        "employee_code": "SYN-EMP-001",
        "full_name": "Demo Colleague Alpha",
        "job_title": "Consultant",
        "standard_weekly_capacity_hours": "40",
        "employment_status": "active",
    },
    {
        "employee_code": "SYN-EMP-002",
        "full_name": "Demo Colleague Beta",
        "job_title": "Analyst",
        "standard_weekly_capacity_hours": "40",
        "employment_status": "active",
    },
)
DEMO_PROJECTS = (
    {"project_code": "SYN-PRJ-001", "name": "Demo Delivery Project", "status": "planned"},
)
OPERATIONAL_DATASETS = ("employees", "projects", "weekly_capacity", "resource_assignments")
TEMPLATE_DATASETS = (*OPERATIONAL_DATASETS, "financial_export")

EMPLOYEE_HEADERS = tuple(DEMO_EMPLOYEES[0])
PROJECT_HEADERS = tuple(DEMO_PROJECTS[0])
CAPACITY_HEADERS = (
    "employee_code",
    "week_start_date",
    "availability_status",
    "scheduled_hours",
    "non_project_hours",
    "unavailable_hours",
    "source",
)
ASSIGNMENT_HEADERS = (
    "project_code",
    "employee_code",
    "start_date",
    "end_date",
    "planned_hours_per_week",
    "status",
    "source",
)
FINANCIAL_HEADERS = (
    "source_report",
    "exported_at",
    "source_label",
    "source_value_text",
    "currency_code",
    "period_start",
    "period_end",
    "source_record_id",
)


def next_monday(today: date | None = None) -> date:
    current = today or datetime.now(UTC).date()
    return current + timedelta(days=(-current.weekday()) % 7)


def _csv_bytes(headers: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _week_dates(start_week: date) -> tuple[date, ...]:
    return tuple(start_week + timedelta(days=7 * index) for index in range(4))


def _capacity_rows(start_week: date) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for week in _week_dates(start_week):
        rows.extend(
            [
                {
                    "employee_code": "SYN-EMP-001",
                    "week_start_date": week.isoformat(),
                    "availability_status": "confirmed",
                    "scheduled_hours": "36",
                    "non_project_hours": "8",
                    "unavailable_hours": "0",
                    "source": "manual",
                },
                {
                    "employee_code": "SYN-EMP-002",
                    "week_start_date": week.isoformat(),
                    "availability_status": "confirmed",
                    "scheduled_hours": "40",
                    "non_project_hours": "4",
                    "unavailable_hours": "0",
                    "source": "manual",
                },
            ]
        )
    return rows


def _assignment_rows(start_week: date, *, prior: bool) -> list[dict[str, str]]:
    period_end = start_week + timedelta(days=27)
    hours = ("20", "30") if prior else ("34", "30")
    return [
        {
            "project_code": "SYN-PRJ-001",
            "employee_code": employee["employee_code"],
            "start_date": start_week.isoformat(),
            "end_date": period_end.isoformat(),
            "planned_hours_per_week": weekly_hours,
            "status": "committed",
            "source": "manual",
        }
        for employee, weekly_hours in zip(DEMO_EMPLOYEES, hours, strict=True)
    ]


def prior_pack_csvs(current_start_week: date) -> dict[str, bytes]:
    """Synthetic M4 files used only after the explicit, empty-company demo-seed action."""
    baseline_start = current_start_week - timedelta(days=28)
    return {
        "employees": _csv_bytes(EMPLOYEE_HEADERS, [dict(row) for row in DEMO_EMPLOYEES]),
        "projects": _csv_bytes(PROJECT_HEADERS, [dict(row) for row in DEMO_PROJECTS]),
        "weekly_capacity": _csv_bytes(CAPACITY_HEADERS, _capacity_rows(baseline_start)),
        "resource_assignments": _csv_bytes(
            ASSIGNMENT_HEADERS, _assignment_rows(baseline_start, prior=True)
        ),
    }


def current_template_csv(dataset_type: str, current_start_week: date) -> bytes:
    if dataset_type == "employees":
        return _csv_bytes(EMPLOYEE_HEADERS, [dict(row) for row in DEMO_EMPLOYEES])
    if dataset_type == "projects":
        return _csv_bytes(PROJECT_HEADERS, [dict(row) for row in DEMO_PROJECTS])
    if dataset_type == "weekly_capacity":
        return _csv_bytes(CAPACITY_HEADERS, _capacity_rows(current_start_week))
    if dataset_type == "resource_assignments":
        return _csv_bytes(
            ASSIGNMENT_HEADERS,
            _assignment_rows(current_start_week, prior=False),
        )
    if dataset_type == "financial_export":
        baseline_start = current_start_week - timedelta(days=28)
        baseline_end = current_start_week - timedelta(days=1)
        current_end = current_start_week + timedelta(days=27)
        exported_at = f"{(current_start_week - timedelta(days=2)).isoformat()}T12:00:00Z"
        common = {
            "source_report": "Synthetic management report",
            "exported_at": exported_at,
            "currency_code": "USD",
        }
        rows = [
            {
                **common,
                "source_label": "Cash at bank",
                "source_value_text": "125000.00",
                "period_start": baseline_start.isoformat(),
                "period_end": baseline_end.isoformat(),
                "source_record_id": "SYN-CASH-PRIOR",
            },
            {
                **common,
                "source_label": "Cash at bank",
                "source_value_text": "118000.00",
                "period_start": current_start_week.isoformat(),
                "period_end": current_end.isoformat(),
                "source_record_id": "SYN-CASH-CURRENT",
            },
            {
                **common,
                "source_label": "Accounts receivable",
                "source_value_text": "42250.00",
                "period_start": baseline_start.isoformat(),
                "period_end": baseline_end.isoformat(),
                "source_record_id": "SYN-AR-PRIOR",
            },
            {
                **common,
                "source_label": "Accounts receivable",
                "source_value_text": "44750.00",
                "period_start": current_start_week.isoformat(),
                "period_end": current_end.isoformat(),
                "source_record_id": "SYN-AR-CURRENT",
            },
        ]
        return _csv_bytes(FINANCIAL_HEADERS, rows)
    raise ValueError("unsupported synthetic demo template")


def current_template_hashes(current_start_week: date) -> dict[str, str]:
    return {
        dataset: sha256(current_template_csv(dataset, current_start_week)).hexdigest()
        for dataset in TEMPLATE_DATASETS
    }


def demo_manifest_hash(current_start_week: date) -> str:
    baseline_start = current_start_week - timedelta(days=28)
    files = prior_pack_csvs(current_start_week)
    return content_hash(
        {
            "seed_version": DEMO_SEED_VERSION,
            "synthetic_label": DEMO_SYNTHETIC_LABEL,
            "baseline_start_week_date": baseline_start.isoformat(),
            "baseline_end_week_date": (current_start_week - timedelta(days=1)).isoformat(),
            "current_start_week_date": current_start_week.isoformat(),
            "prior_pack_file_hashes": {
                name: sha256(content).hexdigest() for name, content in sorted(files.items())
            },
            "current_template_hashes": current_template_hashes(current_start_week),
        }
    )
