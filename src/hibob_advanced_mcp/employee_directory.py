"""Finding the employee a user means: by ID, work email or display name.

An ID or email is read directly (POST /people/{identifier}), which finds
inactive employees too. A name is matched against a directory of active
employees, exactly but ignoring case and repeated spaces; several people
sharing it are returned as candidates rather than one being picked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cache import NamedListCache
from .client import HiBobClient
from .envelopes import normalize_id
from .forms import rank_matches
from .people_api import read_employee
from .people_fields import read_field

PEOPLE_SEARCH_PATH = "/people/search"
IDENTITY_FIELDS = ["root.id", "root.displayName", "root.email", "work.title"]
MAX_CANDIDATES = 5
EMPLOYEE_REF_DESCRIPTION = (
    "The employee, by HiBob employee ID, work email or display name."
)


@dataclass(frozen=True)
class EmployeeMatch:
    employee: dict[str, Any] | None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False


def identity(record: Any) -> dict[str, Any] | None:
    """ID, name, email and title from a people record."""
    employee_id, _ = read_field(record, "root.id")
    if employee_id in (None, ""):
        return None
    name, _ = read_field(record, "root.displayName")
    email, _ = read_field(record, "root.email")
    title, title_display = read_field(record, "work.title")
    found: dict[str, Any] = {
        "id": normalize_id(employee_id),
        "name": name,
        "email": email,
    }
    if title_display or title:
        found["title"] = title_display or title
    return found


def describe_candidates(candidates: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{c.get('name') or c['id']} ({c.get('email') or 'no email'}, ID {c['id']})"
        for c in candidates
    )


def _squash(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


async def _directory(
    client: HiBobClient, cache: NamedListCache
) -> list[dict[str, Any]]:
    async def fetch() -> list[dict[str, Any]]:
        payload = await client.search(
            PEOPLE_SEARCH_PATH, {"fields": IDENTITY_FIELDS, "humanReadable": "APPEND"}
        )
        employees = payload.get("employees") if isinstance(payload, dict) else None
        found = [identity(e) for e in employees or []]
        return [person for person in found if person]

    return await cache.get_or_fetch(("people:directory", False), fetch)


async def find_employee(
    client: HiBobClient, cache: NamedListCache, ref: str | int
) -> EmployeeMatch:
    text = str(ref).strip() if ref is not None else ""
    if not text:
        raise ValueError("employee must not be empty.")
    if "@" in text or text.isdigit():
        record = await read_employee(
            client,
            text.lower() if "@" in text else text,
            IDENTITY_FIELDS,
            human_readable=True,
        )
        return EmployeeMatch(identity(record) if record else None)
    people = await _directory(client, cache)
    wanted = _squash(text)
    exact = [person for person in people if _squash(person.get("name")) == wanted]
    if len(exact) == 1:
        return EmployeeMatch(exact[0])
    if exact:
        return EmployeeMatch(None, exact, ambiguous=True)
    by_id = {person["id"]: person for person in people}
    leaves = [
        {"id": person["id"], "name": person.get("name") or ""} for person in people
    ]
    near = rank_matches(leaves, text, require_all=True)[:MAX_CANDIDATES]
    return EmployeeMatch(None, [by_id[leaf["id"]] for leaf in near])
