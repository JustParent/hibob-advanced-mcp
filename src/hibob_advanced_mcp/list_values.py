"""Turning the option names a user picked back into HiBob list item IDs.

A form shows each option by name, and what comes back from it is the name
the user chose; HiBob wants the item's ID. Between the two a bot may have
lost the form response that paired them, along with the ID of the list
behind the field. These pure functions take what it still has, the field's
label and the chosen names, and give back the IDs, or say precisely why they
cannot: a name that matches nothing comes back with the nearest items, and a
name two items share comes back as a choice rather than a guess.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from .envelopes import normalize_field_key, normalize_id
from .forms import (
    PATH_SEPARATOR,
    field_id_of,
    normalize_metadata_fields,
    rank_matches,
)

MAX_CANDIDATES = 5
MULTI_LIST_TYPE = "multi_list"


@dataclass(frozen=True)
class ListField:
    """A field that draws its values from a named list."""

    field_id: str
    name: str
    type: str | None
    list_id: str

    @property
    def multi(self) -> bool:
        return self.type == MULTI_LIST_TYPE


def _descriptor_parts(
    object_type: str, descriptor: dict[str, Any]
) -> tuple[str | None, str, str | None, str | None]:
    field_id = field_id_of(object_type, descriptor)
    name = str(descriptor.get("name") or field_id or "").strip()
    field_type = descriptor.get("fieldType")
    type_name = field_type.get("type") if isinstance(field_type, dict) else None
    type_data = field_type.get("typeData") if isinstance(field_type, dict) else None
    list_id = type_data.get("listId") if isinstance(type_data, dict) else None
    return (
        field_id,
        name,
        type_name if isinstance(type_name, str) else None,
        list_id if isinstance(list_id, str) and list_id else None,
    )


def find_list_field(object_type: str, metadata_payload: Any, field: Any) -> ListField:
    """The list-backed field a caller means, by ID or by the label HiBob shows."""
    wanted = str(field or "").strip()
    if not wanted:
        raise ValueError("Field cannot be empty.")
    try:
        wanted_id = normalize_field_key(object_type, wanted)
    except ValueError:
        wanted_id = None
    lowered = wanted.lower()

    list_backed: list[str] = []
    for descriptor in normalize_metadata_fields(metadata_payload):
        field_id, name, type_name, list_id = _descriptor_parts(object_type, descriptor)
        if list_id:
            list_backed.append(name)
        matched = (field_id is not None and field_id == wanted_id) or (
            name.lower() == lowered
        )
        if not matched or field_id is None:
            continue
        if list_id:
            return ListField(
                field_id=field_id, name=name, type=type_name, list_id=list_id
            )
        raise ValueError(
            f"{name!r} is a {type_name or 'plain'} field, not a list; submit its "
            "value as given."
        )
    raise ValueError(
        f"No {object_type} field called {wanted!r}. List-backed fields: "
        + ", ".join(list_backed)
        + "."
    )


def _walk(
    items: Any,
    prefix: str,
    leaves: list[dict[str, Any]],
    branches: list[dict[str, Any]],
) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None:
            name = item.get("value")
        name = "" if name is None else str(name)
        label = f"{prefix}{PATH_SEPARATOR}{name}" if prefix else name
        children = item.get("children")
        if isinstance(children, list) and children:
            below: list[dict[str, Any]] = []
            _walk(children, label, below, branches)
            branches.append({"name": name, "label": label, "leaves": below})
            leaves.extend(below)
            continue
        raw_id = item.get("id")
        leaves.append(
            {
                "id": None if raw_id is None else normalize_id(raw_id),
                "name": name,
                "label": label,
                "value": item.get("value"),
                "archived": bool(item.get("archived")),
            }
        )


def _candidate(leaf: dict[str, Any]) -> dict[str, Any]:
    candidate: dict[str, Any] = {"id": leaf["id"], "name": leaf["label"]}
    if leaf.get("archived"):
        candidate["archived"] = True
    return candidate


def _exact_matches(leaves: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    matched: list[dict[str, Any]] = []
    seen: set[str | None] = set()
    for leaf in leaves:
        value = leaf.get("value")
        hit = (
            leaf["id"] == text
            or leaf["label"].lower() == lowered
            or leaf["name"].lower() == lowered
            or (value is not None and str(value).strip().lower() == lowered)
        )
        if hit and leaf["id"] not in seen:
            seen.add(leaf["id"])
            matched.append(leaf)
    return matched


def _nearest(
    leaves: list[dict[str, Any]], branches: list[dict[str, Any]], text: str
) -> list[dict[str, Any]]:
    """What to offer for a name that matched no leaf.

    A branch of a tree named exactly is not submittable, so its leaves are
    offered; otherwise the leaves sharing words with the name, then the
    closest spellings.
    """
    lowered = text.lower()
    for branch in branches:
        if branch["name"].lower() == lowered or branch["label"].lower() == lowered:
            return [_candidate(leaf) for leaf in branch["leaves"][:MAX_CANDIDATES]]
    ranked = rank_matches(
        [{"id": leaf["id"], "name": leaf["label"]} for leaf in leaves], text
    )
    by_id = {leaf["id"]: leaf for leaf in leaves}
    picked: list[dict[str, Any]] = []
    for entry in ranked:
        leaf = by_id.get(entry["id"])
        if leaf is not None and leaf not in picked:
            picked.append(leaf)
    labels = {leaf["label"].lower(): leaf for leaf in leaves}
    for close in difflib.get_close_matches(lowered, list(labels), n=MAX_CANDIDATES):
        leaf = labels[close]
        if leaf not in picked:
            picked.append(leaf)
    return [_candidate(leaf) for leaf in picked[:MAX_CANDIDATES]]


def resolve_list_values(items: Any, values: list[Any]) -> dict[str, Any]:
    """Match each requested name (or ID) to one list item.

    Returns ``resolved`` (name to ID), ``values`` (the IDs, in the order
    given, ready to submit), ``ambiguous`` and ``unmatched`` (each with
    ``candidates`` to put to the user) and ``complete``.
    """
    leaves: list[dict[str, Any]] = []
    branches: list[dict[str, Any]] = []
    _walk(items, "", leaves, branches)

    requested: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("Values cannot be empty.")
        if text.lower() not in seen:
            seen.add(text.lower())
            requested.append(text)

    resolved: dict[str, str] = {}
    ids: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for text in requested:
        matched = _exact_matches(leaves, text)
        if len(matched) == 1:
            item_id = matched[0]["id"]
            resolved[text] = item_id
            if item_id not in ids:
                ids.append(item_id)
        elif matched:
            ambiguous.append(
                {"name": text, "candidates": [_candidate(leaf) for leaf in matched]}
            )
        else:
            unmatched.append(
                {"name": text, "candidates": _nearest(leaves, branches, text)}
            )
    return {
        "resolved": resolved,
        "values": ids,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "complete": not ambiguous and not unmatched,
    }
