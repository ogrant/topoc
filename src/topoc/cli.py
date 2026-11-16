"""topoc: compile a `.topo` topology into AMPS XML configuration.

    topoc check    --topology <file.topo> [--context <context.json>]
    topoc generate --topology <file.topo> [--context <context.json>]
                   --catalog <catalog.json> --output-dir <dir>
    topoc view     --cluster <name> [--catalog <catalog.json>] --output-dir <dir>
                   <config.xml>...
    topoc lsp      (a language server for editors, over stdio)

Every option is required, except `--context`, which is required exactly when the
topology declares a `context` block, and `view`'s `--catalog`, which gives SOW
keys and filters their field names. This front end owns argument parsing and
file I/O; everything else lives in the library modules.
"""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from topoc.amps.generator import generate
from topoc.catalog import Catalog
from topoc.catalog_json import parse_catalog_json
from topoc.context import Context
from topoc.errors import CatalogError, CompileError, ContextError, TopocError
from topoc.model import ResolvedSystem
from topoc.resolve import compile_topology
from topoc.view.cluster import read_cluster
from topoc.view.field_names import with_field_names
from topoc.view.page import render_page

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2  # argparse's own exit status for usage errors


class InputError(TopocError):
    """A failure attributed to an input file: reported as "<file>: <message>"."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    if args.command == "lsp":
        # Imported here: the server's dependencies are only needed by it.
        from topoc.lsp.server import serve

        serve()
        return EXIT_OK
    try:
        if args.command == "view":
            catalog = None if args.catalog is None else Path(args.catalog)
            configs = [Path(f) for f in args.configs]
            return _run_view(args.cluster, catalog, Path(args.output_dir), configs)
        context = None if args.context is None else Path(args.context)
        if args.command == "check":
            return _run_check(Path(args.topology), context)
        return _run_generate(
            Path(args.topology), context, Path(args.catalog), Path(args.output_dir)
        )
    except TopocError as error:
        print(f"topoc: {error}", file=sys.stderr)
        return EXIT_FAILURE


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topoc",
        description="Compile a .topo topology into AMPS XML configuration.",
        epilog="--context supplies the generation context, required exactly when the topology "
        "declares a 'context' block.",
    )
    parser.add_argument("--version", action="version", version=f"topoc {version('topoc')}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("lsp", help="serve the .topo language server over stdio, for editors")

    check = commands.add_parser("check", help="parse and validate a topology")
    check.add_argument("--topology", required=True, help="topology file")
    check.add_argument("--context", help="generation context JSON file")

    generate_cmd = commands.add_parser(
        "generate", help="write one AMPS configuration <Instance>.xml per instance"
    )
    generate_cmd.add_argument("--topology", required=True, help="topology file")
    generate_cmd.add_argument("--context", help="generation context JSON file")
    generate_cmd.add_argument("--catalog", required=True, help="topic catalog JSON file")
    generate_cmd.add_argument("--output-dir", required=True, help="directory for the XML files")

    view = commands.add_parser(
        "view",
        help="write a standalone HTML page showing a cluster, from its AMPS XML configurations",
    )
    view.add_argument(
        "--cluster", required=True, type=_cluster_name, help="the cluster's name: the page's title"
    )
    view.add_argument(
        "--catalog",
        help="the topic catalog the configurations were generated from: shows SOW keys and "
        "filters with field names rather than wire names",
    )
    view.add_argument(
        "--output-dir", required=True, help="directory for the page, written as <name>.html"
    )
    view.add_argument("configs", nargs="+", metavar="config.xml", help="the instances' AMPS XML")
    return parser


def _cluster_name(text: str) -> str:
    """A cluster name, which also names the page's file."""
    if text in ("", ".", "..") or any(c in "/\\" or ord(c) < 32 for c in text):
        raise argparse.ArgumentTypeError(
            f"invalid cluster name '{text}': it names the page's file, so it cannot be empty, "
            "'.' or '..', or contain '/', '\\' or control characters"
        )
    return text


def _run_check(topology: Path, context: Path | None) -> int:
    system = _load_topology(topology, context)
    print(f"{topology}: ok ({len(system.instances)} instances, {len(system.topics)} topics)")
    return EXIT_OK


def _run_generate(
    topology: Path, context: Path | None, catalog_path: Path, output_dir: Path
) -> int:
    files = generate(_load_topology(topology, context), _load_catalog(catalog_path))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise InputError(output_dir, f"cannot create directory ({error.strerror})") from error
    for file in files:
        path = output_dir / file.name
        try:
            path.write_text(file.content, encoding="utf-8", newline="")
        except OSError as error:
            raise InputError(path, f"cannot write file ({error.strerror})") from error
        print(f"wrote {path}")
    return EXIT_OK


def _run_view(name: str, catalog: Path | None, output_dir: Path, configs: list[Path]) -> int:
    cluster = read_cluster(name, configs)
    if catalog is not None:
        cluster = with_field_names(cluster, _load_catalog(catalog))
    page = render_page(cluster)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise InputError(output_dir, f"cannot create directory ({error.strerror})") from error
    path = output_dir / f"{name}.html"
    try:
        path.write_text(page, encoding="utf-8", newline="")
    except OSError as error:
        raise InputError(path, f"cannot write file ({error.strerror})") from error
    print(f"wrote {path}")
    return EXIT_OK


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(path, f"cannot open file ({error.strerror})") from error
    except UnicodeDecodeError as error:
        raise InputError(path, "file is not valid UTF-8") from error


def _load_context(path: Path) -> Context:
    try:
        return Context.from_json(_read_text(path))
    except ContextError as error:
        raise InputError(path, str(error)) from error


def _load_topology(path: Path, context_path: Path | None) -> ResolvedSystem:
    """Compile the topology against its context. Errors in the context data are
    reported against the context file, everything else against the topology."""
    context = None if context_path is None else _load_context(context_path)
    try:
        return compile_topology(_read_text(path), context)
    except ContextError as error:
        raise InputError(context_path or path, str(error)) from error
    except CompileError as error:
        raise InputError(path, str(error)) from error


def _load_catalog(path: Path) -> Catalog:
    try:
        return parse_catalog_json(_read_text(path))
    except CatalogError as error:
        raise InputError(path, str(error)) from error


if __name__ == "__main__":
    sys.exit(main())
