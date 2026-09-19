"""Unit tests for the cli_tools_heavy_setup task.

The task is the heavy half of the console tools: the media and document
packages whose install is long (calibre, texlive-extra-utils, pdftk-java). The
install path itself is shared with the lite section (pyntara.package_set), so
this suite checks the facts that belong to this section alone: its place in the
catalog, the real package list, that the task installs its own list, and that a
package of that list which fails is named here.

All external resources (dpkg-query, apt-get) are mocked via monkeypatch;
the tests never touch the real system (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import cli_tools_heavy_setup
from pyntara.values import cli_tools_heavy_setup as heavy_values
from pyntara.values import tasks as tasks_values

# The real catalog from the values package; the mode-membership and
# dependency tests use it so they cover the actual task set.
REAL_TASKS = tasks_values.CATALOG

# A three-package fixture keeps the share arithmetic readable: one failure is
# 66 percent, below the shipped 70 percent threshold, so the shortfall of this
# section is reported as well.
TEST_PACKAGES = ("calibre", "pandoc", "qpdf")


@pytest.fixture
def _test_package_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the task's values at the small test package set.

    The fixture is requested by the tests that run the task, so the tests that
    check the declared values keep seeing the shipped list.
    """

    monkeypatch.setattr(heavy_values, "PACKAGES", TEST_PACKAGES)
    monkeypatch.setattr(heavy_values, "PACKAGE_SUCCESS_THRESHOLD_PERCENT", 70)


def _ctx() -> Context:
    return make_context()


def _install_fake(
    monkeypatch: pytest.MonkeyPatch, *, installed: set[str]
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, every other command succeeds
    and is recorded.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            if command[-1] in installed:
                return _FakeProc(0, "install ok installed\n")
            return _FakeProc(1, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_cli_tools_heavy_setup_stays_out_of_the_quick_mode() -> None:
    # The media and document toolset is the long install the quick set exists
    # to avoid, so fast_desktop leaves it out while the other modes keep it.
    for mode in ("minimal", "server", "desktop"):
        assert "cli_tools_heavy_setup" in task_catalog.default_tasks(mode, REAL_TASKS)
    assert "cli_tools_heavy_setup" not in task_catalog.default_tasks(
        "fast_desktop", REAL_TASKS
    )


def test_cli_tools_heavy_setup_depends_on_add_extra_repos() -> None:
    # The heavy packages live in universe and multiverse, so add_extra_repos
    # is a hard dependency.
    task_def = task_catalog.by_name("cli_tools_heavy_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_the_heavy_list_holds_the_media_and_document_tools() -> None:
    # The heavy half is the media and document toolset; the everyday utilities
    # stay in the lite section, and the virtual kind of exiftool is never named
    # because dpkg-query cannot see it.
    assert "calibre" in heavy_values.PACKAGES
    assert "tesseract-ocr" in heavy_values.PACKAGES
    assert "libimage-exiftool-perl" in heavy_values.PACKAGES
    assert "exiftool" not in heavy_values.PACKAGES
    assert "tree" not in heavy_values.PACKAGES


def test_all_installed_skips_apt(
    monkeypatch: pytest.MonkeyPatch, _test_package_set: None
) -> None:
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools_heavy_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_the_missing_package_of_its_own_list(
    monkeypatch: pytest.MonkeyPatch, _test_package_set: None
) -> None:
    # Only pandoc is missing; apt must install exactly that package, which is
    # how the task is proven to read the list of its own values module.
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"pandoc"})
    result = cli_tools_heavy_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "pandoc"]]


def test_a_package_of_the_heavy_list_that_fails_is_named(
    monkeypatch: pytest.MonkeyPatch, _test_package_set: None
) -> None:
    # calibre cannot be installed: the task completes, the share falls below
    # the threshold of this section, and the failing package is named with its
    # reason in the warnings.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] == "calibre"
        ):
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_heavy_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "2/3" in (result.message or "")
    assert any("calibre" in warning for warning in result.warnings)
    assert any("below the configured" in warning for warning in result.warnings)
