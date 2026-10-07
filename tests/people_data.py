"""Employee metadata and records shaped like HiBob's, for the employee tools.

Modelled on the demo tenant's real metadata (tests/fixtures/people): there is
no "calculated" flag, work.site is plain text beside the dated work.siteId
(both labelled "Site"), and work.reportsTo has type "employee".
"""

from __future__ import annotations

import copy
import datetime as dt
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
    _field("work.workPhone", "Work phone", "Work contact", "text"),
    _field("work.workMobile", "Work mobile", "Work contact", "text"),
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
        "Employment contract",
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
        "Salary pay period",
        "Payroll",
        "list",
        list_id="payPeriod",
        historical=True,
    ),
    _field(
        "payroll.salary.payFrequency",
        "Salary pay frequency",
        "Payroll",
        "list",
        list_id="payFrequency",
        historical=True,
    ),
    _field("payroll.salary.yearlyPayment", "Yearly payment", "Payroll", "currency"),
    _field(
        "work.manager",
        "Manager",
        "Work",
        "employee-reference",
        historical=True,
    ),
    _field(
        "work.workChangeType",
        "Change type",
        "Work",
        "list",
        list_id="workChangeType",
        historical=True,
    ),
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
        "workPhone": "020 7946 0000",
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
        {"id": "79", "displayName": "Priya Patel", "email": "priya@x.com"},
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
    "title": {
        "name": "title",
        "values": [
            {"id": "101", "name": "Analyst", "value": "Analyst"},
            {"id": "102", "name": "Head of Data", "value": "Head of Data"},
            {"id": "103", "name": "Director", "value": "Director"},
        ],
    },
    "site": {
        "name": "site",
        "values": [
            {"id": 2606110, "name": "London (Demo)", "value": "London (Demo)"},
            {"id": 2606111, "name": "New York (Demo)", "value": "New York (Demo)"},
            {"id": 2606112, "name": "Madrid (Demo)", "value": "Madrid (Demo)"},
        ],
    },
    "employmentstatus": {
        "name": "employmentstatus",
        "values": [
            {"id": "Full time", "name": "Full time", "value": "Full time"},
            {"id": "Part time", "name": "Part time", "value": "Part time"},
        ],
    },
    "payrollEmploymentType": {
        "name": "payrollEmploymentType",
        "values": [
            {"id": "Permanent", "name": "Permanent", "value": "Permanent"},
            {"id": "Temporary", "name": "Temporary", "value": "Temporary"},
        ],
    },
    "calendar": {
        "name": "calendar",
        "values": [
            {
                "id": 2657450,
                "name": "Canada bank holidays",
                "value": "Canada bank holidays",
            },
            {
                "id": 2657449,
                "name": "Hong Kong bank holidays",
                "value": "Hong Kong bank holidays",
            },
        ],
    },
    "payPeriod": {
        "name": "payPeriod",
        "values": [
            {"id": "Annual", "name": "Annual", "value": "Annual"},
            {"id": "Monthly", "name": "Monthly", "value": "Monthly"},
        ],
    },
    "payFrequency": {
        "name": "payFrequency",
        "values": [
            {"id": "Weekly", "name": "Weekly", "value": "Weekly"},
            {"id": "Monthly", "name": "Monthly", "value": "Monthly"},
        ],
    },
    "payType": {
        "name": "payType",
        "values": [
            {"id": "Bonus", "name": "Bonus", "value": "Bonus"},
            {
                "id": "Executive bonus",
                "name": "Executive bonus",
                "value": "Executive bonus",
            },
            {"id": "Commission", "name": "Commission", "value": "Commission"},
        ],
    },
    "variablePayPeriod": {
        "name": "variablePayPeriod",
        "values": [
            {"id": "Monthly", "name": "Monthly", "value": "Monthly"},
            {"id": "Annual", "name": "Annual", "value": "Annual"},
            {"id": "Quarterly", "name": "Quarterly", "value": "Quarterly"},
        ],
    },
    "entitlementType": {
        "name": "entitlementType",
        "values": [
            {"id": "ET1", "name": "Lunch vouchers", "value": "Lunch vouchers"},
            {"id": "ET2", "name": "Company Car", "value": "Company Car"},
        ],
    },
    "deductionType": {
        "name": "deductionType",
        "values": [
            {"id": "Company Car", "name": "Company Car", "value": "Company Car"},
            {"id": "Cycle to work", "name": "Cycle to work", "value": "Cycle to work"},
        ],
    },
    "trainingName": {
        "name": "trainingName",
        "values": [{"id": "Training", "name": "Training", "value": "Training"}],
    },
    "trainingStatus": {
        "name": "trainingStatus",
        "values": [
            {"id": "Invited", "name": "Invited", "value": "Invited"},
            {"id": "Completed", "name": "Completed", "value": "Completed"},
        ],
    },
    "trainingFrequency": {
        "name": "trainingFrequency",
        "values": [
            {"id": "Once", "name": "Once", "value": "Once"},
            {"id": "Yearly", "name": "Yearly", "value": "Yearly"},
        ],
    },
    "bankaccounttype": {
        "name": "bankaccounttype",
        "values": [
            {"id": "Current", "name": "Current", "value": "Current"},
            {"id": "Savings", "name": "Savings", "value": "Savings"},
        ],
    },
    "allocation": {
        "name": "allocation",
        "values": [
            {"id": "percent", "name": "%", "value": "%"},
            {"id": "remaining", "name": "Remaining", "value": "Remaining"},
        ],
    },
    "legalGender": {
        "name": "legalGender",
        "values": [
            {"id": "Female", "name": "Female", "value": "Female"},
            {"id": "Male", "name": "Male", "value": "Male"},
        ],
    },
    "equityTypes": {
        "name": "equityTypes",
        "values": [
            {"id": "Options", "name": "Options", "value": "Options"},
            {"id": "RSU", "name": "RSU", "value": "RSU"},
        ],
    },
    "grantTypes": {
        "name": "grantTypes",
        "values": [
            {"id": "Initial Grant", "name": "Initial Grant", "value": "Initial Grant"},
            {"id": "Merit Grant", "name": "Merit Grant", "value": "Merit Grant"},
        ],
    },
    "grantStatuses": {
        "name": "grantStatuses",
        "values": [
            {"id": "Granted", "name": "Granted", "value": "Granted"},
            {
                "id": "Pending Approval",
                "name": "Pending Approval",
                "value": "Pending Approval",
            },
        ],
    },
    "workChangeType": {
        "name": "workChangeType",
        "values": [
            {"id": "Promotion", "name": "Promotion", "value": "Promotion"},
            {"id": "Lateral Move", "name": "Lateral Move", "value": "Lateral Move"},
            {"id": "Other", "name": "Other", "value": "Other"},
        ],
    },
    "certs": {
        "name": "certs",
        "values": [
            {"id": "C1", "name": "First aid", "value": "First aid"},
            {"id": "C2", "name": "Fire warden", "value": "Fire warden"},
        ],
    },
}


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


