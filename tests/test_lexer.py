"""Unit tests for `tokenize`, the `.topo` lexer.

Methodology: each test feeds a short source string straight to the lexer, with
no parser involved, and checks either the exact token values produced (ignoring
locations unless locations are the point of the test) or, for invalid input,
the exact `CompileError` message, which pins down both the wording and the
reported line/column.
"""

import pytest

from topoc.dsl.lexer import tokenize
from topoc.dsl.tokens import (
    ContextPathToken,
    IdentToken,
    IntToken,
    PlaceholderToken,
    Punct,
    StringToken,
    TokenValue,
)
from topoc.errors import CompileError, SourceLocation


def values(source: str) -> list[TokenValue]:
    """The token values of `source`, without their locations."""
    return [token.value for token in tokenize(source)]


def lex_error(source: str) -> str:
    """Message of the `CompileError` that lexing `source` must raise."""
    with pytest.raises(CompileError) as excinfo:
        tokenize(source)
    return str(excinfo.value)


def test_lexes_punctuation_and_arrow() -> None:
    """Every punctuation token, including the multi-character `::`, `->` and
    `<->`, lexes to its own `Punct`, in order; the whitespace between them
    produces nothing."""
    assert values("{ } [ ] , : :: = -> <->") == [
        Punct.LBRACE,
        Punct.RBRACE,
        Punct.LBRACKET,
        Punct.RBRACKET,
        Punct.COMMA,
        Punct.COLON,
        Punct.COLON_COLON,
        Punct.EQ,
        Punct.ARROW,
        Punct.TWO_WAY_ARROW,
    ]


def test_lexes_dotted_identifiers() -> None:
    """Dots join name segments into one identifier token (`Quoting.LN` is one
    name, not three tokens); leading underscores and trailing digits are valid."""
    assert values("Quoting.LN Quoting.Fast.key plain _under score9") == [
        IdentToken("Quoting.LN"),
        IdentToken("Quoting.Fast.key"),
        IdentToken("plain"),
        IdentToken("_under"),
        IdentToken("score9"),
    ]


@pytest.mark.parametrize("source", ["Quoting.", "A..b", "A.1b"])
def test_rejects_trailing_or_consecutive_dots(source: str) -> None:
    """A dot must be followed by a segment start, so a trailing dot, a double dot,
    and a segment starting with a digit are all invalid identifiers."""
    assert "invalid identifier" in lex_error(source)


def test_lexes_integers_including_negative() -> None:
    """Integer literals, with a leading `-` folded into the literal (there is no
    unary minus token)."""
    assert values("9090 -42") == [IntToken(9090), IntToken(-42)]


def test_rejects_out_of_range_integers() -> None:
    """A literal beyond the int64 range is an error reported at its first
    character, not an arbitrarily large value."""
    assert lex_error("99999999999999999999") == ("integer literal out of range at line 1, column 1")


def test_dash_without_arrow_or_digit_is_an_error() -> None:
    """`-` is only valid in `->` or a negative integer; a lone dash is an
    unexpected character, reported at its own column."""
    assert lex_error("a - b") == "unexpected character '-' at line 1, column 3"


def test_lexes_strings_with_escapes() -> None:
    """String contents are kept verbatim, AMPS filter syntax included, except the
    two supported escapes `\\"` and `\\\\`, which are unescaped."""
    assert values(r""""/owner LIKE 'LN/.*'" "a\"b" "c\\d" """) == [
        StringToken("/owner LIKE 'LN/.*'"),
        StringToken('a"b'),
        StringToken("c\\d"),
    ]


def test_rejects_unterminated_string_and_bad_escape() -> None:
    """An unterminated string is reported at its opening quote; an unsupported
    escape (`\\n`) is reported at the escaped character."""
    assert lex_error('"abc') == "unterminated string literal starting at line 1, column 1"
    assert lex_error(r'"a\n"') == "invalid escape sequence '\\n' at line 1, column 4"


def test_skips_comments_and_tracks_locations() -> None:
    """`//` comments run to end of line and produce no tokens, and token
    locations are 1-based line/column positions that account for the skipped
    lines."""
    tokens = tokenize("// header\nrole Infra // trailing\n{")
    assert [token.location for token in tokens] == [
        SourceLocation(2, 1),
        SourceLocation(2, 6),
        SourceLocation(3, 1),
    ]


def test_columns_count_code_points_not_bytes() -> None:
    """Columns count Unicode code points: a two-byte UTF-8 character advances the
    column by one, and is kept intact in the token value."""
    tokens = tokenize('"é" x')
    assert tokens[0].value == StringToken("é")
    assert tokens[1].location == SourceLocation(1, 5)


def test_rejects_unexpected_characters() -> None:
    """Characters outside the language are rejected with their location; a
    multi-byte character is quoted whole in the message."""
    assert lex_error("role @ x") == "unexpected character '@' at line 1, column 6"
    assert lex_error("role #x") == "unexpected character '#' at line 1, column 6"


def test_lexes_context_paths_without_their_sigil() -> None:
    """`@` directly followed by a dotted identifier is one context path token."""
    assert values("in @locations @site.workers") == [
        IdentToken("in"),
        ContextPathToken("locations"),
        ContextPathToken("site.workers"),
    ]
    assert lex_error("role é") == "unexpected character 'é' at line 1, column 6"


def test_lexes_bare_placeholders() -> None:
    """A bare `{{expr}}` outside a string is one placeholder token holding the
    trimmed expression; two closing braces are still two tokens."""
    assert values("attr R.port {{ loc.port }} { }") == [
        IdentToken("attr"),
        IdentToken("R.port"),
        PlaceholderToken("loc.port"),
        Punct.LBRACE,
        Punct.RBRACE,
    ]
    assert values("}}") == [Punct.RBRACE, Punct.RBRACE]


def test_rejects_unterminated_or_empty_placeholders() -> None:
    """A placeholder closes on its own line and holds an expression."""
    assert lex_error("{{a") == (
        "unterminated placeholder (expected '}}' on the same line) at line 1, column 1"
    )
    assert "unterminated placeholder" in lex_error("{{a\n}}")
    assert lex_error("{{ }}") == "empty placeholder '{{}}' at line 1, column 1"


def test_lexes_durations_with_their_unit() -> None:
    """An integer directly followed by a unit is a duration."""
    from topoc.dsl.tokens import DurationToken
    from topoc.queue import Duration, DurationUnit

    assert values("500ms 60s 5m 2h 1d 7") == [
        DurationToken(Duration(500, DurationUnit.MILLISECONDS)),
        DurationToken(Duration(60, DurationUnit.SECONDS)),
        DurationToken(Duration(5, DurationUnit.MINUTES)),
        DurationToken(Duration(2, DurationUnit.HOURS)),
        DurationToken(Duration(1, DurationUnit.DAYS)),
        IntToken(7),
    ]


@pytest.mark.parametrize("source", ["5w", "10sec", "-5s"])
def test_rejects_unknown_units_and_negative_durations(source: str) -> None:
    assert "invalid duration" in lex_error(source)
