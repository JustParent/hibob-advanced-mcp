"""HiBob employee write bodies, and checking what a read-back holds."""

from __future__ import annotations

from typing import Any

from .envelopes import normalize_id
from .people_fields import ROOT_PREFIX


def put_body(values: dict[str, Any]) -> dict[str, Any]:
    """PUT /people/{id}'s body: each value nested by its JSON path.

    "home.mobilePhone" goes under "home"; root fields ("root.firstName") go
    at the top level, as HiBob's reference shows.
    """
    body: dict[str, Any] = {}
    for path, value in values.items():
        if path.startswith(ROOT_PREFIX):
            path = path[len(ROOT_PREFIX) :]
        *parents, leaf = path.split(".")
        node = body
        for part in parents:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"{path} clashes with another change.")
            node = child
        node[leaf] = value
    return body


def same_value(sent: Any, read: Any) -> bool:
    """Whether a read-back holds what was sent, allowing for HiBob's shapes:
    a person read back as {"id", ...}, numbers as floats, lists reordered, and
    dicts compared by what was sent."""
    if isinstance(sent, dict) and isinstance(read, dict):
        if "id" in sent:
            return normalize_id(sent["id"]) == normalize_id(read.get("id"))
        if "value" in sent:
            return normalize_id(sent["value"]) == normalize_id(read.get("value")) and (
                str(sent.get("currency", "")).upper()
                == str(read.get("currency", "")).upper()
            )
        return all(
            key in read and same_value(item, read[key]) for key, item in sent.items()
        )
    if isinstance(read, dict) and "id" in read and not isinstance(sent, dict):
        read = read["id"]
    if isinstance(sent, list) and isinstance(read, list):
        return sorted(normalize_id(v) for v in sent) == sorted(
            normalize_id(v) for v in read
        )
    if isinstance(sent, bool):
        return str(sent).lower() == str(read).strip().lower()
    if sent is None or read is None:
        return sent is read
    return normalize_id(sent) == normalize_id(read)
