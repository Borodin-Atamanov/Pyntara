"""Deployed helper programs must parse under the declared minimum interpreter.

The programs under task_data/ are copied to the target machine and started by
their boot service with the system interpreter of that machine, which the
project does not control. A helper that uses syntax newer than the declared
minimum cannot even be parsed there, so its task fails on a machine whose
interpreter is older (zram_service, 2026-10-14: an unparenthesized except,
allowed only from Python 3.14, broke the deployed program on a 3.12 machine).

The running interpreter of the test suite is 3.14, which accepts such syntax
silently, so the check parses every helper with the grammar of the declared
minimum version instead of trusting the interpreter that happens to run.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYED_HELPER_DIRECTORY = REPO_ROOT / "task_data"

# The oldest interpreter a deployed helper must be understood by. The programs
# are standard-library only, so a low floor costs nothing and covers machines
# that carry an older system interpreter than the fleet target. Raising it is a
# deliberate decision, because it drops those machines.
MINIMUM_HELPER_INTERPRETER = (3, 10)


def _deployed_helper_paths() -> list[Path]:
    """Every program under task_data/ that a task copies to a target machine."""

    return sorted(DEPLOYED_HELPER_DIRECTORY.rglob("*.py"))


def test_deployed_helpers_parse_under_the_minimum_interpreter() -> None:
    # A helper the minimum interpreter cannot parse is a task that fails on
    # every machine with that interpreter, before a single line runs.
    paths = _deployed_helper_paths()
    assert paths, "no deployed helper programs were found under task_data"
    failures: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(
                source,
                filename=str(path),
                feature_version=MINIMUM_HELPER_INTERPRETER,
            )
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.msg}")
    assert not failures, (
        "deployed helpers use syntax newer than Python "
        f"{MINIMUM_HELPER_INTERPRETER[0]}.{MINIMUM_HELPER_INTERPRETER[1]}: "
        + "; ".join(failures)
    )


def test_the_minimum_interpreter_check_rejects_newer_syntax() -> None:
    # The guard is only worth anything when it really rejects what the old
    # interpreter rejects; a check that silently passed everything would hide
    # the very defect it exists to catch.
    newer_syntax = "try:\n    pass\nexcept OSError, ValueError:\n    pass\n"
    with pytest.raises(SyntaxError):
        ast.parse(newer_syntax, feature_version=MINIMUM_HELPER_INTERPRETER)
