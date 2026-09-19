"""Generated blocks of the launcher file pyntara.sh.

The launcher carries the install modes and the whole task catalog as commented
lines a user uncomments for a run, so the file holds two copies of data the
catalog declares: the mode names of MODES and the task names of CATALOG. This
helper is the only writer of those copies. It reads the catalog, rewrites the
two blocks in place, and reports a launcher that has drifted without writing
anything under --check, so a mode or a task added to the catalog reaches the
launcher by one command on the development machine instead of by hand.

The launcher is found under the repository root, which the caller passes with
--root (the current directory by default), the same way python -m
pyntara.bump_version reaches its carriers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyntara.values.tasks import CATALOG, MODES

_LAUNCHER_FILE = Path("pyntara.sh")
_MODE_LINE_PREFIX = '# PYNTARA_INSTALL_MODE="'
_MODE_ASSIGNMENT_PREFIX = "PYNTARA_INSTALL_MODE="
_TASK_LINE_PREFIX = '# PYNTARA_TASKS="'


def mode_lines(modes: tuple[str, ...]) -> tuple[str, ...]:
    """The commented mode lines for the declared vocabulary, in catalog order.

    Every mode is written commented out, so a user uncomments the one line of
    the mode of this run and the file stays inert otherwise.
    """

    return tuple(f'{_MODE_LINE_PREFIX}{mode}"' for mode in modes)


def task_line(task_names: tuple[str, ...]) -> str:
    """The commented line that carries the whole catalog in catalog order."""

    return f'{_TASK_LINE_PREFIX}{" ".join(task_names)}"'


def _block_bounds(lines: list[str], prefix: str) -> tuple[int, int] | None:
    """The first and last index of the consecutive lines with that prefix."""

    start: int | None = None
    end: int | None = None
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            if start is None:
                start = index
            end = index
        elif start is not None:
            break
    if start is None or end is None:
        return None
    return start, end


def _replaced_block(
    lines: list[str], prefix: str, replacement: tuple[str, ...], what: str
) -> list[str]:
    """The lines with the block of that prefix replaced by the new lines.

    A block that is absent is an error: the helper may only rewrite a block it
    can see, so a launcher that lost it is reported instead of silently getting
    a new one in a place nobody chose.
    """

    bounds = _block_bounds(lines, prefix)
    if bounds is None:
        raise ValueError(f"the launcher carries no {what} line ({prefix}...)")
    start, end = bounds
    return [*lines[:start], *replacement, *lines[end + 1 :]]


def rewrite_launcher(
    text: str, modes: tuple[str, ...], task_names: tuple[str, ...]
) -> str:
    """The launcher text with both generated blocks taken from the catalog.

    A mode selected outside a comment is a choice of the machine and not a
    generated line, so the helper refuses to touch a file that carries one
    instead of dropping it.
    """

    lines = text.splitlines()
    for line in lines:
        if line.startswith(_MODE_ASSIGNMENT_PREFIX):
            raise ValueError(
                "the launcher selects a mode outside a comment "
                f"({line.strip()}): comment it out or drop it, so the generated "
                "block can be rewritten without losing a choice"
            )
    lines = _replaced_block(lines, _MODE_LINE_PREFIX, mode_lines(modes), "mode")
    lines = _replaced_block(
        lines,
        _TASK_LINE_PREFIX,
        (task_line(task_names),),
        "task list",
    )
    return "\n".join(lines) + "\n"


def launcher_needs_rewrite(
    text: str, modes: tuple[str, ...], task_names: tuple[str, ...]
) -> bool:
    """True when the launcher's generated blocks differ from the catalog.

    The comparison is what --check reports, so a launcher that drifted is
    visible in a gate instead of on the target machine.
    """

    try:
        return rewrite_launcher(text, modes, task_names) != text
    except ValueError:
        return True


def _catalog_names() -> tuple[str, ...]:
    """The task names in catalog order."""

    return tuple(task.name for task in CATALOG)


def main(argv: list[str] | None = None) -> int:
    """Rewrite the launcher blocks, or report a launcher that drifted."""

    parser = argparse.ArgumentParser(
        description="rewrite the generated mode and task blocks of pyntara.sh"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report a launcher that differs from the catalog; write nothing",
    )
    args = parser.parse_args(argv)

    launcher = args.root / _LAUNCHER_FILE
    if not launcher.is_file():
        print(f"{launcher} not found", file=sys.stderr)
        return 1
    text = launcher.read_text(encoding="utf-8")
    names = _catalog_names()
    if args.check:
        if launcher_needs_rewrite(text, MODES, names):
            print(
                f"{launcher} differs from the catalog: "
                "run python -m pyntara.launcher_modes",
                file=sys.stderr,
            )
            return 1
        print(f"{launcher} carries the declared modes and the whole catalog")
        return 0
    try:
        rewritten = rewrite_launcher(text, MODES, names)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if rewritten == text:
        print(f"{launcher} already carries the declared modes and the whole catalog")
        return 0
    launcher.write_text(rewritten, encoding="utf-8")
    print(f"{launcher} rewritten with {len(MODES)} modes and {len(names)} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
