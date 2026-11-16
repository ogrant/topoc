"""Tests for the command-line front end, `topoc.cli.main`.

Methodology: `main` is called in-process with an argument list (no subprocess),
reading the shared fixtures and writing into pytest's per-test `tmp_path`. Each
test asserts the exit status and the user-visible output captured by `capsys`:
the CLI's contract is its exit codes, its messages and the files it writes.
"""

from pathlib import Path

import pytest
from helpers import TESTDATA

from topoc.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main

TOPOLOGY = str(TESTDATA / "reference.topo")
CONTEXT = str(TESTDATA / "reference.context.json")
CATALOG = str(TESTDATA / "reference.catalog.json")


def generate_args(catalog: str, output_dir: Path) -> list[str]:
    """Arguments for `generate` on the reference topology and its context."""
    output = str(output_dir)
    return [
        "generate",
        "--topology",
        TOPOLOGY,
        "--context",
        CONTEXT,
        "--catalog",
        catalog,
        "--output-dir",
        output,
    ]


def test_check_reports_a_valid_topology(capsys: pytest.CaptureFixture[str]) -> None:
    """`check` on a valid topology exits 0 and summarises what it found."""
    assert main(["check", "--topology", TOPOLOGY, "--context", CONTEXT]) == EXIT_OK
    assert capsys.readouterr().out == f"{TOPOLOGY}: ok (5 instances, 10 topics)\n"


def test_generate_writes_one_file_per_instance(tmp_path: Path) -> None:
    """`generate` creates the output directory and writes every instance's
    configuration, matching the goldens byte for byte."""
    out = tmp_path / "nested" / "out"
    assert main(generate_args(CATALOG, out)) == EXIT_OK
    written = sorted(p.name for p in out.iterdir())
    assert written == ["Admin.xml", "Infra1.xml", "Infra2.xml", "Quoting.LN.xml", "Quoting.NY.xml"]
    for name in written:
        assert (out / name).read_bytes() == (TESTDATA / "expected" / name).read_bytes()


def test_invalid_topology_is_reported_against_its_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A compile error exits 1 with the message prefixed by the file it is in."""
    bad = tmp_path / "bad.topo"
    bad.write_text("role X { }\ninstance X { }\n", encoding="utf-8")
    assert main(["check", "--topology", str(bad)]) == EXIT_FAILURE
    assert capsys.readouterr().err == (
        f"topoc: {bad}: name 'X' already defined as role at line 1, column 6 at line 2, column 10\n"
    )


def test_invalid_catalog_is_reported_against_its_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A catalog error exits 1 with the message prefixed by the catalog file."""
    bad = tmp_path / "bad.json"
    bad.write_text('[{"definition": "x", "name_pattern": "x"}]', encoding="utf-8")
    assert main(generate_args(str(bad), tmp_path)) == EXIT_FAILURE
    assert capsys.readouterr().err == (
        f"topoc: {bad}: parsing topic catalog: entry 0: missing 'codec'\n"
    )


def test_invalid_context_is_reported_against_its_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Context data that is not JSON, or that does not fit the topology's
    declaration, exits 1 with the message prefixed by the context file."""
    bad = tmp_path / "bad.json"
    bad.write_text('{ "quoting_locations": ["LN", "LN", 3] }', encoding="utf-8")
    assert main(["check", "--topology", TOPOLOGY, "--context", str(bad)]) == EXIT_FAILURE
    assert capsys.readouterr().err == (
        f"topoc: {bad}: context: quoting_locations[2]: expected 'string', got 3\n"
    )
    bad.write_text('{ "a": 1, "a": 2 }', encoding="utf-8")
    assert main(["check", "--topology", TOPOLOGY, "--context", str(bad)]) == EXIT_FAILURE
    assert capsys.readouterr().err == f"topoc: {bad}: context: duplicate key 'a'\n"


def test_context_is_required_exactly_when_declared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without `--context`, a topology declaring a context fails against the
    topology; with one, a topology declaring none fails against the context."""
    assert main(["check", "--topology", TOPOLOGY]) == EXIT_FAILURE
    assert capsys.readouterr().err == (
        f"topoc: {TOPOLOGY}: this topology declares a 'context' block, but no context was "
        "supplied at line 12, column 1\n"
    )
    plain = tmp_path / "plain.topo"
    plain.write_text("instance A { }\n", encoding="utf-8")
    assert main(["check", "--topology", str(plain), "--context", CONTEXT]) == EXIT_FAILURE
    assert capsys.readouterr().err == (
        f"topoc: {CONTEXT}: context: a context was supplied, but the topology declares no "
        "'context' block\n"
    )


def test_missing_input_file_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unreadable input file exits 1, naming the file."""
    missing = tmp_path / "missing.topo"
    assert main(["check", "--topology", str(missing)]) == EXIT_FAILURE
    assert capsys.readouterr().err.startswith(f"topoc: {missing}: cannot open file")


def test_view_writes_a_page_named_after_the_cluster(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`view` reads the configurations and writes `<name>.html`, embedding the
    cluster."""
    configs = sorted(str(p) for p in (TESTDATA / "expected").glob("*.xml"))
    out = tmp_path / "pages"
    assert main(["view", "--cluster", "ref", "--output-dir", str(out), *configs]) == EXIT_OK
    assert capsys.readouterr().out == f"wrote {out / 'ref.html'}\n"
    page = (out / "ref.html").read_text(encoding="utf-8")
    assert '"name": "Quoting.LN"' in page
    assert "<title>ref · topoview</title>" in page


def test_view_gives_field_names_from_a_catalog(tmp_path: Path) -> None:
    """With `--catalog`, keys show field names: `refdata` keys on `symbol`."""
    configs = sorted(str(p) for p in (TESTDATA / "expected").glob("*.xml"))
    catalog = str(TESTDATA / "reference.catalog.json")
    argv = ["view", "--cluster", "ref", "--catalog", catalog, "--output-dir", str(tmp_path)]
    assert main([*argv, *configs]) == EXIT_OK
    page = (tmp_path / "ref.html").read_text(encoding="utf-8")
    assert '"keys": ["/s"], "keyNames": ["/symbol"]' in page


def test_view_reports_a_configuration_against_its_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file that is not an AMPS configuration exits 1, naming the file."""
    bad = tmp_path / "bad.xml"
    bad.write_text("<Config/>", encoding="utf-8")
    assert main(["view", "--cluster", "c", "--output-dir", str(tmp_path), str(bad)]) == EXIT_FAILURE
    assert capsys.readouterr().err == (
        f"topoc: {bad}: not an AMPS configuration (root element <Config>)\n"
    )


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "a\tb"])
def test_view_rejects_cluster_names_that_cannot_name_a_file(name: str, tmp_path: Path) -> None:
    """The cluster names the page's file, so a name that cannot is a usage
    error."""
    with pytest.raises(SystemExit) as excinfo:
        main(["view", "--cluster", name, "--output-dir", str(tmp_path), "a.xml"])
    assert excinfo.value.code == EXIT_USAGE


@pytest.mark.parametrize(
    "argv",
    [[], ["frob"], ["check"], ["generate", "--topology", TOPOLOGY]],
    ids=["no-command", "unknown-command", "missing-topology", "missing-catalog"],
)
def test_usage_errors_exit_2(argv: list[str]) -> None:
    """A missing or unknown command, or a missing required option, is a usage
    error: argparse prints usage and exits with status 2."""
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == EXIT_USAGE


def test_version_reports_the_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == EXIT_OK
    assert capsys.readouterr().out == "topoc 1.0.0\n"
