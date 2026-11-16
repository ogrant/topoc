"""Extents of tokens and blocks in `.topo` source, which the compiler locates by
their first character only.

Positions here are 0-based `(line, column)` pairs, columns in code points, the
server's own units; `server` converts them to the client's.
"""

import re
from dataclasses import dataclass

from topoc.errors import SourceLocation


@dataclass(frozen=True, order=True, slots=True)
class Position:
    """0-based line and column (code points)."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Span:
    start: Position
    end: Position


# One token, matched at its first character: a string (to its closing quote,
# or the end of the line), a placeholder, a context path, an arrow, or a word
# (identifier, number, duration, `::` path).
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"?|\{\{.*?\}\}|@[\w.]+|<->|->|[\w.]+(?:::[\w.]+)*')


def start_of(location: SourceLocation) -> Position:
    return Position(location.line - 1, location.column - 1)


def end_of_text(lines: list[str]) -> Position:
    return Position(max(len(lines) - 1, 0), len(lines[-1]) if lines else 0)


def token_span(lines: list[str], location: SourceLocation) -> Span:
    """The token starting at `location`, or its first character if none does."""
    start = start_of(location)
    if start.line >= len(lines):
        end = end_of_text(lines)
        return Span(end, end)
    line = lines[start.line]
    match = _TOKEN.match(line, start.column)
    length = len(match.group()) if match else 1
    return Span(start, Position(start.line, min(start.column + length, len(line) + 1)))


def block_span(lines: list[str], location: SourceLocation) -> Span:
    """From `location` through the end of the first `{ ... }` or `[ ... ]`
    after it, its nesting matched past comments, strings and placeholders; the
    token at `location` if no block follows."""
    start = start_of(location)
    depth = 0
    for row in range(start.line, len(lines)):
        line = lines[row]
        column = start.column if row == start.line else 0
        while column < len(line):
            char = line[column]
            if line.startswith("//", column):
                break
            if char == '"':
                closing = re.compile(r'"(?:[^"\\]|\\.)*"?').match(line, column)
                column += len(closing.group()) if closing else 1
                continue
            if line.startswith("{{", column):
                close = line.find("}}", column + 2)
                column = len(line) if close < 0 else close + 2
                continue
            if char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    return Span(start, Position(row, column + 1))
            column += 1
    return token_span(lines, location)


def embedded_regions(lines: list[str]) -> list[list[tuple[int, int]]]:
    """Per line, the `[start, end)` columns of each string literal and bare
    `{{...}}` placeholder, outside comments."""
    regions: list[list[tuple[int, int]]] = []
    for line in lines:
        found: list[tuple[int, int]] = []
        column = 0
        while column < len(line):
            if line.startswith("//", column):
                break
            if line[column] == '"':
                closing = re.compile(r'"(?:[^"\\]|\\.)*"?').match(line, column)
                end = column + (len(closing.group()) if closing else 1)
                found.append((column, end))
                column = end
                continue
            if line.startswith("{{", column):
                close = line.find("}}", column + 2)
                end = len(line) if close < 0 else close + 2
                found.append((column, end))
                column = end
                continue
            column += 1
        regions.append(found)
    return regions
