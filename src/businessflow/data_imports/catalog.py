from __future__ import annotations

import re
from dataclasses import dataclass

from businessflow.data_imports.schemas import ImportDataset, ImportFieldDefinition


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    aliases: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    fields: tuple[FieldSpec, ...]
    required: frozenset[str]
    required_one_of: tuple[frozenset[str], ...] = ()
    mutually_exclusive: tuple[frozenset[str], ...] = ()
    defaults: tuple[str, ...] = ()

    @property
    def by_name(self) -> dict[str, FieldSpec]:
        return {field.name: field for field in self.fields}


DATASETS: dict[ImportDataset, DatasetSpec] = {
    "employees": DatasetSpec(
        fields=(
            FieldSpec(
                "employee_code",
                ("employee code", "staff code", "code"),
                "Unique company employee code.",
            ),
            FieldSpec(
                "full_name",
                ("full name", "employee name", "staff name", "name"),
                "Employee's full name.",
            ),
            FieldSpec("job_title", ("job title", "title", "position"), "Employee's job title."),
            FieldSpec(
                "standard_weekly_capacity_hours",
                (
                    "standard weekly capacity hours",
                    "weekly capacity hours",
                    "standard capacity hours",
                ),
                "Standard weekly capacity in hours; a value is required.",
            ),
            FieldSpec(
                "department_id",
                ("department id", "department uuid", "dept id"),
                "Existing active department UUID.",
            ),
            FieldSpec(
                "department_name",
                ("department", "department name", "dept name"),
                "Name of an existing active department.",
            ),
            FieldSpec(
                "employment_status",
                ("employment status", "employee status"),
                "One of active, on_leave, inactive, or terminated.",
            ),
            FieldSpec(
                "employment_start_date",
                ("employment start date", "start date", "hire date"),
                "Employment start date in ISO format (YYYY-MM-DD).",
            ),
            FieldSpec(
                "employment_end_date",
                ("employment end date", "end date", "termination date"),
                "Employment end date in ISO format (YYYY-MM-DD).",
            ),
        ),
        required=frozenset(
            {"employee_code", "full_name", "job_title", "standard_weekly_capacity_hours"}
        ),
        mutually_exclusive=(frozenset({"department_id", "department_name"}),),
        defaults=("employment_status",),
    ),
    "skills": DatasetSpec(
        fields=(
            FieldSpec("name", ("skill", "skill name", "skill title"), "Unique tenant skill name."),
            FieldSpec(
                "description",
                ("skill description", "description", "details"),
                "Optional description.",
            ),
        ),
        required=frozenset({"name"}),
    ),
    "employee_skills": DatasetSpec(
        fields=(
            FieldSpec("employee_code", ("employee code", "staff code"), "Existing employee code."),
            FieldSpec(
                "employee_id",
                ("employee id", "employee uuid", "staff id"),
                "Existing employee UUID.",
            ),
            FieldSpec("skill_name", ("skill", "skill name"), "Existing active skill name."),
            FieldSpec("skill_id", ("skill id", "skill uuid"), "Existing active skill UUID."),
            FieldSpec(
                "proficiency",
                ("proficiency", "proficiency level", "skill level"),
                "Proficiency level from 1 to 5.",
            ),
        ),
        required=frozenset({"proficiency"}),
        required_one_of=(
            frozenset({"employee_code", "employee_id"}),
            frozenset({"skill_name", "skill_id"}),
        ),
        mutually_exclusive=(
            frozenset({"employee_code", "employee_id"}),
            frozenset({"skill_name", "skill_id"}),
        ),
    ),
    "weekly_capacity": DatasetSpec(
        fields=(
            FieldSpec("employee_code", ("employee code", "staff code"), "Existing employee code."),
            FieldSpec(
                "employee_id",
                ("employee id", "employee uuid", "staff id"),
                "Existing employee UUID.",
            ),
            FieldSpec(
                "week_start_date",
                ("week start", "week start date", "week of"),
                "Monday date identifying the capacity week.",
            ),
            FieldSpec(
                "availability_status",
                ("availability", "availability status", "capacity status"),
                "One of confirmed, estimated, or unknown.",
            ),
            FieldSpec(
                "scheduled_hours",
                ("scheduled hours", "weekly scheduled hours"),
                "Required for confirmed or estimated availability; "
                "do not substitute zero for missing data.",
            ),
            FieldSpec(
                "non_project_hours",
                ("non project hours", "non-project hours", "internal hours"),
                "Required for confirmed or estimated availability.",
            ),
            FieldSpec(
                "unavailable_hours",
                ("unavailable hours", "leave hours"),
                "Required for confirmed or estimated availability.",
            ),
            FieldSpec(
                "source",
                ("source", "capacity source"),
                "Optional source: manual, calendar, or manager_adjustment.",
            ),
            FieldSpec(
                "reason",
                ("reason", "adjustment reason"),
                "Reason required for manager_adjustment source.",
            ),
        ),
        required=frozenset({"week_start_date", "availability_status"}),
        required_one_of=(frozenset({"employee_code", "employee_id"}),),
        mutually_exclusive=(frozenset({"employee_code", "employee_id"}),),
        defaults=("source",),
    ),
    "projects": DatasetSpec(
        fields=(
            FieldSpec(
                "project_code",
                ("project code", "project key", "code"),
                "Unique company project code.",
            ),
            FieldSpec("name", ("project name", "name", "title"), "Project name."),
            FieldSpec(
                "description",
                ("description", "project description", "details"),
                "Optional description.",
            ),
            FieldSpec(
                "status",
                ("status", "project status"),
                "New projects may be draft or planned; omitted status defaults to draft.",
            ),
            FieldSpec(
                "start_date", ("start date", "project start date"), "Optional project start date."
            ),
            FieldSpec(
                "planned_end_date",
                ("planned end", "planned end date", "target end date"),
                "Optional planned end date.",
            ),
            FieldSpec(
                "project_manager_employee_code",
                ("project manager employee code", "manager employee code", "manager code"),
                "Optional active employee code for the project manager.",
            ),
            FieldSpec(
                "project_manager_employee_id",
                ("project manager employee id", "manager employee id", "manager employee uuid"),
                "Optional active employee UUID for the project manager.",
            ),
        ),
        required=frozenset({"project_code", "name"}),
        mutually_exclusive=(
            frozenset({"project_manager_employee_code", "project_manager_employee_id"}),
        ),
        defaults=("status",),
    ),
    "resource_assignments": DatasetSpec(
        fields=(
            FieldSpec("project_code", ("project code", "project key"), "Existing project code."),
            FieldSpec("project_id", ("project id", "project uuid"), "Existing project UUID."),
            FieldSpec("employee_code", ("employee code", "staff code"), "Existing employee code."),
            FieldSpec(
                "employee_id",
                ("employee id", "employee uuid", "staff id"),
                "Existing employee UUID.",
            ),
            FieldSpec(
                "start_date", ("start date", "assignment start date"), "Assignment start date."
            ),
            FieldSpec("end_date", ("end date", "assignment end date"), "Assignment end date."),
            FieldSpec(
                "planned_hours_per_week",
                ("planned hours per week", "weekly hours", "hours per week"),
                "Planned weekly hours, greater than zero and at most 168.",
            ),
            FieldSpec(
                "status",
                ("status", "assignment status"),
                "New assignments may be proposed or committed; "
                "omitted status defaults to proposed.",
            ),
            FieldSpec(
                "notes",
                ("notes", "assignment notes"),
                "Optional notes; required for manager_adjustment source.",
            ),
            FieldSpec(
                "source",
                ("source", "assignment source"),
                "Optional source: manual or manager_adjustment.",
            ),
        ),
        required=frozenset({"start_date", "end_date", "planned_hours_per_week"}),
        required_one_of=(
            frozenset({"project_code", "project_id"}),
            frozenset({"employee_code", "employee_id"}),
        ),
        mutually_exclusive=(
            frozenset({"project_code", "project_id"}),
            frozenset({"employee_code", "employee_id"}),
        ),
        defaults=("status", "source"),
    ),
}


