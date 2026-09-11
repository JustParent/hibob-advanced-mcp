"""Form assembly tests: field metadata plus named lists become one form."""

from __future__ import annotations

from typing import Any

from hibob_advanced_mcp.envelopes import (
    OBJECT_TYPE_BUDGET,
    OBJECT_TYPE_OPENING,
    OBJECT_TYPE_POSITION,
)
from hibob_advanced_mcp.forms import (
    DOCUMENTED_VALUES,
    MAX_OPTIONS_PER_LIST,
    READ_ONLY_FIELDS,
    REQUIRED_FIELDS,
    WRITABLE_FIELDS,
    build_form_section,
    collect_list_ids,
    count_list_items,
    field_id_of,
    index_named_lists,
    lookup_named_list,
    normalize_metadata_fields,
    shape_list_items,
)


def _field(
    field_id: str,
    *,
    name: str | None = None,
    field_type: str = "text",
    list_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "id": field_id,
        "name": name or field_id.rsplit("/", 1)[-1],
        "fieldType": {"type": field_type},
        "jsonPath": {"root": field_id, "rawData": f"{field_id}/value"},
    }
    if list_id:
        descriptor["fieldType"]["typeData"] = {"listId": list_id}
    descriptor.update(extra)
    return descriptor


POSITION_METADATA = [
    _field("/position/id", field_type="number"),
    _field(
        "/position/department",
        name="Department",
        field_type="list",
        list_id="department",
    ),
    _field("/position/site", name="Site", field_type="list", list_id="site"),
    _field(
        "/position/jobProfile",
        name="Job profile",
        field_type="list",
        list_id="jobProfile",
    ),
    _field("/position/effectiveDate", field_type="date"),
    _field("/position/fte", field_type="number", description="Job percentage."),
    _field("/position/positionType", field_type="list"),
    _field("/position/status", field_type="list"),
    _field("/position/customField_123", name="Cost centre"),
]

NAMED_LISTS = [
    {
        "name": "department",
        "items": [
            {
                "id": 10,
                "value": "Engineering",
                "name": "Engineering",
                "archived": False,
                "children": [],
            },
            {"id": 11, "value": "Sales", "name": "Sales", "archived": True},
        ],
    },
    {"name": "site", "items": [{"id": 20, "value": "London", "name": "London"}]},
]


def _by_id(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["id"]: field for field in section["fields"]}


# ---------------------------------------------------------------- normalizers


def test_normalize_metadata_accepts_list_single_descriptor_and_wrapper() -> None:
    descriptor = _field("/position/fte")
    assert normalize_metadata_fields([descriptor, "junk"]) == [descriptor]
    assert normalize_metadata_fields(descriptor) == [descriptor]
    assert normalize_metadata_fields({"fields": [descriptor]}) == [descriptor]
    assert normalize_metadata_fields({"unexpected": True}) == []
    assert normalize_metadata_fields(None) == []


def test_field_id_prefers_slash_ids_then_json_path_then_prefixes_bare_names() -> None:
    assert (
        field_id_of(OBJECT_TYPE_POSITION, {"id": "/position/fte/"}) == "/position/fte"
    )
    assert (
        field_id_of(
            OBJECT_TYPE_POSITION, {"id": "fte", "jsonPath": {"root": "/position/fte"}}
        )
        == "/position/fte"
    )
    assert field_id_of(OBJECT_TYPE_POSITION, {"id": "fte"}) == "/position/fte"
    assert field_id_of(OBJECT_TYPE_POSITION, {"name": "no id"}) is None


def test_index_named_lists_accepts_array_and_mapping_shapes() -> None:
    from_array = index_named_lists(NAMED_LISTS)
    from_mapping = index_named_lists(
        {
            "department": {"items": NAMED_LISTS[0]["items"]},
            "site": NAMED_LISTS[1]["items"],
        }
    )
    assert set(from_array) == {"department", "site"}
    assert from_array == from_mapping
    assert index_named_lists("nonsense") == {}


def test_lookup_named_list_tolerates_case_differences() -> None:
    index = index_named_lists(NAMED_LISTS)
    assert lookup_named_list(index, "Department") == NAMED_LISTS[0]["items"]
    assert lookup_named_list(index, "jobProfile") is None