PATTERN = {
    "workingPatternType": "hourly",
    "days": {
        "monday": 8,
        "tuesday": 8,
        "wednesday": 8,
        "thursday": 8,
        "friday": 8,
        "saturday": 0,
        "sunday": 0,
    },
    "hoursPerDay": 8,
    "workingPatternId": 0,
}
SITES = {
    2606110: "London (Demo)",
    2606111: "New York (Demo)",
    2606112: "Madrid (Demo)",
}
# Every column a row has; HiBob stores any a write leaves out as null.
TABLE_COLUMNS = {
    "work": ("title", "department", "site", "siteId", "reportsTo", "workChangeType"),
    "employment": (
        "contract",
        "type",
        "salaryPayType",
        "flsaCode",
        "calendarId",
        "calendarName",
        "personalWorkingPatternType",
        "workingPattern",
        "standardWorkingPattern",
        "standardWorkingPatternId",
        "siteWorkingPattern",
        "actualWorkingPattern",
        "hoursInDayNotWorked",
        "fte",
        "weeklyHours",
    ),
    "salaries": ("base", "payPeriod", "payFrequency"),
}


def row_header(row_id: int, day: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "id": row_id,
        "effectiveDate": day,
        "endEffectiveDate": None,
        "isCurrent": False,
        "canBeDeleted": True,
        "change": {"reason": reason, "changedBy": None, "changedById": "1"},
        "creationDate": None,
        "modificationDate": day,
        "activeEffectiveDate": day,
    }


