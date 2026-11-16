"""Writes the development and test fixture: the reference cluster, read from
`testdata/expected/` with field names from `testdata/reference.catalog.json`,
in the JSON form a topoview page embeds.

    uv run python topoview/fixtures/generate.py
"""

import json
from pathlib import Path

from topoc.catalog_json import parse_catalog_json
from topoc.view.cluster import read_cluster
from topoc.view.field_names import with_field_names
from topoc.view.model_json import cluster_to_json

ROOT = Path(__file__).resolve().parents[2]

TESTDATA = ROOT / "testdata"
catalog = parse_catalog_json((TESTDATA / "reference.catalog.json").read_text(encoding="utf-8"))
cluster = with_field_names(
    read_cluster("reference", sorted((TESTDATA / "expected").glob("*.xml"))), catalog
)
output = Path(__file__).with_name("reference.cluster.json")
output.write_text(json.dumps(cluster_to_json(cluster), indent=2) + "\n", encoding="utf-8")
print(f"wrote {output.relative_to(ROOT)}")
