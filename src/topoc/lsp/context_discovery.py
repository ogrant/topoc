"""Finding the generation context for a `.topo` open in an editor, which the
compiler needs to check a topology that declares a `context` block.

In order, the first that exists:

1. the file configured for this topology (the client's `contextFiles`);
2. `<name>.context.json` beside `<name>.topo`, topoc's layout;
3. the `context:` section of an `env.yaml` beside it, skynet's layout.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from topoc.context import Context
from topoc.errors import ContextError


@dataclass(frozen=True, slots=True)
class ContextFound:
    path: Path
    context: Context


@dataclass(frozen=True, slots=True)
class ContextUnreadable:
    """A context file that exists but cannot be read as context data."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ContextNotFound:
    """No context data: the paths looked at, in order (none for a document not
    saved to disk)."""

    searched: list[Path]


type ContextLookup = ContextFound | ContextUnreadable | ContextNotFound


def find_context(topology: Path | None, configured: Mapping[Path, Path]) -> ContextLookup:
    """The context data for the `.topo` at `topology` (`None` when it is not
    on disk). `configured` maps absolute topology paths to their context
    files."""
    if topology is None:
        return ContextNotFound([])
    searched: list[Path] = []
    if (path := configured.get(topology)) is not None:
        if path.is_file():
            return _read_json(path)
        searched.append(path)
    beside = topology.with_name(f"{topology.stem}.context.json")
    if beside.is_file():
        return _read_json(beside)
    searched.append(beside)
    env = topology.with_name("env.yaml")
    if env.is_file():
        found = _read_env(env)
        if found is not None:
            return found
    searched.append(env)
    return ContextNotFound(searched)


def _read_json(path: Path) -> ContextLookup:
    try:
        return ContextFound(path, Context.from_json(path.read_text(encoding="utf-8")))
    except (OSError, ContextError) as error:
        return ContextUnreadable(path, str(error))


def _read_env(path: Path) -> ContextLookup | None:
    """The `context:` section of an `env.yaml`, or `None` if it has none."""
    try:
        env = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return ContextUnreadable(path, str(error))
    if not isinstance(env, dict) or "context" not in env:
        return None
    try:
        return ContextFound(path, Context.from_json(json.dumps(env["context"])))
    except (TypeError, ValueError, ContextError) as error:
        return ContextUnreadable(path, f"its 'context' section: {error}")
