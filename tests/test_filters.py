"""Unit tests for the AMPS filter parser and the field-name-to-wire-name
rewrite
(`references`, `rewrite`), including the routed envelope's `$route`.

Methodology: rewrites run against a stub `PathResolver` (`resolve` below) that
renames `symbol` to `s` and rejects `unknown`, so a test can see exactly which
spans were rewritten and that everything else (operators, literals, spacing)
comes back character for character. Failures are classified by
`FilterError.kind` rather than by message, since the wording of syntax errors
is not part of the contract.
"""

import pytest

from topoc.filters import FilterError, FilterErrorKind, references, rewrite, rewrite_to_names


def resolve(path: str) -> str | None:
    """Renames `symbol` -> `s` and rejects `unknown`, so wire != source and the
    effect of a rewrite is visible."""
    segments = path[1:].split("/")
    if "unknown" in segments:
        return None
    return "/" + "/".join("s" if s == "symbol" else s for s in segments)


def error_kind(filter_text: str) -> FilterErrorKind:
    """How rewriting `filter_text` fails; fails the test if it does not."""
    with pytest.raises(FilterError) as excinfo:
        rewrite(filter_text, resolve, routed=False)
    return excinfo.value.kind


@pytest.mark.parametrize(
    ("source", "wire"),
    [
        ("/symbol = 5", "/s = 5"),
        ("/a > 1 AND /symbol < 2", "/a > 1 AND /s < 2"),
        ("(/x > 5) OR (/y = 3)", "(/x > 5) OR (/y = 3)"),
        ("NOT /symbol >= -1.5", "NOT /s >= -1.5"),
        ("5 <> /symbol", "5 <> /s"),
        ("/flag = true OR /a != false", "/flag = true OR /a != false"),
    ],
)
def test_resolves_field_paths_and_leaves_the_rest(source: str, wire: str) -> None:
    """Only field paths change: logical operators, parentheses, NOT, negative
    decimals, a field on the right-hand side, and boolean literals are all
    accepted and reproduced verbatim."""
    assert rewrite(source, resolve, routed=False) == wire


@pytest.mark.parametrize(
    ("source", "wire"),
    [
        ("/symbol = '/api/v1'", "/s = '/api/v1'"),
        ("/owner LIKE 'LN/.*'", "/owner LIKE 'LN/.*'"),
        ("/symbol NOT LIKE 'it''s /x'", "/s NOT LIKE 'it''s /x'"),
    ],
)
def test_does_not_rewrite_paths_inside_string_literals(source: str, wire: str) -> None:
    """Path-like text inside a quoted literal is data, not a field, so it is never
    rewritten, even after an escaped `''` quote."""
    assert rewrite(source, resolve, routed=False) == wire


@pytest.mark.parametrize(
    ("source", "wire"),
    [
        ("/symbol = 5", "/msg/s = 5"),
        ("/book/venue = 1", "/msg/book/venue = 1"),
        ("/symbol = '/api/v1'", "/msg/s = '/api/v1'"),
    ],
)
def test_routed_prefixes_msg_after_resolution(source: str, wire: str) -> None:
    """For a routed topic every resolved field gets the `/msg` envelope prefix;
    literals remain untouched."""
    assert rewrite(source, resolve, routed=True) == wire


def test_collects_field_references_in_source_order() -> None:
    """`references` reports each field in source order with its exact offset and
    length (the spans the rewriter splices), including inside parentheses and on
    the right of a comparison."""
    refs = references("/a/b = 1 AND (/c LIKE 'x' OR 2 < /d)").fields
    assert [(r.path, r.offset, r.length) for r in refs] == [
        ("/a/b", 0, 4),
        ("/c", 14, 2),
        ("/d", 33, 2),
    ]


def test_unknown_field_is_an_error() -> None:
    """A field the resolver does not know is an `UNKNOWN_FIELD` error naming the
    path, never a silent passthrough."""
    with pytest.raises(FilterError) as excinfo:
        rewrite("/unknown = 1", resolve, routed=False)
    assert excinfo.value.kind is FilterErrorKind.UNKNOWN_FIELD
    assert str(excinfo.value) == "filter references unknown field '/unknown'"


