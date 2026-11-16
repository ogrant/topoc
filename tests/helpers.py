"""Shared helpers for the tests."""

from pathlib import Path

from topoc.context import Context
from topoc.model import ResolvedInstance, ResolvedSystem

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def read_testdata(name: str) -> str:
    return (TESTDATA / name).read_text(encoding="utf-8")


def reference_context() -> Context:
    """testdata/reference.context.json, the reference example's generation context."""
    return Context.from_json(read_testdata("reference.context.json"))


def instance(system: ResolvedSystem, name: str) -> ResolvedInstance:
    """The resolved instance called `name`; raises `StopIteration` if absent."""
    return next(i for i in system.instances if i.name == name)
