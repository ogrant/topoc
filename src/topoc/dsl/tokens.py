"""Tokens produced by the lexer."""

from dataclasses import dataclass
from enum import Enum

from topoc.errors import SourceLocation
from topoc.queue import Duration


@dataclass(frozen=True, slots=True)
class IdentToken:
    """A dotted identifier or a contextual keyword."""

    text: str


@dataclass(frozen=True, slots=True)
class ContextPathToken:
    """`@path`: a path from a context field, without the `@` (`@locations`,
    `@site.workers`)."""

    path: str


@dataclass(frozen=True, slots=True)
class StringToken:
    """A double-quoted string, with escapes already processed."""

    value: str


@dataclass(frozen=True, slots=True)
class IntToken:
    value: int


@dataclass(frozen=True, slots=True)
class DurationToken:
    """An AMPS interval: `60s`, `500ms`, `1d`."""

    value: Duration


@dataclass(frozen=True, slots=True)
class PlaceholderToken:
    """`{{expr}}` outside a string: the trimmed expression."""

    expr: str


class Punct(Enum):
    """Punctuation, valued by its spelling."""

    LBRACE = "{"
    RBRACE = "}"
    LBRACKET = "["
    RBRACKET = "]"
    LPAREN = "("  # a topology block's parameters, a `use`'s arguments
    RPAREN = ")"
    COMMA = ","
    COLON = ":"
    COLON_COLON = "::"  # path separator in a definition path
    EQ = "="
    ARROW = "->"
    TWO_WAY_ARROW = "<->"  # two-way replication


type TokenValue = (
    IdentToken
    | ContextPathToken
    | StringToken
    | IntToken
    | DurationToken
    | PlaceholderToken
    | Punct
)


@dataclass(frozen=True, slots=True)
class Token:
    value: TokenValue
    location: SourceLocation


def describe(value: TokenValue) -> str:
    """Human-readable rendering for error messages, e.g. `'{'` or `a string literal`."""
    match value:
        case IdentToken(text):
            return f"'{text}'"
        case ContextPathToken(path):
            return f"'@{path}'"
        case StringToken():
            return "a string literal"
        case IntToken(integer):
            return f"'{integer}'"
        case DurationToken(duration):
            return f"'{duration}'"
        case PlaceholderToken(expr):
            return f"'{{{{{expr}}}}}'"
        case Punct():
            return f"'{value.value}'"