@pytest.mark.parametrize(
    "filter_text",
    [
        "",
        "/a > ",
        "/a",
        "/a = 1 AND",
        "(/a = 1",
        "/a = 'open",
        "/a/ = 1",
        "/a = 007",
        "/a LIKE 5",
        "/a = 1 garbage",
        "and /a = 1",
    ],
)
def test_malformed_filters_are_syntax_errors(filter_text: str) -> None:
    """A table of malformed filters, each breaking one grammar rule (empty input,
    missing operand, bare field, dangling AND, unclosed parenthesis or quote,
    trailing slash, leading-zero number, non-string LIKE pattern, trailing junk,
    lowercase keyword); each must be a `SYNTAX` error."""
    assert error_kind(filter_text) is FilterErrorKind.SYNTAX


@pytest.mark.parametrize(
    ("source", "wire"),
    [
        ("$route LIKE '^LN/'", "/route LIKE '^LN/'"),
        ("$route = 'LN/A' AND /symbol > 5", "/route = 'LN/A' AND /msg/s > 5"),
        (
            "NOT $route NOT LIKE '^LN/' OR 'x' = $route",
            "NOT /route NOT LIKE '^LN/' OR 'x' = /route",
        ),
    ],
)
def test_route_names_the_envelope_route_on_routed_topics(source: str, wire: str) -> None:
    """On a routed topic, `$route` becomes the envelope's `/route` (not
    `/msg`-prefixed), in `LIKE` and on either side of a comparison; message
    fields around it are still resolved and prefixed."""
    assert rewrite(source, resolve, routed=True) == wire


def test_route_is_an_error_on_unrouted_topics() -> None:
    """An unrouted topic has no envelope, so `$route` is a
    `ROUTE_ON_UNROUTED_TOPIC` error."""
    with pytest.raises(FilterError) as excinfo:
        rewrite("$route LIKE '^LN/'", resolve, routed=False)
    assert excinfo.value.kind is FilterErrorKind.ROUTE_ON_UNROUTED_TOPIC
    assert str(excinfo.value) == "'$route' is only valid on a routed topic"


def test_route_inside_a_string_literal_is_text() -> None:
    """Inside a quoted literal, `$route` is just text."""
    assert rewrite("/symbol = '$route'", resolve, routed=False) == "/s = '$route'"


def test_route_is_not_a_message_field() -> None:
    """`$route` is collected apart from message fields (which feed field-name
    resolution), and only `route` may follow the `$`."""
    refs = references("$route = 'a' AND /symbol = 1")
    assert [f.path for f in refs.fields] == ["/symbol"]
    assert [(r.offset, r.length) for r in refs.routes] == [(0, 6)]
    assert error_kind("$other = 1") is FilterErrorKind.SYNTAX


def wire_to_name(wire: str) -> str | None:
    """The inverse of `resolve`: `s` -> `symbol`, and `unknown` names no field."""
    segments = wire[1:].split("/")
    if "unknown" in segments:
        return None
    return "/" + "/".join("symbol" if s == "s" else s for s in segments)


@pytest.mark.parametrize(
    ("wire", "names"),
    [
        ("/s = 'X'", "/symbol = 'X'"),
        ("/book/s > 1 AND NOT /s LIKE '/s'", "/book/symbol > 1 AND NOT /symbol LIKE '/s'"),
    ],
)
def test_rewrite_to_names_inverts_the_rewrite(wire: str, names: str) -> None:
    assert rewrite_to_names(wire, wire_to_name, routed=False) == names
    assert rewrite(names, resolve, routed=False) == wire


def test_rewrite_to_names_strips_the_envelope_on_routed_topics() -> None:
    wire = "/msg/s = 'X' AND /route = 'LN'"
    assert rewrite_to_names(wire, wire_to_name, routed=True) == "/symbol = 'X' AND $route = 'LN'"


@pytest.mark.parametrize("wire", ["/unknown = 1", "/s = 1"])
def test_rewrite_to_names_rejects_paths_naming_no_field(wire: str) -> None:
    """`/unknown` resolves to nothing; on a routed topic, so does a path outside
    the envelope's `/msg` and `/route`."""
    with pytest.raises(FilterError) as excinfo:
        rewrite_to_names(wire, wire_to_name, routed=wire == "/s = 1")
    assert excinfo.value.kind == FilterErrorKind.UNKNOWN_FIELD
