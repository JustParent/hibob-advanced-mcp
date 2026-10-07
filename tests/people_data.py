"""Employee metadata and records shaped like HiBob's, for the employee tools.

Modelled on the demo tenant's real metadata (tests/fixtures/people): there is
no "calculated" flag, work.site is plain text beside the dated work.siteId
(both labelled "Site"), and work.reportsTo has type "employee".
"""

from __future__ import annotations

from typing import Any


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
