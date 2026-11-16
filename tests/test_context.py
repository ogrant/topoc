"""Reads generation context data from JSON and checks it against a topology's
`context` declaration: document order, duplicate keys, the declaration's
well-formedness (V20) and the strict conformance of the data (V21).

Methodology: `Context.from_json` is tested directly. The declaration and the
conformance check run inside compilation, so those tests compile a one-line
topology against a small JSON context and assert the exact `ContextError` or
`CompileError` message, path included.
"""

import pytest

from topoc.context import Context, ObjectData
from topoc.errors import CompileError, ContextError
from topoc.resolve import compile_topology


def read_error(text: str) -> str:
    """Message of the `ContextError` that reading `text` must raise."""
    with pytest.raises(ContextError) as excinfo:
        Context.from_json(text)
    return str(excinfo.value)


def test_keeps_document_order() -> None:
    """Object members keep document order, not sorted order."""
    context = Context.from_json('{ "z": 1, "a": { "y": [true], "b": null } }')
    assert [key for key, _ in context.members] == ["z", "a"]
    inner = context.members[1][1]
    assert isinstance(inner, ObjectData)
    assert inner.members == [("y", [True]), ("b", None)]


def test_rejects_duplicate_keys() -> None:
    """A duplicate key would silently drop one value, so it is an error."""
    assert read_error('{ "a": { "k": 1, "k": 2 } }') == "context: duplicate key 'k'"


def test_requires_an_object_at_the_top_level() -> None:
    """The top level is the context record, so it must be an object; the text must
    be JSON."""
    assert read_error("[1]") == "context: the context must be an object, got a list"
    assert read_error('{ "a": ').startswith("context: invalid JSON: ")


def test_rejects_integers_beyond_64_signed_bits() -> None:
    """Integers are signed 64-bit, like `int` attributes."""
    assert read_error('{ "a": 9223372036854775808 }') == (
        "context: integer 9223372036854775808 does not fit 64 signed bits"
    )
    context = Context.from_json('{ "a": -9223372036854775808 }')
    assert context.members[0][1] == -(2**63)


# The declaration every validation test checks data against.
LOCATIONS = """context {
    type location_info { order_interfaces: string[] port_base: int }
    locations: location_info[string]
    flags: bool[string]
    region: string
}"""


def validate_error(text: str) -> str:
    """Message of the `ContextError` compiling `LOCATIONS` against `text` raises."""
    with pytest.raises(ContextError) as excinfo:
        compile_topology(LOCATIONS, Context.from_json(text))
    return str(excinfo.value)


def test_accepts_conforming_data() -> None:
    """Valid data compiles."""
    compile_topology(
        LOCATIONS,
        Context.from_json(
            """{ "region": "emea", "flags": { "b": true },
                 "locations": { "NY": { "port_base": 9200, "order_interfaces": ["A"] } } }"""
        ),
    )


def test_rejects_undeclared_and_missing_fields() -> None:
    """An undeclared field is reported before the missing one it is most likely
    a typo of, at every level."""
    assert validate_error(
        """{ "region": "x", "flags": {}, "locations": {
             "NY": { "order_interface": [], "port_base": 1 } } }"""
    ) == ("context: locations.NY: 'location_info' has no field 'order_interface'")
    assert validate_error(
        '{ "region": "x", "flags": {}, "locations": { "NY": { "order_interfaces": [] } } }'
    ) == ("context: locations.NY: missing field 'port_base' of 'location_info'")
    assert validate_error('{ "region": "x", "flags": {}, "locations": {}, "extra": 1 }') == (
        "context: the context has no field 'extra'"
    )
    assert validate_error('{ "flags": {}, "locations": {} }') == (
        "context: missing field 'region' of the context"
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            '{ "LN": { "order_interfaces": ["A", 3], "port_base": 1 } }',
            "context: locations.LN.order_interfaces[1]: expected 'string', got 3",
        ),
        (
            '{ "LN": { "order_interfaces": [], "port_base": 1.5 } }',
            "context: locations.LN.port_base: expected 'int', got 1.5",
        ),
        ('{ "LN": ["A"] }', "context: locations.LN: expected 'location_info', got a list"),
        ('["LN"]', "context: locations: expected 'location_info[string]', got a list"),
    ],
)
def test_rejects_values_of_the_wrong_type(data: str, message: str) -> None:
    """Every value must have its declared type; the error names the path to it,
    with list indices and map keys."""
    assert validate_error(f'{{ "region": "x", "flags": {{}}, "locations": {data} }}') == message


def test_names_scalars_and_odd_keys_in_paths() -> None:
    """A null, a bool given as a string, and a key that is not a plain segment
    are reported by path too."""
    assert validate_error('{ "region": null, "flags": {}, "locations": {} }') == (
        "context: region: expected 'string', got null"
    )
    assert validate_error('{ "region": "x", "flags": { "a b": "yes" }, "locations": {} }') == (
        "context: flags[\"a b\"]: expected 'bool', got 'yes'"
    )


def declaration_error(declaration: str) -> str:
    """Message of the `CompileError` that compiling `declaration` against an
    empty context raises."""
    with pytest.raises(CompileError) as excinfo:
        compile_topology(declaration, Context.from_json("{}"))
    return str(excinfo.value)


def test_rejects_unknown_types() -> None:
    """V20: record types must be declared in the context block."""
    assert declaration_error("context { a: info[string] }") == (
        "unknown type 'info' (expected 'string', 'int', 'bool' or a type declared in the "
        "context block) at line 1, column 14"
    )


def test_rejects_lists_of_non_scalars() -> None:
    """V20: list items are scalars, since an iteration is named by its item."""
    assert "a list holds scalars ('string', 'int' or 'bool'), not 't'" in declaration_error(
        "context { type t { a: int } l: t[] }"
    )
    assert "a list holds scalars ('string', 'int' or 'bool'), not 'string[]'" in (
        declaration_error("context { l: string[][] }")
    )
    message = declaration_error("context { l: int[string][] }")
    assert "not 'int[string]'" in message
    assert message.endswith("at line 1, column 14")


def test_rejects_recursive_types() -> None:
    """V20: a record type containing itself, directly or through another record,
    could never be supplied as finite data; sharing a type is not recursion."""
    assert declaration_error("context { type node { children: node[string] } }") == (
        "type 'node' contains itself (node -> node) at line 1, column 16"
    )
    assert declaration_error(
        "context { type a { b: b } type b { c: c[string] } type c { a: a } x: int }"
    ) == ("type 'a' contains itself (a -> b -> c -> a) at line 1, column 16")
    compile_topology(
        "context { type leaf { x: int } type pair { l: leaf r: leaf } p: pair }",
        Context.from_json('{ "p": { "l": { "x": 1 }, "r": { "x": 2 } } }'),
    )
