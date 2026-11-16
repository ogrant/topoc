"""`{{...}}` placeholders, the DSL's one interpolation syntax. Loop variables are
substituted when templates are expanded; `{{Role.attr}}` when replication
edges fan out."""

from collections.abc import Callable

from topoc.model import AttrValue

# The text to splice in for one placeholder, given its expression with
# surrounding whitespace trimmed (`{{ a.b }}` -> `a.b`), or `None` to keep the
# placeholder verbatim.
type Replacer = Callable[[str], str | None]

_WHITESPACE = " \t\n\r\v\f"


def substitute(text: str, replace: Replacer) -> str | None:
    """`text` with each `{{...}}` placeholder passed to `replace`, left to right.
    Returns `None` if a `{{` is never closed."""
    out = ""
    rest = text
    while (opening := rest.find("{{")) >= 0:
        out += rest[:opening]
        rest = rest[opening:]
        closing = rest.find("}}")
        if closing < 0:
            return None
        placeholder = rest[: closing + 2]
        replacement = replace(placeholder[2:-2].strip(_WHITESPACE))
        out += placeholder if replacement is None else replacement
        rest = rest[len(placeholder) :]
    return out + rest


def to_text(value: AttrValue) -> str:
    """The text an attribute value interpolates as: strings verbatim, integers in
    decimal, booleans as `true` / `false`."""
    # `bool` is a subclass of `int`, so it must be tested first.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
