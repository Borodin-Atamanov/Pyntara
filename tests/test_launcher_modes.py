"""Unit tests for the generated blocks of the launcher file.

The helper is the only writer of the mode line block and the catalog line of
pyntara.sh, so the tests cover the rewrite of both blocks, the refusal to drop
a mode the machine selected, and the guard that reports a launcher which
drifted from the catalog. The last tests read the shipped launcher, which is
the file the gate checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara import launcher_modes
from pyntara.values.tasks import CATALOG, MODES

REPO_ROOT = Path(__file__).resolve().parents[1]

FIXTURE_TEXT = (
    "# a comment\n"
    '# PYNTARA_INSTALL_MODE="minimal"\n'
    '# PYNTARA_INSTALL_MODE="server"\n'
    "# a comment between the blocks\n"
    '# PYNTARA_TASKS="one two"\n'
    '# PYNTARA_FORCE_TASKS=""\n'
)


def _catalog_names() -> tuple[str, ...]:
    """The task names in catalog order, as the helper writes them."""

    return tuple(task.name for task in CATALOG)


def test_mode_lines_are_commented_and_come_in_catalog_order() -> None:
    assert launcher_modes.mode_lines(("minimal", "fast_desktop")) == (
        '# PYNTARA_INSTALL_MODE="minimal"',
        '# PYNTARA_INSTALL_MODE="fast_desktop"',
    )


def test_the_rewrite_adds_a_declared_mode_to_the_block() -> None:
    rewritten = launcher_modes.rewrite_launcher(
        FIXTURE_TEXT, ("minimal", "server", "fast_desktop"), ("one",)
    )
    assert '# PYNTARA_INSTALL_MODE="fast_desktop"\n' in rewritten
    assert rewritten.count("# PYNTARA_INSTALL_MODE=") == 3


def test_the_rewrite_takes_the_task_line_from_the_catalog() -> None:
    rewritten = launcher_modes.rewrite_launcher(
        FIXTURE_TEXT, ("minimal",), ("alpha", "beta")
    )
    assert '# PYNTARA_TASKS="alpha beta"\n' in rewritten
    assert "one two" not in rewritten


def test_the_rewrite_keeps_the_rest_of_the_file() -> None:
    rewritten = launcher_modes.rewrite_launcher(FIXTURE_TEXT, ("minimal",), ("alpha",))
    assert "# a comment\n" in rewritten
    assert "# a comment between the blocks\n" in rewritten
    assert '# PYNTARA_FORCE_TASKS=""\n' in rewritten


def test_the_rewrite_refuses_a_mode_selected_outside_a_comment() -> None:
    # A selected mode is a choice of the machine, so the helper reports it
    # instead of dropping the line while it rewrites the block.
    active = FIXTURE_TEXT + 'PYNTARA_INSTALL_MODE="desktop"\n'
    with pytest.raises(ValueError, match="outside a comment"):
        launcher_modes.rewrite_launcher(active, ("minimal",), ("alpha",))


def test_the_rewrite_requires_both_blocks() -> None:
    with pytest.raises(ValueError, match="no mode line"):
        launcher_modes.rewrite_launcher("# nothing here\n", ("minimal",), ("alpha",))
    without_task_line = '# PYNTARA_INSTALL_MODE="minimal"\n'
    with pytest.raises(ValueError, match="no task list line"):
        launcher_modes.rewrite_launcher(without_task_line, ("minimal",), ("alpha",))


def test_drift_is_reported_and_a_matching_launcher_is_not() -> None:
    matching = launcher_modes.rewrite_launcher(
        FIXTURE_TEXT, ("minimal", "server"), ("one", "two")
    )
    assert (
        launcher_modes.launcher_needs_rewrite(
            matching, ("minimal", "server"), ("one", "two")
        )
        is False
    )
    assert (
        launcher_modes.launcher_needs_rewrite(
            matching, ("minimal", "server", "fast_desktop"), ("one", "two")
        )
        is True
    )


def test_the_shipped_launcher_carries_the_declared_modes_and_the_catalog() -> None:
    # The gate: the blocks of the file in the repository are the ones the
    # catalog declares, so a mode added to the catalog reaches the launcher by
    # python -m pyntara.launcher_modes and never by hand.
    text = (REPO_ROOT / "pyntara.sh").read_text(encoding="utf-8")
    assert launcher_modes.launcher_needs_rewrite(text, MODES, _catalog_names()) is False


def test_main_check_reports_drift_without_writing(tmp_path: Path) -> None:
    launcher = tmp_path / "pyntara.sh"
    launcher.write_text(FIXTURE_TEXT, encoding="utf-8")
    assert launcher_modes.main(["--root", str(tmp_path), "--check"]) == 1
    assert launcher.read_text(encoding="utf-8") == FIXTURE_TEXT


def test_main_writes_the_blocks_the_catalog_declares(tmp_path: Path) -> None:
    launcher = tmp_path / "pyntara.sh"
    launcher.write_text(FIXTURE_TEXT, encoding="utf-8")
    assert launcher_modes.main(["--root", str(tmp_path)]) == 0
    rewritten = launcher.read_text(encoding="utf-8")
    assert rewritten.count("# PYNTARA_INSTALL_MODE=") == len(MODES)
    assert f'# PYNTARA_TASKS="{" ".join(_catalog_names())}"' in rewritten


def test_main_reports_a_repository_without_a_launcher(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert launcher_modes.main(["--root", str(tmp_path), "--check"]) == 1
    assert "not found" in capsys.readouterr().err
