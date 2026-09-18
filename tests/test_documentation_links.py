"""Every document must be reachable from README and every link must resolve.

README.md is the documentation index the agent chain starts from and every
document links its neighbours, so a link to a file that was moved or removed
breaks the chain silently: nothing compiles a link, and a reader on the
target machine finds no document. Three rules: each relative link of README
and of every markdown file under docs/ points at a file that exists, a link
with a fragment points at a heading of that file, and every document of
docs/contracts, docs/spec and docs/guides is named in the README index.
Absolute web links are out of scope.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTED_FILES = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"\]\(([^)#]+?)(?:#[^)]*)?\)")
FRAGMENT_LINK = re.compile(r"\]\(([^)#]+?)?#([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def _heading_slug(title: str) -> str:
    """The anchor a markdown renderer builds from a heading."""

    text = title.strip().lower()
    text = re.sub(r"[`*_\[\]().,:/]", "", text)
    return re.sub(r"\s+", "-", text)


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


def test_every_fragment_points_at_a_heading() -> None:
    # A renamed heading leaves a link that still opens the document but lands
    # nowhere, so the anchor is compared with the headings of its target.
    unmatched: list[str] = []
    for path in DOCUMENTED_FILES:
        text = path.read_text(encoding="utf-8")
        for target, fragment in FRAGMENT_LINK.findall(text):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            target_path = (path.parent / target).resolve() if target else path
            if not target_path.is_file():
                continue
            headings = {
                _heading_slug(match.group(1))
                for match in HEADING.finditer(target_path.read_text(encoding="utf-8"))
            }
            if fragment.lower() not in headings:
                unmatched.append(f"{path.name}: {target}#{fragment}")
    assert not unmatched, f"fragments without a heading: {unmatched}"


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
