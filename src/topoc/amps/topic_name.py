"""The topic-name check: every `.topo` topic name must fit the name pattern of
the definition it references.

A definition's name pattern is its topic name, in which each `{}` is a hole
standing for one whole, non-empty `/`-separated segment; a pattern without
holes is a single fixed name. Segments are compared position by position:

- a literal segment of the pattern must appear verbatim in the topic name, and
  must be written literally in the `.topo`: interpolation is only allowed where
  the pattern has a hole;
- a hole accepts any non-empty segment, written literally or interpolated.

Values interpolated into topic names never contain `/` (V19), so the segments of
a template line up one-to-one with those of every name it expands to.
"""

from typing import Final

from topoc.catalog import CatalogEntry
from topoc.errors import GenerateError
from topoc.model import ResolvedTopic

# A hole in a name pattern: one whole segment.
_HOLE: Final = "{}"


def check_topic_name(topic: ResolvedTopic, entry: CatalogEntry) -> None:
    """Raise `GenerateError` if `topic` does not fit `entry`'s name pattern."""
    pattern = entry.name_pattern
    mismatch = GenerateError(
        f"topic {_describe(topic)} does not match '{pattern}', the name pattern of its "
        f"definition '{entry.definition}'"
    )
    expected = pattern.split("/")
    actual = topic.name.split("/")
    if len(actual) != len(expected):
        raise mismatch
    written = topic.name_template.split("/")
    for index, (constant, segment) in enumerate(zip(expected, actual, strict=True)):
        if constant == _HOLE:
            if not segment:
                raise mismatch
            continue
        if index < len(written) and "{{" in written[index]:
            raise GenerateError(
                f"topic '{topic.name_template}' interpolates '{written[index]}' where "
                f"'{pattern}', the name pattern of its definition '{entry.definition}', "
                f"expects the constant '{constant}'"
            )
        if segment != constant:
            raise mismatch


def _describe(topic: ResolvedTopic) -> str:
    """`'orders/LN'`, or `'orders/LN' (from 'orders/{{q.Quoting.key}}')` for an
    expanded topic."""
    if topic.name == topic.name_template:
        return f"'{topic.name}'"
    return f"'{topic.name}' (from '{topic.name_template}')"