def normalize_header(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.casefold())).strip("_")


def field_definitions(dataset_type: ImportDataset) -> list[ImportFieldDefinition]:
    spec = DATASETS[dataset_type]
    return [
        ImportFieldDefinition(
            name=field.name,
            required=field.name in spec.required,
            description=field.description,
            aliases=[field.name.replace("_", " "), *field.aliases],
        )
        for field in spec.fields
    ]


def suggest_mapping(headers: list[str], dataset_type: ImportDataset) -> dict[str, str]:
    normalized_headers: dict[str, list[str]] = {}
    for header in headers:
        normalized_headers.setdefault(normalize_header(header), []).append(header)
    suggestions: dict[str, str] = {}
    used_headers: set[str] = set()
    for field in DATASETS[dataset_type].fields:
        candidate_aliases = {
            normalize_header(field.name),
            *(normalize_header(a) for a in field.aliases),
        }
        candidates = [
            header
            for alias in candidate_aliases
            for header in normalized_headers.get(alias, [])
            if header not in used_headers
        ]
        if len(candidates) == 1:
            suggestions[field.name] = candidates[0]
            used_headers.add(candidates[0])
    return suggestions


def suggested_datasets(headers: list[str]) -> list[ImportDataset]:
    result: list[ImportDataset] = []
    for dataset_type, spec in DATASETS.items():
        suggested = suggest_mapping(headers, dataset_type)
        required_present = spec.required.issubset(suggested)
        groups_present = all(
            any(field in suggested for field in group) for group in spec.required_one_of
        )
        if required_present and groups_present:
            result.append(dataset_type)
    return result
