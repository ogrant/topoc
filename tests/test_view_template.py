"""The committed page template is current: it was built from the front-end
sources in `topoview/` as they are now.

Methodology: the front-end build stamps the template with a hash of its
sources (`topoview/build/sourceHash.ts`); this test recomputes that hash from
`topoview/` with the same algorithm and compares. A failure means the front end
changed without the template being rebuilt: run `npm run build` in `topoview/`
and commit the result. No Node is needed to run the check.
"""

import hashlib
import re
from pathlib import Path

import pytest

from topoc.view.page import PLACEHOLDER, template

FRONT_END = Path(__file__).resolve().parents[1] / "topoview"
EXCLUDED_DIRECTORIES = {"node_modules", "dist"}


def source_files(directory: Path) -> list[str]:
    """Every file under `directory`, except the excluded directories and names
    starting with `.`, as `/`-separated paths relative to `FRONT_END`."""
    files: list[str] = []
    for entry in directory.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if entry.name not in EXCLUDED_DIRECTORIES:
                files.extend(source_files(entry))
        elif entry.is_file():
            files.append(entry.relative_to(FRONT_END).as_posix())
    return files


def source_hash() -> str:
    digest = hashlib.sha256()
    for file in sorted(source_files(FRONT_END)):
        digest.update(file.encode())
        digest.update(b"\0")
        digest.update((FRONT_END / file).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@pytest.mark.skipif(not FRONT_END.is_dir(), reason="the front-end sources are not here")
def test_template_was_built_from_the_current_sources() -> None:
    stamp = re.search(r'<meta name="topoview-source-hash" content="([0-9a-f]{64})">', template())
    assert stamp is not None, "the template carries no source hash; rebuild it"
    assert stamp.group(1) == source_hash(), (
        "the topoview template is stale: run `npm run build` in topoview/ and commit "
        "src/topoc/view/template.html"
    )


def test_template_has_one_cluster_placeholder() -> None:
    assert template().count(PLACEHOLDER) == 1
