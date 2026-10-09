"""Guard: the apt argv is spelled in exactly one module.

Every apt command carries the package-lock wait through the shared factories of
pyntara.utils, so a task or a values module that spells an apt command itself
can drop the option and fail on the lock alone with exit code 100
(docs/contracts/bootstrap.md). This guard reads the source of the package and
refuses an apt executable named anywhere but that one builder module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pyntara

PACKAGE_ROOT: Path = Path(pyntara.__file__).resolve().parent
BUILDER_MODULE: Path = PACKAGE_ROOT / "utils.py"
APT_EXECUTABLES: frozenset[str] = frozenset({"apt", "apt-get"})


def _apt_executable_literal_lines(path: Path) -> list[int]:
    """The line numbers whose string literals name an apt executable."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in APT_EXECUTABLES
    ]


def test_apt_argv_is_spelled_only_in_the_builder_module() -> None:
    # The rule that keeps the lock wait from being dropped: the apt command is
    # built in utils.py alone, so no caller can forget the option.
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path == BUILDER_MODULE:
            continue
        offenders.extend(
            f"{path.relative_to(PACKAGE_ROOT)}:{line}"
            for line in _apt_executable_literal_lines(path)
        )
    assert offenders == [], (
        "an apt command is spelled outside the shared factories of "
        f"pyntara.utils, which would drop the package-lock wait: {offenders}"
    )


def test_the_builder_module_really_builds_apt_commands() -> None:
    # The exception for utils.py must stay justified: the guard above would
    # pass silently if the builders ever moved out of that module.
    assert _apt_executable_literal_lines(BUILDER_MODULE)