def _renumber(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: row["effectiveDate"])
    today = dt.date.today().isoformat()
    current = None
    for row in rows:
        row["isCurrent"] = False
        if row["effectiveDate"] <= today:
            current = row
    if current is not None:
        current["isCurrent"] = True


def _start_tables() -> dict[str, list[dict[str, Any]]]:
    work = {
        **row_header(1, "2024-03-01"),
        **{column: None for column in TABLE_COLUMNS["work"]},
        "title": "101",
        "department": "201",
        "site": SITES[2606110],
        "siteId": 2606110,
        "reportsTo": {
            "id": MANAGER_ID,
            "firstName": "Sam",
            "surname": "Jones",
            "email": "sam@x.com",
            "displayName": "Sam Jones",
        },
        "workChangeType": "New Employee",
        "customColumns": {},
    }
    employment = {
        **row_header(1, "2024-03-01"),
        **{column: None for column in TABLE_COLUMNS["employment"]},
        "contract": "Full time",
        "siteWorkingPattern": PATTERN,
        "actualWorkingPattern": PATTERN,
        "hoursInDayNotWorked": 8,
        "fte": 100,
        "weeklyHours": 40,
        "customColumns": {},
    }
    tables: dict[str, list[dict[str, Any]]] = {
        "work": [work],
        "employment": [employment],
        "salaries": [],
    }
    for rows in tables.values():
        _renumber(rows)
    return tables