def test_shape_list_items_keeps_only_what_a_drop_down_needs() -> None:
    shaped = shape_list_items(
        [
            {
                "id": 1,
                "value": "eng",
                "name": "Engineering",
                "archived": False,
                "children": [
                    {"id": 2, "value": "Platform", "name": "Platform", "archived": True}
                ],
            },
            "not an item",
        ]
    )
    assert shaped == [
        {
            "id": 1,
            "name": "Engineering",
            "value": "eng",
            "children": [{"id": 2, "name": "Platform", "archived": True}],
        }
    ]


def test_count_list_items_includes_nested_children() -> None:
    items = [
        {"id": 1, "children": [{"id": 2}, {"id": 3, "children": [{"id": 4}]}]},
        {"id": 5},
        "junk",
    ]
    assert count_list_items(items) == 5
    assert count_list_items(None) == 0


def test_shape_list_items_limit_counts_nested_children() -> None:
    items = [
        {
            "id": "a",
            "name": "A",
            "children": [{"id": f"a{i}", "name": f"A{i}"} for i in range(3)],
        },
        {
            "id": "b",
            "name": "B",
            "children": [{"id": f"b{i}", "name": f"B{i}"} for i in range(3)],
        },
    ]

    shaped = shape_list_items(items, limit=6)

    assert [option["id"] for option in shaped] == ["a", "b"]
    assert len(shaped[0]["children"]) == 3
    assert [option["id"] for option in shaped[1]["children"]] == ["b0"]
    # A limit no smaller than the list changes nothing.
    assert shape_list_items(items, limit=8) == shape_list_items(items)


def test_collect_list_ids_gathers_across_payloads() -> None:
    assert collect_list_ids(
        POSITION_METADATA, [_field("/x/y", list_id="currency")]
    ) == {
        "department",
        "site",
        "jobProfile",
        "currency",
    }


# ------------------------------------------------------------------ sections


def test_section_resolves_options_and_marks_required_fields() -> None:
    section = build_form_section(
        OBJECT_TYPE_POSITION,
        POSITION_METADATA,
        index_named_lists(NAMED_LISTS),
        role="primary",
        argument="position_fields",
    )
    fields = _by_id(section)

    assert section["role"] == "primary"
    assert section["argument"] == "position_fields"
    assert fields["/position/department"]["options"] == [
        {"id": 10, "name": "Engineering"},
        {"id": 11, "name": "Sales", "archived": True},
    ]
    assert fields["/position/department"]["list_id"] == "department"
    assert fields["/position/department"]["required"] is True
    assert fields["/position/fte"]["required"] is True
    assert fields["/position/fte"]["description"] == "Job percentage."
    assert fields["/position/positionType"]["required"] is False
    assert set(section["required_fields"]) == set(REQUIRED_FIELDS[OBJECT_TYPE_POSITION])


def test_section_lists_required_fields_first() -> None:
    section = build_form_section(
        OBJECT_TYPE_POSITION,
        POSITION_METADATA,
        index_named_lists(NAMED_LISTS),
        role="primary",
        argument="position_fields",
    )
    required_flags = [field["required"] for field in section["fields"]]
    assert required_flags == sorted(required_flags, reverse=True)


def test_section_separates_read_only_fields_from_inputs() -> None:
    section = build_form_section(
        OBJECT_TYPE_POSITION, POSITION_METADATA, {}, role="primary", argument="f"
    )
    read_only_ids = {field["id"] for field in section["read_only_fields"]}
    assert read_only_ids == {"/position/id", "/position/status"}
    assert "/position/status" not in _by_id(section)
    assert section["read_only_fields"][0]["type"] == "number"


def test_section_uses_documented_values_only_without_a_named_list() -> None:
    metadata = [
        _field("/position/positionType", field_type="list"),
        _field("/position/employmentType", field_type="list", list_id="employmentType"),
    ]
    lists = {"employmentType": [{"id": "perm", "name": "Permanent"}]}
    fields = _by_id(
        build_form_section(
            OBJECT_TYPE_POSITION, metadata, lists, role="primary", argument="f"
        )
    )

    assert fields["/position/positionType"]["allowed_values"] == list(
        DOCUMENTED_VALUES["/position/positionType"]
    )
    assert "options" not in fields["/position/positionType"]
    assert fields["/position/employmentType"]["options"] == [
        {"id": "perm", "name": "Permanent"}
    ]
    assert "allowed_values" not in fields["/position/employmentType"]


def _job_profiles(count: int) -> list[dict[str, Any]]:
    return [{"id": i, "name": f"Profile {i}"} for i in range(count)]


