from __future__ import annotations

from datetime import date

PROJECT_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"planned", "cancelled"}),
    "planned": frozenset({"active", "on_hold", "cancelled"}),
    "active": frozenset({"on_hold", "completed", "cancelled"}),
    "on_hold": frozenset({"active", "completed", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}

ASSIGNMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"committed", "cancelled"}),
    "committed": frozenset({"completed", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}

PROFICIENCY_LEVELS: dict[int, str] = {
    1: "beginner",
    2: "working",
    3: "proficient",
    4: "advanced",
    5: "expert",
}


def is_monday(value: date) -> bool:
    """Company-local calendar weeks use Monday as the first day of the week."""
    return value.isoweekday() == 1


def monday_for(value: date) -> date:
    return (
        value.replace(day=value.day)
        if is_monday(value)
        else value.fromordinal(value.toordinal() - value.isoweekday() + 1)
    )


def transition_allowed(
    current: str, requested: str, transitions: dict[str, frozenset[str]]
) -> bool:
    return current == requested or requested in transitions.get(current, frozenset())
