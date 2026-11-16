"""Which `for` iterations produced a generated topic or instance, for error
messages."""

from dataclasses import dataclass

from topoc.errors import CompileError, SourceLocation


@dataclass(frozen=True, slots=True)
class Origin:
    """The iterations that produced a definition: the innermost `for` keyword and
    the value of every enclosing loop variable, outermost first."""

    loop: SourceLocation
    bindings: tuple[tuple[str, str], ...]

    @staticmethod
    def within(outer: "Origin | None", loop: SourceLocation, variable: str, value: str) -> "Origin":
        """The origin of one iteration of the `for` at `loop`, which binds
        `variable` to `value`, within the `outer` iterations if any."""
        enclosing = () if outer is None else outer.bindings
        return Origin(loop, (*enclosing, (variable, value)))

    def bindings_text(self) -> str:
        """The bindings as an error shows them: `q = 'Q.NY', i = '0'`."""
        return ", ".join(f"{variable} = '{value}'" for variable, value in self.bindings)


def annotate(error: CompileError, origin: Origin, kind: str, name: str) -> CompileError:
    """`error` with a note naming the iterations that produced the `kind`
    (`topic` or `instance`) called `name`."""
    return CompileError(
        f"{error} (in {kind} '{name}', from the 'for' at line {origin.loop.line}, "
        f"column {origin.loop.column} with {origin.bindings_text()})",
        error.location,
    )


def annotated(error: CompileError, origin: Origin | None, kind: str, name: str) -> CompileError:
    """`error`, annotated with the iterations that generated the `kind` called
    `name` if a loop did."""
    return error if origin is None else annotate(error, origin, kind, name)