def _job_profile_field(items: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = [_field("/position/jobProfile", field_type="list", list_id="jobProfile")]
    section = build_form_section(
        OBJECT_TYPE_POSITION, metadata, {"jobProfile": items}, role="p", argument="f"
    )
    return _by_id(section)["/position/jobProfile"]


def test_section_lists_every_option_up_to_the_cap() -> None:
    field = _job_profile_field(_job_profiles(MAX_OPTIONS_PER_LIST))

    assert [option["id"] for option in field["options"]] == list(
        range(MAX_OPTIONS_PER_LIST)
    )
    assert "options_truncated" not in field
    assert "options_note" not in field


def test_section_truncates_long_lists_and_says_how_to_get_the_rest() -> None:
    field = _job_profile_field(_job_profiles(MAX_OPTIONS_PER_LIST + 50))

    assert len(field["options"]) == MAX_OPTIONS_PER_LIST
    assert field["options"][-1]["id"] == MAX_OPTIONS_PER_LIST - 1
    assert field["options_truncated"] is True
    assert field["options_shown"] == MAX_OPTIONS_PER_LIST
    assert field["options_total"] == MAX_OPTIONS_PER_LIST + 50
    assert "hibob_get_company_named_lists" in field["options_note"]
    assert "list_name='jobProfile'" in field["options_note"]


def test_section_reports_lists_it_could_not_resolve() -> None:
    section = build_form_section(
        OBJECT_TYPE_POSITION,
        POSITION_METADATA,
        index_named_lists(NAMED_LISTS),
        role="primary",
        argument="f",
    )
    assert section["unresolved_lists"] == ["jobProfile"]
    assert "options" not in _by_id(section)["/position/jobProfile"]
    assert _by_id(section)["/position/jobProfile"]["list_id"] == "jobProfile"


def test_section_adds_documented_fields_the_metadata_omits() -> None:
    """A required field must be on the form even if metadata leaves it out."""
    metadata = [
        _field("/positionBudget/currency", field_type="list", list_id="currency")
    ]
    section = build_form_section(
        OBJECT_TYPE_BUDGET,
        metadata,
        {"currency": []},
        role="primary",
        argument="fields",
    )
    fields = _by_id(section)

    assert fields["/positionBudget/salaryPayPeriod"]["required"] is True
    assert "note" in fields["/positionBudget/salaryPayPeriod"]
    assert fields["/positionBudget/salaryPayPeriod"]["allowed_values"] == list(
        DOCUMENTED_VALUES["/positionBudget/salaryPayPeriod"]
    )
    assert fields["/positionBudget/salaryPayPeriod"]["name"] == "Salary pay period"
    assert set(fields) == set(WRITABLE_FIELDS[OBJECT_TYPE_BUDGET])


def test_section_flags_fields_outside_the_documented_payload() -> None:
    fields = _by_id(
        build_form_section(
            OBJECT_TYPE_POSITION, POSITION_METADATA, {}, role="p", argument="f"
        )
    )
    assert fields["/position/customField_123"]["documented"] is False
    assert "documented" not in fields["/position/department"]


def test_section_honours_a_required_flag_from_metadata() -> None:
    metadata = [_field("/positionOpening/positionOpeningName", required=True)]
    fields = _by_id(
        build_form_section(OBJECT_TYPE_OPENING, metadata, {}, role="p", argument="f")
    )
    assert fields["/positionOpening/positionOpeningName"]["required"] is True


def test_section_ignores_duplicate_descriptors() -> None:
    metadata = [_field("/position/fte"), _field("/position/fte")]
    section = build_form_section(
        OBJECT_TYPE_POSITION, metadata, {}, role="p", argument="f"
    )
    assert [f["id"] for f in section["fields"]].count("/position/fte") == 1


# ----------------------------------------------------------------- constants


def test_field_knowledge_is_internally_consistent() -> None:
    for object_type in (OBJECT_TYPE_POSITION, OBJECT_TYPE_OPENING, OBJECT_TYPE_BUDGET):
        writable = set(WRITABLE_FIELDS[object_type])
        assert REQUIRED_FIELDS[object_type] <= writable
        assert not (READ_ONLY_FIELDS[object_type] & writable)
        prefix = f"/{object_type}/"
        assert all(
            f.startswith(prefix) for f in writable | READ_ONLY_FIELDS[object_type]
        )
    all_writable = {f for fields in WRITABLE_FIELDS.values() for f in fields}
    assert set(DOCUMENTED_VALUES) <= all_writable
