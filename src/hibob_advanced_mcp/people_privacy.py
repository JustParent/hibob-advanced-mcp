"""What people reads may still show when HIBOB_HIDE_PEOPLE_DATA is on.

Finding a person by name stays possible, and so does reaching them: the lock
allows a name, a work email, a work phone and a work site, and nothing else
from HiBob's people endpoints. Workforce planning is not covered.
"""

from __future__ import annotations

from typing import Any

from .config import ENV_HIDE_PEOPLE_DATA
from .people_fields import PeopleField

ALLOWED_FIELD_IDS = frozenset(
    {
        "root.id",
        "root.displayName",
        "root.fullName",
        "root.firstName",
        "root.surname",
        "root.email",
        "work.workPhone",
        "work.workMobile",
        "work.site",
        "work.siteId",
    }
)
# What hibob_get_employee reads when no fields are named.
LOCKED_DEFAULT_FIELDS = (
    "root.displayName",
    "root.email",
    "work.workPhone",
    "work.workMobile",
    "work.site",
)
SHOWN = "name, work email, work phone and work site"
HISTORY_REFUSAL = (
    f"{ENV_HIDE_PEOPLE_DATA} is on, so employee table history (work, salary, "
    "bank accounts and the rest) is not available. Only "
    f"{SHOWN} can be read."
)


def blocked_fields_message(blocked: list[PeopleField], known: list[PeopleField]) -> str:
    """Why these fields were refused, and which can be read instead."""
    refused = ", ".join(f"{f.qualified_label} ({f.id})" for f in blocked)
    allowed = ", ".join(
        sorted(
            f"{f.qualified_label} ({f.id})" for f in known if f.id in ALLOWED_FIELD_IDS
        )
    )
    return (
        f"{ENV_HIDE_PEOPLE_DATA} is on, so {refused} cannot be read. Only "
        f"{SHOWN} can be: {allowed or ', '.join(sorted(ALLOWED_FIELD_IDS))}."
    )


def _without(entry: Any, *keys: str) -> Any:
    if not isinstance(entry, dict):
        return entry
    return {k: v for k, v in entry.items() if k not in keys}


def scrub_person(person: Any) -> Any:
    """An employee's ID, name and email, without the job title."""
    return _without(person, "title")


def scrub_result(result: dict[str, Any]) -> dict[str, Any]:
    """A write tool's result without anything read back from HiBob.

    A change reports the value it replaced ("from") and a change HiBob did not
    keep reports what HiBob holds instead ("read"); both are people data. What
    the caller sent, and the employee's name and email, stay.
    """
    out = dict(result)
    if "employee" in out:
        out["employee"] = scrub_person(out["employee"])
    for key, hidden in (("applied", "from"), ("unconfirmed", "read")):
        if isinstance(out.get(key), list):
            out[key] = [_without(entry, hidden) for entry in out[key]]
    if isinstance(out.get("questions"), list):
        out["questions"] = [
            {**q, "candidates": [scrub_person(c) for c in q["candidates"]]}
            if isinstance(q, dict) and isinstance(q.get("candidates"), list)
            else q
            for q in out["questions"]
        ]
    return out
