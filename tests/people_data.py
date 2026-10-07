"""Employee metadata and records shaped like HiBob's, for the employee tools.

Modelled on the demo tenant's real metadata (tests/fixtures/people): there is
no "calculated" flag, work.site is plain text beside the dated work.siteId
(both labelled "Site"), and work.reportsTo has type "employee".
"""

from __future__ import annotations

import copy
import json as jsonlib
from typing import Any

import httpx
import respx


def _field(
    field_id: str,
    name: str,
    category: str,
    field_type: str,
    *,
    list_id: str | None = None,
    historical: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": field_id,
        "name": name,
        "categoryId": field_id.split(".")[0],
        "categoryDisplayName": category,
        "type": field_type,
        "jsonPath": field_id.removeprefix("root."),
        "historical": historical,
        "typeData": {"listId": list_id} if list_id else {},
    }
    return entry


FIELDS = [
    _field("root.id", "ID", "Basic info", "employee-reference"),
    _field("root.displayName", "Display name", "Basic info", "text"),
    _field("root.email", "Email", "Basic info", "text"),
    _field("root.firstName", "First name", "Basic info", "text"),
    _field("home.mobilePhone", "Mobile phone", "Home", "text"),
    _field("work.title", "Job title", "Work", "list", list_id="title", historical=True),
    _field(
        "work.department",
        "Department",
        "Work",
        "list",
        list_id="department",
        historical=True,
    ),
    _field("work.site", "Site", "Work", "text"),
    _field("work.siteId", "Site", "Work", "list_id", list_id="site", historical=True),
    _field("work.reportsTo", "Reports to", "Work", "employee", historical=True),
    _field("work.startDate", "Start date", "Work", "date"),
    _field("work.tenureDuration", "Tenure (duration)", "Work", "period"),
    _field(
        "peopleAnalytics.teamSizeRiskIndicator",
        "Team size risk",
        "People analytics",
        "text",
    ),
    _field(
        "internal.status",
        "Status",
        "Lifecycle",
        "list",
        list_id="status",
        historical=True,
    ),
    _field("address.city", "City", "Address", "text", historical=True),
    _field(
        "payroll.salary.payment", "Base salary", "Payroll", "currency", historical=True
    ),
    _field(
        "payroll.variable.Bonus.amount",
        "Bonus amount",
        "Payroll",
        "currency",
        historical=True,
    ),
    _field(
        "payroll.employment.contract",
        "Contract",
        "Employment",
        "list",
        list_id="employmentstatus",
        historical=True,
    ),
    _field(
        "payroll.employment.type",
        "Employment type",
        "Employment",
        "list",
        list_id="payrollEmploymentType",
        historical=True,
    ),
    _field(
        "payroll.employment.calendarId",
        "Holiday calendar ID",
        "Employment",
        "list_id",
        list_id="calendar",
        historical=True,
    ),
    _field("payroll.employment.fte", "FTE", "Employment", "number", historical=True),
    _field(
        "payroll.employment.personalWorkingPatternType",
        "Personal working pattern type",
        "Employment",
        "list",
        list_id="personalWorkingPatternTypes",
        historical=True,
    ),
    _field(
        "payroll.employment.workingPattern",
        "Working pattern",
        "Employment",
        "working_pattern",
        historical=True,
    ),
    _field(
        "payroll.employment.standardWorkingPattern.workingPatternId",
        "Full time working pattern",
        "Employment",
        "list_id",
        list_id="workingPattern_entity_list",
    ),
    _field(
        "payroll.salary.payPeriod",
        "Pay period",
        "Payroll",
        "list",
        list_id="payPeriod",
        historical=True,
    ),
    _field(
        "payroll.salary.payFrequency",
        "Pay frequency",
        "Payroll",
        "list",
        list_id="payFrequency",
        historical=True,
    ),
    _field("payroll.salary.yearlyPayment", "Yearly payment", "Payroll", "currency"),
    _field(
        "work.customColumns.column_55",
        "Cost centre",
        "Work",
        "text",
        historical=True,
    ),
    _field("work.custom.field_100", "Shirt size", "Work", "list", list_id="shirtSize"),
    _field("home.custom.field_200", "Start date", "Home", "date"),
    _field(
        "about.custom.field_300",
        "Languages",
        "About",
        "multi-list",
        list_id="languages",
    ),
    _field("work.custom.field_400", "Buddy", "Work", "employee-reference"),
    _field("financial.custom.field_500", "Bonus target", "Financial", "currency"),
    _field("personal.custom.field_600", "Passport scan", "Personal", "document"),
    _field("about.custom.field_700", "Remote", "About", "boolean"),
    _field("about.custom.field_800", "Desk number", "About", "number"),
]

CUSTOM_TABLES = {
    "tables": [
        {
            "id": "about__table_1",
            "name": "Certifications",
            "category": "about",
            "columns": [
                {
                    "id": "column_1",
                    "name": "Certificate",
                    "type": "list",
                    "typeData": {"listId": "certs"},
                    "mandatory": True,
                },
                {
                    "id": "column_2",
                    "name": "Expires",
                    "type": "date",
                    "mandatory": False,
                },
            ],
        }
    ]
}

EMPLOYEE_ID = "3332883884017713238"
MANAGER_ID = "3332883884017713999"

