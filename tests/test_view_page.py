"""Renders a topoview page: the cluster embedded in the template's script
element, safe against values that would close it, and the page titled after
the cluster.

Methodology: pages are rendered from small clusters built in memory; the test
extracts the embedded script element's text and parses it back.
"""

import json
import re
from typing import Any

from topoc.view.cluster import build_cluster, parse_instance
from topoc.view.model_json import FORMAT_VERSION
from topoc.view.page import PLACEHOLDER, render_page


def embedded(page: str) -> Any:
    """The cluster JSON a page embeds."""
    found = re.search(
        r'<script id="topoview-cluster" type="application/json">(.*?)</script>', page, re.S
    )
    assert found is not None
    return json.loads(found.group(1))


def test_embeds_the_cluster_in_place_of_the_placeholder() -> None:
    cluster = build_cluster(
        "prod", [parse_instance("<AMPSConfig><Name>A</Name></AMPSConfig>", "a")]
    )
    page = render_page(cluster)
    assert PLACEHOLDER not in page
    data = embedded(page)
    assert (data["formatVersion"], data["name"], data["instances"][0]["name"]) == (
        FORMAT_VERSION,
        "prod",
        "A",
    )


def test_values_cannot_close_the_script_element() -> None:
    """A `</script>` in any value is escaped, so the page's structure holds and
    the value reads back intact."""
    hostile = "x</script><script>alert(1)</script>"
    config = (
        f"<AMPSConfig><Name>A</Name><SOW><Topic><Name>{hostile.replace('<', '&lt;')}</Name>"
        "</Topic></SOW></AMPSConfig>"
    )
    page = render_page(build_cluster("prod", [parse_instance(config, "a")]))
    assert "x</script>" not in page
    assert embedded(page)["instances"][0]["topics"][0]["name"] == hostile


def test_titles_the_page_after_the_cluster() -> None:
    page = render_page(build_cluster("LN & NY <prod>", []))
    assert "<title>LN &amp; NY &lt;prod&gt; · topoview</title>" in page
