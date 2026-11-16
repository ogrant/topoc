"""Unit tests for the topic catalog: `parse_catalog_json`, `Catalog` lookup and
`CatalogEntry.resolve_wire_path`.

Methodology: catalogs are built from inline JSON literals so each test shows
exactly the input it exercises. Accepted input is checked through the public
lookup API; rejected input through the `CatalogError` message, which must say
where the problem is so a user can find it.
"""

import pytest

from topoc.catalog import CatalogEntry
from topoc.catalog_json import parse_catalog_json
from topoc.errors import CatalogError


def catalog_error(json: str) -> str:
    """Message of the `CatalogError` that parsing `json` must raise."""
    with pytest.raises(CatalogError) as excinfo:
        parse_catalog_json(json)
    return str(excinfo.value)


def entry(json: str, definition: str) -> CatalogEntry:
    """The entry `definition` of the catalog parsed from `json`, which must exist."""
    found = parse_catalog_json(json).find(definition)
    assert found is not None
    return found


def test_indexes_by_definition_and_ignores_unknown_members() -> None:
    """Entries are found by `definition` with their members intact, an unknown definition
    yields `None`, and members outside the format (`extra`) are ignored."""
    catalog = parse_catalog_json(
        """[
        { "definition": "w::A", "name_pattern": "a", "extra": 1, "codec": "msgpack",
          "routed": false, "wire_keys": ["/s"] },
        { "definition": "w::B", "name_pattern": "b/{}", "codec": "json", "routed": true,
          "wire_keys": ["/msg/x"] }
    ]"""
    )
    a = catalog.find("w::A")
    b = catalog.find("w::B")
    assert a is not None
    assert b is not None
    assert (a.codec, a.wire_keys) == ("msgpack", ["/s"])
    assert b.routed
    assert catalog.find("w::missing") is None


def test_optional_members_may_be_omitted() -> None:
    """Only `definition` and `codec` are required; omitted members read as not routed,
    with no keys and no fields."""
    a = entry('[{ "definition": "w::A", "name_pattern": "a", "codec": "json" }]', "w::A")
    assert (a.routed, a.wire_keys, a.fields) == (False, [], [])


def test_resolves_field_paths_to_wire_via_field_tree() -> None:
    """Path resolution walks the field tree one segment at a time, mapping field
    names to wire names, nested fields included; an unknown field, a path
    descending into a scalar leaf, and a relative path all fail to resolve."""
    a = entry(
        """[{
        "definition": "w::A", "name_pattern": "a", "codec": "msgpack",
        "fields": [
            { "name": "symbol", "wire_name": "s" },
            { "name": "book", "wire_name": "b",
              "children": [ { "name": "venue", "wire_name": "v" } ] }
        ]
    }]""",
        "w::A",
    )
    assert a.resolve_wire_path("/symbol") == "/s"
    assert a.resolve_wire_path("/book/venue") == "/b/v"
    assert a.resolve_wire_path("/nope") is None
    assert a.resolve_wire_path("/symbol/x") is None
    assert a.resolve_wire_path("symbol") is None


@pytest.mark.parametrize(
    ("json", "expected"),
    [
        ("not json", "parsing topic catalog: invalid JSON"),
        ("{}", "catalog: expected an array"),
        ('[{ "definition": "w::A", "name_pattern": "a" }]', "entry 0: missing 'codec'"),
        (
            '[{ "definition": "w::A", "name_pattern": "a", "codec": 1 }]',
            "entry 0: 'codec' must be a string",
        ),
        (
            '[{ "definition": "w::A", "name_pattern": "a", "codec": "j", "routed": "yes" }]',
            "'routed' must be a boolean",
        ),
        (
            '[{ "definition": "w::A", "name_pattern": "a", "codec": "j", "wire_keys": [1] }]',
            "expected an array of strings",
        ),
        (
            '[{ "definition": "w::A", "name_pattern": "a", "codec": "j", '
            '"fields": [{ "name": "a" }] }]',
            "entry 0 field 0: missing 'wire_name'",
        ),
    ],
)
def test_rejects_malformed_input(json: str, expected: str) -> None:
    """Each kind of malformed input (invalid JSON, wrong top-level type, missing
    or mistyped members at entry and field level) yields an error that says
    where the problem is."""
    assert expected in catalog_error(json)


def test_requires_a_name_pattern() -> None:
    """`name_pattern` is required."""
    assert "entry 0: missing 'name_pattern'" in catalog_error(
        '[{ "definition": "w::A", "codec": "json" }]'
    )
    a = entry('[{ "definition": "w::A", "name_pattern": "o/{}/{}", "codec": "json" }]', "w::A")
    assert a.name_pattern == "o/{}/{}"


@pytest.mark.parametrize("pattern", ["o/x-{}", "o/{x}", "o/{", "o/}", "o/{{}}"])
def test_rejects_invalid_name_patterns(pattern: str) -> None:
    """A `name_pattern` may use braces only as whole-segment `{}` holes."""
    json = f'[{{ "definition": "w::A", "name_pattern": "{pattern}", "codec": "json" }}]'
    assert f"entry 0: invalid 'name_pattern' '{pattern}'" in catalog_error(json)


def test_rejects_duplicate_entries() -> None:
    """Two entries with the same `definition` are rejected rather than one silently
    winning."""
    assert "duplicate entry for 'w::A'" in catalog_error(
        '[{ "definition": "w::A", "name_pattern": "a", "codec": "json" }, '
        '{ "definition": "w::A", "name_pattern": "a", "codec": "msgpack" }]'
    )
