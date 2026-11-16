"""Splits `.topo` source into tokens, dropping whitespace and `//` comments."""

from typing import NoReturn

from topoc.dsl.tokens import (
    ContextPathToken,
    DurationToken,
    IdentToken,
    IntToken,
    PlaceholderToken,
    Punct,
    StringToken,
    Token,
    TokenValue,
)
from topoc.errors import CompileError, SourceLocation
from topoc.queue import Duration, DurationUnit

_WHITESPACE = frozenset(" \t\n\r\v\f")
_DIGITS = frozenset("0123456789")
_SINGLE_PUNCT = {
    "{": Punct.LBRACE,
    "}": Punct.RBRACE,
    "[": Punct.LBRACKET,
    "]": Punct.RBRACKET,
    "(": Punct.LPAREN,
    ")": Punct.RPAREN,
    ",": Punct.COMMA,
    "=": Punct.EQ,
}
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _is_digit(c: str | None) -> bool:
    return c is not None and c in _DIGITS


def _is_ident_start(c: str | None) -> bool:
    return c is not None and c.isascii() and (c.isalpha() or c == "_")


def _is_ident_continue(c: str | None) -> bool:
    return c is not None and c.isascii() and (c.isalnum() or c == "_")


class _Cursor:
    """Walks the source one code point at a time, tracking 1-based line/column."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0
        self._line = 1
        self._column = 1

    def peek(self, ahead: int = 0) -> str | None:
        index = self._pos + ahead
        return self._source[index] if index < len(self._source) else None

    def bump(self) -> str:
        """Consume and return one code point. Precondition: not at end of input."""
        c = self._source[self._pos]
        self._pos += 1
        if c == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return c

    @property
    def location(self) -> SourceLocation:
        return SourceLocation(self._line, self._column)


def tokenize(source: str) -> list[Token]:
    """Tokenize `.topo` source.

    Raises `CompileError` on an unexpected character, an unterminated string,
    an invalid escape, a malformed dotted identifier or an out-of-range integer.
    """
    cursor = _Cursor(source)
    tokens: list[Token] = []
    while (c := cursor.peek()) is not None:
        if c in _WHITESPACE:
            cursor.bump()
        elif c == "/" and cursor.peek(1) == "/":
            while cursor.peek() not in (None, "\n"):
                cursor.bump()
        else:
            at = cursor.location
            tokens.append(Token(_lex_token(cursor, c, at), at))
    return tokens


def _lex_token(cursor: _Cursor, c: str, at: SourceLocation) -> TokenValue:
    if c == "{" and cursor.peek(1) == "{":
        return PlaceholderToken(_lex_placeholder(cursor, at))
    if (punct := _SINGLE_PUNCT.get(c)) is not None:
        cursor.bump()
        return punct
    if c == ":":
        cursor.bump()
        if cursor.peek() == ":":
            cursor.bump()
            return Punct.COLON_COLON
        return Punct.COLON
    if c == '"':
        return StringToken(_lex_string(cursor, at))
    if c == "-":
        cursor.bump()
        if cursor.peek() == ">":
            cursor.bump()
            return Punct.ARROW
        if _is_digit(cursor.peek()):
            value = _lex_int(cursor, "-", at)
            if _is_letter(cursor.peek()):
                raise _invalid_duration(f"{value}{_lex_unit(cursor)}", at)
            return IntToken(value)
        raise _unexpected_char("-", at)
    if c == "<":
        cursor.bump()
        if cursor.peek() == "-" and cursor.peek(1) == ">":
            cursor.bump()
            cursor.bump()
            return Punct.TWO_WAY_ARROW
        raise _unexpected_char("<", at)
    if _is_digit(c):
        value = _lex_int(cursor, "", at)
        if _is_letter(cursor.peek()):
            suffix = _lex_unit(cursor)
            unit = DurationUnit.from_suffix(suffix)
            if unit is None:
                raise _invalid_duration(f"{value}{suffix}", at)
            return DurationToken(Duration(value, unit))
        return IntToken(value)
    if _is_ident_start(c):
        return IdentToken(_lex_ident(cursor))
    if c == "@" and _is_ident_start(cursor.peek(1)):
        cursor.bump()
        return ContextPathToken(_lex_ident(cursor))
    raise _unexpected_char(c, at)


def _lex_placeholder(cursor: _Cursor, start: SourceLocation) -> str:
    """`{{expr}}` on one line. No block opens with two braces, so `{{` outside a
    string always starts a placeholder."""
    cursor.bump()
    cursor.bump()
    expr = ""
    while not (cursor.peek() == "}" and cursor.peek(1) == "}"):
        if cursor.peek() in (None, "\n"):
            raise CompileError.at(
                "unterminated placeholder (expected '}}' on the same line)", start
            )
        expr += cursor.bump()
    cursor.bump()
    cursor.bump()
    expr = expr.strip(" \t")
    if not expr:
        raise CompileError.at("empty placeholder '{{}}'", start)
    return expr


def _unexpected_char(c: str, at: SourceLocation) -> CompileError:
    return CompileError.at(f"unexpected character '{c}'", at)


def _unterminated_string(start: SourceLocation) -> NoReturn:
    raise CompileError(
        f"unterminated string literal starting at line {start.line}, column {start.column}",
        start,
    )


def _lex_ident(cursor: _Cursor) -> str:
    """Dot-separated segments; the caller guarantees a segment start."""
    text = ""
    while True:
        text += cursor.bump()
        while _is_ident_continue(cursor.peek()):
            text += cursor.bump()
        if cursor.peek() != ".":
            return text
        if not _is_ident_start(cursor.peek(1)):
            at = cursor.location
            raise CompileError(
                f"invalid identifier at line {at.line}, column {at.column}: "
                f"'{text}.' — '.' must be followed by a name segment",
                at,
            )
        text += cursor.bump()


def _is_letter(c: str | None) -> bool:
    return c is not None and c.isascii() and c.isalpha()


def _lex_unit(cursor: _Cursor) -> str:
    """The unit letters right after a duration's amount."""
    unit = ""
    while _is_letter(cursor.peek()):
        unit += cursor.bump()
    return unit


def _invalid_duration(text: str, at: SourceLocation) -> CompileError:
    return CompileError(
        f"invalid duration '{text}' at line {at.line}, column {at.column} (expected a "
        "non-negative amount and a unit: 'ms', 's', 'm', 'h' or 'd')",
        at,
    )


def _lex_int(cursor: _Cursor, sign: str, start: SourceLocation) -> int:
    digits = sign
    while _is_digit(cursor.peek()):
        digits += cursor.bump()
    value = int(digits)
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise CompileError(
            f"integer literal out of range at line {start.line}, column {start.column}", start
        )
    return value


def _lex_string(cursor: _Cursor, start: SourceLocation) -> str:
    cursor.bump()  # opening quote
    value = ""
    while True:
        if cursor.peek() is None:
            _unterminated_string(start)
        c = cursor.bump()
        if c == '"':
            return value
        if c != "\\":
            value += c
            continue
        escape_at = cursor.location
        if cursor.peek() is None:
            _unterminated_string(start)
        escaped = cursor.bump()
        if escaped not in ('"', "\\"):
            raise CompileError(
                f"invalid escape sequence '\\{escaped}' at line {escape_at.line}, "
                f"column {escape_at.column}",
                escape_at,
            )
        value += escaped
