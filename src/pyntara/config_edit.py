"""Line-level config editing helpers for tasks.

Tasks that must preserve unrelated content and comments use these helpers
for targeted line edits instead of overwriting whole files
(docs/guides/project-structure.md, section Configuration editing). The
module is the single shared implementation of the line-edit approach:
tasks import replace_line_by_string and add_line_to_file instead of
copying the logic (docs/guides/project-rules.md section 4). The helpers
fit files where one setting is one line and the line order does not
matter; structured formats are edited with their parsers, never here.
"""

from __future__ import annotations

from pathlib import Path


def replace_line_by_string(
    text: str,
    needle: str,
    slide: str,
    stop_word: str = "",
    add_slide_if_no_needle: bool = True,
) -> tuple[str, bool]:
    """Replace lines containing needle or slide with slide.

    A line containing stop_word is left untouched, and a line that
    already equals slide is left untouched: the slide is then already
    present and must not be duplicated. When no line was replaced and
    add_slide_if_no_needle is true, slide is appended to the text. The
    result keeps the trailing newline of the input and gains one when
    anything changed. Returns the new text and whether anything changed.
    """

    lines = text.splitlines()
    changed = False
    slide_present = False
    for index, line in enumerate(lines):
        if stop_word and stop_word in line:
            continue
        if line == slide:
            slide_present = True
            continue
        if needle in line or slide in line:
            lines[index] = slide
            changed = True
    if not changed and not slide_present and add_slide_if_no_needle:
        lines.append(slide)
        changed = True
    result = "\n".join(lines)
    if text.endswith("\n") or changed:
        result += "\n"
    return result, changed


def add_line_to_file(path: Path, line: str, comments_sign: str = "#") -> bool:
    """Ensure line is present in the file; return whether the file changed.

    An existing line equal to line is kept, a fuzzy line containing it is
    normalized to the exact line, a line containing comments_sign is left
    untouched and a missing line is appended. A missing file is not
    created. Read and write errors raise OSError at the call site.
    """

    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    new_text, changed = replace_line_by_string(
        text, line, line, stop_word=comments_sign
    )
    if changed:
        path.write_text(new_text, encoding="utf-8")
    return changed