# Where each record type is read (a table read or a bulk read) and whether
# HiBob answers a write with {"entryId": n}.
RECORD_READS = {
    "variable": "table",
    "equities": "table",
    "training": "table",
    "bank-accounts": "table",
    "entitlement": "bulk",
    "deduction": "bulk",
    "dependents": "bulk",
    "right-to-work": "bulk",
    "about__table_1": "custom",
}
RECORD_ENTRY_IDS = {
    "variable",
    "entitlement",
    "deduction",
    "dependents",
    "right-to-work",
}


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
        self.tables = _start_tables()
        self.restricted: dict[str, dict[str, Any]] = {}
        self.row_status: dict[str, int] = {}
        self.drop_on_write: dict[str, set[str]] = {}
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.reads: list[str] = []
        for path in self.tables:
            mock_api.get(f"/people/{EMPLOYEE_ID}/{path}").mock(
                side_effect=self._table_read(path)
            )
            mock_api.post(f"/people/{EMPLOYEE_ID}/{path}").mock(
                side_effect=self._table_write(path)
            )
        self.bulk_errors: dict[str, list[dict[str, Any]]] = {}
        self.records_by_path: dict[str, list[dict[str, Any]]] = {
            path: [] for path in RECORD_READS
        }
        for path, how in RECORD_READS.items():
            if how == "bulk":
                mock_api.get(f"/bulk/people/{path}").mock(
                    side_effect=self._record_bulk_read(path)
                )
                mock_api.post(f"/people/{EMPLOYEE_ID}/{path}").mock(
                    side_effect=self._record_write(path)
                )
            elif how == "custom":
                base = f"/people/custom-tables/{EMPLOYEE_ID}/{path}"
                mock_api.get(base).mock(side_effect=self._record_read(path))
                mock_api.post(base).mock(side_effect=self._record_write(path))
            else:
                mock_api.get(f"/people/{EMPLOYEE_ID}/{path}").mock(
                    side_effect=self._record_read(path)
                )
                mock_api.post(f"/people/{EMPLOYEE_ID}/{path}").mock(
                    side_effect=self._record_write(path)
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

    def add_row(self, path: str, day: str, **columns: Any) -> dict[str, Any]:
        rows = self.tables[path]
        row = {
            **row_header(max((r["id"] for r in rows), default=0) + 1, day),
            **{column: None for column in TABLE_COLUMNS[path]},
            "customColumns": {},
            **columns,
        }
        rows.append(row)
        _renumber(rows)
        return row

    def _table_read(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            self.reads.append(path)
            return httpx.Response(
                200,
                json={
                    "values": self.tables[path],
                    "restricted_columns": self.restricted.get(path, {}),
                },
            )

        return handler

    def _table_write(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            body = jsonlib.loads(request.content)
            self.writes.append(f"row:{path}")
            self.posted.append((path, body))
            status = self.row_status.get(path, 200)
            if status != 200:
                return httpx.Response(
                    status,
                    json={
                        "key": "exception.history.duplicated.bulk",
                        "error": "Duplicate effective date for work, please "
                        "update the effective date.",
                    },
                )
            if path == "salaries" and not body.get("payFrequency"):
                return httpx.Response(400, json={"error": "Missing pay frequency"})
            columns = TABLE_COLUMNS[path]
            stored: dict[str, Any] = {column: None for column in columns}
            stored.update({k: v for k, v in body.items() if k in columns})
            custom = dict(body.get("customColumns") or {})
            custom.update({k: v for k, v in body.items() if k.startswith("column_")})
            stored["customColumns"] = custom
            if stored.get("siteId") is not None and not stored.get("site"):
                stored["site"] = SITES.get(stored["siteId"])
            for column in self.drop_on_write.get(path, ()):
                stored[column] = None
            rows = self.tables[path]
            header = row_header(
                max((r["id"] for r in rows), default=0) + 1,
                body["effectiveDate"],
                body.get("reason"),
            )
            rows.append({**header, **stored})
            _renumber(rows)
            return httpx.Response(200)

        return handler

    def add_record(self, path: str, **columns: Any) -> dict[str, Any]:
        rows = self.records_by_path[path]
        row = {"id": max((r["id"] for r in rows), default=100) + 1, **columns}
        rows.append(row)
        return row

    def _record_read(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            self.reads.append(path)
            return httpx.Response(200, json={"values": self.records_by_path[path]})

        return handler

    def _record_bulk_read(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            self.reads.append(path)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "employeeId": EMPLOYEE_ID,
                            "values": self.records_by_path[path],
                        }
                    ],
                    "response_metadata": {"next_cursor": None},
                    "errors": self.bulk_errors.get(path, []),
                },
            )

        return handler

    def _record_write(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            body = jsonlib.loads(request.content)
            row = dict(body["values"][0]) if "values" in body else dict(body)
            self.writes.append(f"record:{path}")
            self.posted.append((path, body))
            status = self.row_status.get(path, 200)
            if status != 200:
                error = (
                    {"error": f"Duplicate effective date for {path}."}
                    if status == 400
                    else {}
                )
                return httpx.Response(status, json=error)
            rows = self.records_by_path[path]
            duplicate = path == "deduction" and any(
                r.get("effectiveDate") == row.get("effectiveDate")
                and r.get("deduction") == row.get("deduction")
                for r in rows
            )
            if duplicate:
                return httpx.Response(
                    400, json={"error": "Duplicate effective date for deduction."}
                )
            for column in self.drop_on_write.get(path, ()):
                row[column] = None
            stored = self.add_record(path, **row)
            if path in RECORD_ENTRY_IDS:
                return httpx.Response(200, json={"entryId": stored["id"]})
            return httpx.Response(200)

        return handler
