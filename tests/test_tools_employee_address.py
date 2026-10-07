"""Address through the employee tools: written as dated rows, read as history.

HiBob's public API has no address endpoint; its table answers on the web app's
own route. The tools hide that, so these tests only talk to the tools.
"""

from __future__ import annotations

import json
from datetime import date

from conftest import call_tool
from people_data import EMPLOYEE_ID, FakePeople

TODAY = date.today().isoformat()
LEEDS = {
    "line1": "1 Old Street",
    "city": "Leeds",
    "postCode": "LS1 1AA",
    "country": "United Kingdom",
    "usaState": "England",
}


async def _update(mcp_server, **arguments) -> str:
    return await call_tool(mcp_server, "hibob_update_employee", arguments)


async def test_a_city_change_adds_an_address_row_that_carries_everything_else(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("address", "2024-03-01", **LEEDS)
    result = json.loads(
        await _update(
            mcp_server,
            employee="jane@x.com",
            changes={"City": "York"},
            effective_date="2026-11-01",
        )
    )
    expected = {**LEEDS, "city": "York", "effectiveDate": "2026-11-01"}
    assert fake.writes == ["row:address"]
    assert fake.posted == [("address", expected)]
    assert result["status"] == "updated"
    assert result["applied"][0]["via"] == "address row from 2026-11-01"
    assert result["applied"][0]["from"] == "Leeds"
    assert result["rows_added"] == [
        {"table": "address", "effective_date": "2026-11-01", "sent": expected}
    ]
    assert "unconfirmed" not in result
    assert fake.tables["address"][-1]["city"] == "York"


async def test_a_first_address_needs_nothing_to_carry_forward(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Address line 1": "17 Baalbec Road",
                "City": "London",
                "Zip/Post/Postal code": "N5 1QN",
                "Country": "united kingdom",
            },
            effective_date="2026-01-01",
        )
    )
    assert fake.posted == [
        (
            "address",
            {
                "effectiveDate": "2026-01-01",
                "line1": "17 Baalbec Road",
                "city": "London",
                "postCode": "N5 1QN",
                "country": "United Kingdom",
            },
        )
    ]
    assert result["status"] == "updated"
    assert "unconfirmed" not in result


async def test_a_country_not_in_the_list_is_refused_before_anything_is_written(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Country": "Atlantis"},
            effective_date="2026-01-01",
        )
    )
    assert result["status"] == "needs_input"
    assert "Atlantis" in result["questions"][0]["question"]
    assert fake.posted == []


async def test_an_address_change_without_a_date_asks_for_one(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"City": "York"})
    )
    assert result["status"] == "needs_input"
    assert result["questions"][0]["argument"] == "effective_date"
    assert fake.posted == [] and fake.reads == []


async def test_a_row_already_on_that_date_is_refused(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("address", "2026-11-01", **LEEDS)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"City": "York"},
        effective_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert "already has an address row dated 2026-11-01" in text
    assert fake.posted == []


async def test_a_future_dated_first_address_warns_that_hibob_counts_it_as_current(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"City": "York"},
            effective_date="2099-01-01",
        )
    )
    assert any("first address row as current" in w for w in result["warnings"])


async def test_a_column_hibob_blanks_is_reported_unconfirmed(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("address", "2024-03-01", **LEEDS)
    fake.drop_on_write["address"] = {"postCode"}
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"City": "York"},
            effective_date="2026-11-01",
        )
    )
    assert [u["field"] for u in result["unconfirmed"]] == [
        "address row from 2026-11-01 > postCode"
    ]


async def test_address_history_is_read_like_any_other_table(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("address", "2024-03-01", **LEEDS)
    fake.add_row("address", "2025-06-01", **{**LEEDS, "city": "York"})
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {"employee": EMPLOYEE_ID, "history": ["address"]},
        )
    )
    rows = result["history"]["address"]["rows"]
    assert [(r["effectiveDate"], r["city"]) for r in rows] == [
        ("2025-06-01", "York"),
        ("2024-03-01", "Leeds"),
    ]


async def test_the_fields_list_says_which_address_fields_can_be_changed(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "address"})
    )
    by_id = {f["id"]: f for f in result["fields"]}
    for field_id in (
        "address.line1",
        "address.line2",
        "address.city",
        "address.postCode",
        "address.country",
        "address.usaState",
    ):
        assert (by_id[field_id]["write"], by_id[field_id]["table"]) == (
            "dated",
            "address",
        ), field_id
    for field_id in ("address.fullAddress", "address.siteCity"):
        assert by_id[field_id]["write"] == "not_writable", field_id
        assert by_id[field_id]["reason"]


async def test_a_reason_is_recorded_on_the_address_row(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("address", "2024-03-01", **LEEDS)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"City": "York"},
            effective_date="2026-11-01",
            reason="Moved house",
        )
    )
    [(_, body)] = fake.posted
    assert body["change"] == {"reason": "Moved house"}
    assert fake.tables["address"][-1]["change"]["reason"] == "Moved house"
    assert not any("reason" in w for w in result.get("warnings", []))
    assert "unconfirmed" not in result
