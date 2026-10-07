"""HIBOB_HIDE_PEOPLE_DATA: people reads keep to name, email, phone and site."""

from __future__ import annotations

import json

import pytest

from conftest import call_tool
from hibob_advanced_mcp.config import ENV_HIDE_PEOPLE_DATA
from hibob_advanced_mcp.people_privacy import ALLOWED_FIELD_IDS, scrub_result
from hibob_advanced_mcp.server import _print_diagnostics, build_server
from people_data import EMPLOYEE_ID, FakePeople

# Everything HiBob holds for Jane in the fixtures that the lock must not show.
HIDDEN_VALUES = [
    "Analyst",
    "Sam Jones",
    "2024-03-01",
    "07700 900000",
    "Data",
    "Active",
]


@pytest.fixture
def locked(server_factory):
    return server_factory(hide_people_data=True)


def _employee_reads(mock_api) -> list[list[str]]:
    """The field lists sent to HiBob for one employee, in order."""
    return [
        json.loads(call.request.content)["fields"]
        for call in mock_api.calls
        if call.request.method == "POST"
        and call.request.url.path == f"/people/{EMPLOYEE_ID}"
    ]


async def _get(mcp, **arguments) -> str:
    return await call_tool(mcp, "hibob_get_employee", arguments)


async def test_the_default_read_is_name_email_phone_and_site_only(
    mock_api, locked
) -> None:
    FakePeople(mock_api)
    text = await _get(locked, employee=EMPLOYEE_ID)
    result = json.loads(text)
    assert {entry["id"] for entry in result["fields"]} <= ALLOWED_FIELD_IDS
    by_id = {entry["id"]: entry for entry in result["fields"]}
    assert by_id["root.displayName"]["value"] == "Jane Smith"
    assert by_id["root.email"]["value"] == "jane@x.com"
    assert by_id["work.workPhone"]["value"] == "020 7946 0000"
    assert "title" not in result["employee"]
    for hidden in HIDDEN_VALUES:
        assert hidden not in text


async def test_allowed_fields_can_be_named_by_label_or_id(mock_api, locked) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _get(
            locked,
            employee=EMPLOYEE_ID,
            fields=["Work phone", "root.email", "First name"],
        )
    )
    assert [entry["id"] for entry in result["fields"]] == [
        "work.workPhone",
        "root.email",
        "root.firstName",
    ]


@pytest.mark.parametrize(
    "field",
    [
        "Job title",
        "work.title",
        "Department",
        "Base salary",
        "payroll.salary.payment",
        "Mobile phone",
        "home.mobilePhone",
        "City",
        "Start date",
        "Reports to",
        "Status",
    ],
)
async def test_a_field_outside_the_allowlist_is_refused(
    mock_api, locked, field: str
) -> None:
    FakePeople(mock_api)
    text = await _get(locked, employee=EMPLOYEE_ID, fields=[field])
    assert text.startswith("Error:")
    assert ENV_HIDE_PEOPLE_DATA in text
    assert "Work phone" in text
    for fields in _employee_reads(mock_api):
        assert set(fields) <= ALLOWED_FIELD_IDS | {"work.title"}
    for hidden in HIDDEN_VALUES:
        assert hidden not in text


async def test_one_blocked_field_refuses_the_whole_read(mock_api, locked) -> None:
    FakePeople(mock_api)
    text = await _get(
        locked, employee=EMPLOYEE_ID, fields=["Work phone", "Base salary"]
    )
    assert text.startswith("Error:")
    assert "Base salary" in text
    assert "020 7946 0000" not in text


async def test_history_is_refused_without_asking_hibob(mock_api, locked) -> None:
    fake = FakePeople(mock_api)
    text = await _get(locked, employee=EMPLOYEE_ID, history=["salary", "bank accounts"])
    assert text.startswith("Error:")
    assert ENV_HIDE_PEOPLE_DATA in text
    assert fake.reads == []
    assert _employee_reads(mock_api) == []


async def test_a_name_still_finds_the_person(mock_api, locked) -> None:
    FakePeople(mock_api)
    result = json.loads(await _get(locked, employee="Jane Smith"))
    assert result["employee"]["id"] == EMPLOYEE_ID


