"""Generates AMPS configuration for the reference example and compares each
instance's file byte for byte with its golden copy in testdata/expected/.

Methodology: an end-to-end regression test of the whole library pipeline
(parse, resolve, catalog, build, XML) using only the checked-in fixtures, which
are shared with the C++ implementation, so passing here means both
implementations produce identical output. Any change in output (content,
ordering, whitespace, escaping) fails it. When a change is intended, regenerate
the goldens (see the top-level README.md) and review the diff.
"""

import pytest
from helpers import read_testdata, reference_context

from topoc.amps.generator import GeneratedFile, generate
from topoc.catalog_json import parse_catalog_json
from topoc.resolve import compile_topology


def generate_reference() -> list[GeneratedFile]:
    """The files generated from the checked-in reference topology, context and
    catalog."""
    return generate(
        compile_topology(read_testdata("reference.topo"), reference_context()),
        parse_catalog_json(read_testdata("reference.catalog.json")),
    )


def test_generates_one_config_per_instance_in_declaration_order() -> None:
    """Exactly one file per instance, named `<Instance>.xml`, in declaration order."""
    assert [f.name for f in generate_reference()] == [
        "Admin.xml",
        "Infra1.xml",
        "Infra2.xml",
        "Quoting.LN.xml",
        "Quoting.NY.xml",
    ]


@pytest.mark.parametrize("file", generate_reference(), ids=lambda f: f.name)
def test_configs_match_golden_files(file: GeneratedFile) -> None:
    """Each file equals its golden copy byte for byte; each file is its own test
    case, so a failure names the file that drifted."""
    assert file.content == read_testdata(f"expected/{file.name}")
