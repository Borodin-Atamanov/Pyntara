"""Every document must be reachable from README and every link must resolve.

README.md is the documentation index the agent chain starts from and every
document links its neighbours, so a link to a file that was moved or removed
breaks the chain silently: nothing compiles a link, and a reader on the
target machine finds no document. Two rules: each relative link of README and
of every markdown file under docs/ points at a file that exists, and every
document of docs/contracts, docs/spec and docs/guides is named in the README
index. Absolute web links and pure fragment links are out of scope.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTED_FILES = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"\]\(([^)#]+?)(?:#[^)]*)?\)")


def test_every_documented_link_points_at_an_existing_file() -> None:
    missing: list[str] = []
    for path in DOCUMENTED_FILES:
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                label = (
                    path.relative_to(REPO_ROOT)
                    if path.is_relative_to(REPO_ROOT)
                    else path
                )
                missing.append(f"{label}: {target}")
    assert not missing, f"links without a target: {missing}"


def test_every_document_is_listed_in_the_readme_index() -> None:
    # README.md is the entry of the reading chain, so a document nobody links
    # from it is a document a reader does not find. The check names the
    # missing entries instead of counting them.
    index = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    unlisted: list[str] = []
    for folder in ("docs/contracts", "docs/spec", "docs/guides"):
        for path in sorted((REPO_ROOT / folder).glob("*.md")):
            if path.name not in index:
                unlisted.append(str(path.relative_to(REPO_ROOT)))
    assert not unlisted, f"documents absent from the README index: {unlisted}"