async def test_the_lock_is_off_by_default(mock_api, server_factory) -> None:
    FakePeople(mock_api)
    result = json.loads(await _get(server_factory(), employee=EMPLOYEE_ID))
    assert any(entry["id"] == "work.title" for entry in result["fields"])
    assert result["employee"]["title"] == "Analyst"


async def test_field_metadata_stays_available(mock_api, locked) -> None:
    FakePeople(mock_api)
    result = json.loads(await call_tool(locked, "hibob_list_employee_fields", {}))
    assert any(field["id"] == "payroll.salary.payment" for field in result["fields"])


async def test_an_update_does_not_show_what_it_replaced(mock_api, locked) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(
            locked,
            "hibob_update_employee",
            {"employee": EMPLOYEE_ID, "changes": {"Shirt size": "Medium"}},
        )
    )
    assert result["status"] == "updated"
    [applied] = result["applied"]
    assert applied["to"] == "Medium"
    assert "from" not in applied
    assert "title" not in result["employee"]


async def test_a_new_row_does_not_show_the_row_it_copied(mock_api, locked) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(
            locked,
            "hibob_update_employee",
            {
                "employee": "jane@x.com",
                "changes": {"Job title": "Head of Data"},
                "effective_date": "2026-11-01",
            },
        )
    )
    assert result["status"] == "updated"
    assert "from" not in result["applied"][0]
    assert result["applied"][0]["to"] == "Head of Data"


async def test_an_unconfirmed_change_does_not_show_what_hibob_holds(
    mock_api, locked
) -> None:
    fake = FakePeople(mock_api)
    fake.apply_writes = False
    result = json.loads(
        await call_tool(
            locked,
            "hibob_update_employee",
            {"employee": EMPLOYEE_ID, "changes": {"Mobile phone": "1"}},
        )
    )
    assert result["unconfirmed"] == [{"field": "Home > Mobile phone", "sent": "1"}]


async def test_a_later_row_question_does_not_quote_the_row(mock_api, locked) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("work", "2025-01-01", title="103", department="202")
    text = await call_tool(
        locked,
        "hibob_update_employee",
        {
            "employee": EMPLOYEE_ID,
            "changes": {"Job title": "Head of Data"},
            "effective_date": "2024-06-01",
        },
    )
    asked = json.loads(text)
    [question] = asked["questions"]
    assert question["argument"] == "allow_later_rows"
    assert "2025-01-01" in question["question"]
    assert "103" not in text
    assert question["later_rows"] == [
        {"effectiveDate": "2025-01-01", "columns": ["title"]}
    ]


async def test_a_new_record_names_no_job_title(mock_api, locked) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(
            locked,
            "hibob_add_employee_record",
            {
                "employee": "jane@x.com",
                "record_type": "training",
                "values": {"Training name": "Training"},
            },
        )
    )
    assert result["status"] == "added"
    assert "title" not in result["employee"]


def test_scrub_result_drops_only_what_hibob_held() -> None:
    result = {
        "employee": {"id": "1", "name": "A", "email": "a@x.com", "title": "T"},
        "applied": [{"field": "F", "to": 2, "from": 1}],
        "unconfirmed": [{"field": "F", "sent": 2, "read": 1}],
        "questions": [
            {"candidates": [{"id": "1", "name": "A", "title": "T"}], "key": "k"}
        ],
    }
    assert scrub_result(result) == {
        "employee": {"id": "1", "name": "A", "email": "a@x.com"},
        "applied": [{"field": "F", "to": 2}],
        "unconfirmed": [{"field": "F", "sent": 2}],
        "questions": [{"candidates": [{"id": "1", "name": "A"}], "key": "k"}],
    }


async def test_the_environment_variable_locks_the_built_server(monkeypatch) -> None:
    monkeypatch.setenv(ENV_HIDE_PEOPLE_DATA, "true")
    text = await call_tool(
        build_server(),
        "hibob_get_employee",
        {"employee": EMPLOYEE_ID, "history": ["salary"]},
    )
    assert text.startswith("Error:")
    assert ENV_HIDE_PEOPLE_DATA in text


def test_diagnostics_say_whether_the_lock_is_on(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _print_diagnostics(build_server())
    assert "Hide people data: off" in capsys.readouterr().out
    monkeypatch.setenv(ENV_HIDE_PEOPLE_DATA, "yes")
    _print_diagnostics(build_server())
    assert "Hide people data: on" in capsys.readouterr().out
