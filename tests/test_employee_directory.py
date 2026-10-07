"""Employee lookup by ID, email or name, and the cached people reads."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from hibob_advanced_mcp.cache import NamedListCache
from hibob_advanced_mcp.client import HiBobClient
from hibob_advanced_mcp.employee_directory import (
    describe_candidates,
    find_employee,
    identity,
)
from hibob_advanced_mcp.people_api import people_fields, read_employee, read_table
from people_data import EMPLOYEE_ID, MANAGER_ID, FakePeople

REAL_READ = Path(__file__).parent / "fixtures" / "people" / "employee_read.json"


async def test_get_or_fetch_fetches_once() -> None:
    cache = NamedListCache()
    calls = []

    async def fetch() -> list[int]:
        calls.append(1)
        return [1]

    assert await cache.get_or_fetch(("k", False), fetch) == [1]
    assert await cache.get_or_fetch(("k", False), fetch) == [1]
    assert len(calls) == 1


async def test_find_by_email_reads_that_employee(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), " Jane@X.com ")
    assert match.employee == {
        "id": EMPLOYEE_ID,
        "name": "Jane Smith",
        "email": "jane@x.com",
        "title": "Analyst",
    }


async def test_find_by_id_given_as_a_number(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), int(MANAGER_ID))
    assert match.employee is not None
    assert match.employee["name"] == "Sam Jones"


async def test_find_by_name_ignores_case_and_repeated_spaces(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), "jane  SMITH")
    assert match.employee is not None
    assert match.employee["id"] == EMPLOYEE_ID


async def test_a_shared_name_is_ambiguous_with_both_candidates(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), "Alex Lee")
    assert match.employee is None
    assert match.ambiguous is True
    assert {c["email"] for c in match.candidates} == {
        "alex.lee@x.com",
        "alex.lee2@x.com",
    }
    assert "alex.lee2@x.com" in describe_candidates(match.candidates)


async def test_unknown_email_or_name_finds_nobody(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    cache = NamedListCache()
    assert (await find_employee(client, cache, "nobody@x.com")).employee is None
    match = await find_employee(client, cache, "Zed Zebra")
    assert match.employee is None
    assert match.ambiguous is False


async def test_the_directory_is_fetched_once_across_lookups(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    fake = FakePeople(mock_api)
    cache = NamedListCache()
    await find_employee(client, cache, "Jane Smith")
    await find_employee(client, cache, "Sam Jones")
    assert fake.directory.call_count == 1


async def test_people_fields_are_cached(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    cache = NamedListCache()
    await people_fields(client, cache)
    fields = await people_fields(client, cache)
    assert mock_api.calls.call_count == 1
    assert any(f.id == "work.title" for f in fields)


async def test_read_employee_unwraps_a_list_response(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/people/9").mock(
        return_value=httpx.Response(200, json={"employees": [{"id": "9"}]})
    )
    assert await read_employee(client, "9", ["root.id"]) == {"id": "9"}


async def test_read_table_sends_human_readable_as_a_string_and_sorts_newest_first(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"/people/{EMPLOYEE_ID}/work").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {"id": 1, "effectiveDate": "2024-03-01"},
                    {"id": 2, "effectiveDate": "2025-01-01"},
                ],
                "restricted_columns": {"no_view_permission": ["salary"]},
            },
        )
    )
    table = await read_table(client, EMPLOYEE_ID, "work")
    assert route.calls.last.request.url.params["includeHumanReadable"] == "true"
    assert [row["id"] for row in table["rows"]] == [2, 1]
    assert table["restricted_columns"] == {"no_view_permission": ["salary"]}


async def test_read_table_with_an_empty_body_has_no_rows(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/people/custom-tables/7/about__table_1").mock(
        return_value=httpx.Response(200)
    )
    table = await read_table(client, "7", "about__table_1", custom=True)
    assert table == {"rows": [], "restricted_columns": {}}


def test_real_read_fixture_gives_an_identity() -> None:
    if not REAL_READ.exists():
        return
    found = identity(json.loads(REAL_READ.read_text()))
    assert found is not None and found["id"]
