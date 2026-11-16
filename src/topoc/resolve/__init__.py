"""Resolution: from syntax tree to the resolved cluster model, expanding `for`
templates on the way."""

from topoc.resolve.resolver import compile_topology, resolve

__all__ = ["compile_topology", "resolve"]