JANE: dict[str, Any] = {
    "id": EMPLOYEE_ID,
    "displayName": "Jane Smith",
    "email": "jane@x.com",
    "firstName": "Jane",
    "home": {"mobilePhone": "07700 900000"},
    "work": {
        "title": "101",
        "department": "201",
        "startDate": "2024-03-01",
        "reportsTo": {"id": MANAGER_ID, "email": "sam@x.com"},
        "custom": {"field_100": "L"},
    },
    "about": {"custom": {"field_300": ["1"], "field_700": False}},
    "internal": {"status": "Active"},
    "humanReadable": {
        "work": {
            "title": "Analyst",
            "department": "Data",
            "reportsTo": "Sam Jones",
            "custom": {"field_100": "Large"},
        }
    },
}
SAM: dict[str, Any] = {
    "id": MANAGER_ID,
    "displayName": "Sam Jones",
    "email": "sam@x.com",
    "work": {"title": "102"},
    "humanReadable": {"work": {"title": "Head of Data"}},
}
DIRECTORY = {
    "employees": [
        JANE,
        SAM,
        {"id": "77", "displayName": "Alex Lee", "email": "alex.lee@x.com"},
        {"id": "78", "displayName": "Alex Lee", "email": "alex.lee2@x.com"},
    ]
}
LISTS: dict[str, dict[str, Any]] = {
    "department": {
        "name": "department",
        "values": [
            {"id": "201", "name": "Data", "value": "Data"},
            {"id": "202", "name": "Marketing", "value": "Marketing"},
        ],
    },
    "shirtSize": {
        "name": "shirtSize",
        "values": [
            {"id": "M", "name": "Medium", "value": "Medium"},
            {"id": "L", "name": "Large", "value": "Large"},
            {"id": "L2", "name": "Large", "value": "Large"},
        ],
    },
    "languages": {
        "name": "languages",
        "values": [
            {"id": "1", "name": "Spanish", "value": "Spanish"},
            {"id": "2", "name": "French", "value": "French"},
        ],
    },
}


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


class FakePeople:
    """HiBob's people API, enough for the employee tools.

    Reads answer from ``records``; a PUT /people/{id} merges its body into the
    record unless ``apply_writes`` is false (as HiBob does with fields the
    service user may not edit). Every write is appended to ``writes``.
    """

    def __init__(self, mock_api: respx.MockRouter) -> None:
        self.records = {
            EMPLOYEE_ID: copy.deepcopy(JANE),
            MANAGER_ID: copy.deepcopy(SAM),
        }
        self.writes: list[str] = []
        self.apply_writes = True
        self.put_status = 200
        mock_api.get("/company/people/fields").mock(
            return_value=httpx.Response(200, json=FIELDS)
        )
        mock_api.get("/people/custom-tables/metadata").mock(
            return_value=httpx.Response(200, json=CUSTOM_TABLES)
        )
        self.directory = mock_api.post("/people/search").mock(
            return_value=httpx.Response(200, json=DIRECTORY)
        )
        for name, body in LISTS.items():
            mock_api.get(f"/company/named-lists/{name}").mock(
                return_value=httpx.Response(200, json=body)
            )
        for identifier in (EMPLOYEE_ID, MANAGER_ID, "jane@x.com", "sam@x.com"):
            mock_api.post(f"/people/{identifier}").mock(side_effect=self._read)
        mock_api.post("/people/nobody@x.com").mock(
            return_value=httpx.Response(404, json={})
        )
        self.put = mock_api.put(f"/people/{EMPLOYEE_ID}").mock(side_effect=self._put)
        self.start_date_status = 200
        self.email_status = 200
        self.start_date = mock_api.post(f"/employees/{EMPLOYEE_ID}/start-date").mock(
            side_effect=self._start_date
        )
        self.email = mock_api.put(f"/people/{EMPLOYEE_ID}/email").mock(
            side_effect=self._email
        )

    def _record(self, identifier: str) -> dict[str, Any] | None:
        for record in self.records.values():
            if identifier in (record["id"], record["email"]):
                return record
        return None

    def _read(self, request: httpx.Request) -> httpx.Response:
        record = self._record(request.url.path.rsplit("/", 1)[-1])
        if record is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=record)

    def _put(self, request: httpx.Request) -> httpx.Response:
        self.writes.append("fields")
        if self.put_status != 200:
            return httpx.Response(self.put_status)
        if self.apply_writes:
            _merge(self.records[EMPLOYEE_ID], jsonlib.loads(request.content))
        return httpx.Response(200)

    def _start_date(self, request: httpx.Request) -> httpx.Response:
        self.writes.append("start date")
        if self.start_date_status != 200:
            return httpx.Response(
                self.start_date_status, json={"error": "Bad start date"}
            )
        self.records[EMPLOYEE_ID]["work"]["startDate"] = jsonlib.loads(request.content)[
            "startDate"
        ]
        return httpx.Response(200)

    def _email(self, request: httpx.Request) -> httpx.Response:
        self.writes.append("email")
        email = jsonlib.loads(request.content)["email"]
        if email == self.records[EMPLOYEE_ID]["email"]:
            return httpx.Response(304)
        if self.email_status != 200:
            return httpx.Response(self.email_status, json={})
        self.records[EMPLOYEE_ID]["email"] = email
        return httpx.Response(200)
