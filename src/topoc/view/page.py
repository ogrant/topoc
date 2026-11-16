"""A topoview page: the front end's template (`template.html`, built from
`topoview/` and committed) with a cluster embedded, so the page works on its
own, offline.

The template holds one placeholder script element, which receives the
cluster's JSON. Every `<` in the JSON is escaped, so no value — a topic name, a
filter — can close the script element early.
"""

import html
import json
from importlib import resources

from topoc.errors import TopocError
from topoc.view.cluster import Cluster
from topoc.view.model_json import cluster_to_json

# The template's cluster element, as the front end's `index.html` writes it.
PLACEHOLDER = (
    '<script id="topoview-cluster" type="application/json">{"topoviewPlaceholder":true}</script>'
)
_DEFAULT_TITLE = "<title>topoview</title>"


def template() -> str:
    """The page template shipped with the package."""
    return resources.files("topoc.view").joinpath("template.html").read_text(encoding="utf-8")


def render_page(cluster: Cluster) -> str:
    """The standalone page showing `cluster`."""
    page = template()
    if page.count(PLACEHOLDER) != 1:
        raise TopocError("the topoview template has no single cluster placeholder; rebuild it")
    data = json.dumps(cluster_to_json(cluster), ensure_ascii=False).replace("<", "\\u003c")
    page = page.replace(
        PLACEHOLDER,
        f'<script id="topoview-cluster" type="application/json">{data}</script>',
    )
    # The front end titles the page too; this title shows before it runs.
    return page.replace(_DEFAULT_TITLE, f"<title>{html.escape(cluster.name)} · topoview</title>", 1)
