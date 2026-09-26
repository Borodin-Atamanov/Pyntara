"""Unit tests for the cli_tools_lite_setup task.

The install path itself is shared: pyntara.package_set asks dpkg which packages
of the list are missing, installs exactly those and reports the installed
share. This suite exercises that path through this task, and the heavy section
reuses it with its own list.

All external resources (dpkg-query, apt-get) are mocked via monkeypatch;
the tests never touch the real system (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import cli_tools_lite_setup
from pyntara.values import cli_tools_lite_setup as lite_values
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import tasks as tasks_values

# Package set used by the tests; the real set stays in the values module.
# Four packages keep the threshold math clean: one failure gives 75 percent,
# above the shipped 70 percent threshold.
TEST_PACKAGES = ("mc", "htop", "hollywood", "wget")

# The real catalog from the values package; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = tasks_values.CATALOG


@pytest.fixture(autouse=True)
def _point_the_values_at_the_test_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give every test of this file the small test package set.

    The package set and the threshold are values of the task, so the fixture
    patches them for the run of one test and the shipped values come back
    afterwards. A test that needs another set or another threshold patches
    the same names itself.
    """

    monkeypatch.setattr(lite_values, "PACKAGES", TEST_PACKAGES)
    monkeypatch.setattr(lite_values, "PACKAGE_SUCCESS_THRESHOLD_PERCENT", 70)


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


def test_cli_tools_lite_setup_is_in_the_installed_modes() -> None:
    # The everyday utilities are small, so the quick set keeps them. The modes
    # are named one by one, so a mode added later is not a failure here.
    for mode in ("minimal", "server", "desktop", "fast_desktop"):
        assert "cli_tools_lite_setup" in task_catalog.default_tasks(mode, REAL_TASKS)


def test_cli_tools_lite_setup_depends_on_add_extra_repos() -> None:
    # cli_tools_lite_setup needs universe and multiverse enabled before its packages
    # can resolve, so add_extra_repos is a hard dependency.
    task_def = task_catalog.by_name("cli_tools_lite_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_all_installed_skips_apt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only mc is missing; apt must install exactly that package.
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"mc"})
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "4/4" in (result.message or "")
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "mc"]]


def test_share_follows_the_configured_percent_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another declared percent scale is the scale the share is counted with,
    # so the factor is not a value of the module.
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"mc"})
    monkeypatch.setattr(engine_values, "PERCENT_SCALE", 200)
    ctx = _ctx()
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    assert "200%" in (result.message or "")


def test_config_files_leftover_counts_as_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A package in "deinstall ok config-files" state is not fully installed
    # and must be reinstalled. Each package is installed in its own call.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(0, "deinstall ok config-files\n")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [
        ["apt-get", "install", "-y", "mc"],
        ["apt-get", "install", "-y", "htop"],
        ["apt-get", "install", "-y", "hollywood"],
        ["apt-get", "install", "-y", "wget"],
    ]


def test_apt_failure_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every package fails and the index refresh fails too: nothing could be
    # installed, so the task reports the reasons in the warnings.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        raise subprocess.CalledProcessError(100, command)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert any("mc" in warning for warning in result.warnings)
    assert any("htop" in warning for warning in result.warnings)


def test_apt_hang_reports_the_timeout_as_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hung apt-get must not block the other packages; the timed-out
    # package is reported and the rest still installs.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] == "htop"
        ):
            raise subprocess.TimeoutExpired(command, timeout=1800)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "3/4" in (result.message or "")
    assert result.error is None
    assert any("htop" in warning for warning in result.warnings)


def test_update_runs_before_first_install(monkeypatch: pytest.MonkeyPatch) -> None:
    # By default the apt index is refreshed once, before the first install,
    # so the first apt-get call is an update and a transient install failure
    # still succeeds on the first retry.
    calls: list[list[str]] = []
    first_install = True

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal first_install
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install" and first_install:
            first_install = False
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    updates = [call for call in calls if call[0] == "apt-get" and call[1] == "update"]
    assert len(updates) == 1
    apt_calls = [call for call in calls if call[0] == "apt-get"]
    assert apt_calls[0] == ["apt-get", "update"]


def test_force_mode_keeps_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force mode reruns the task but does not change the outcome when the
    # target state is already reached.
    ctx = make_context(force_tasks=frozenset({"cli_tools_lite_setup"}))
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert not any(call[0] == "apt-get" for call in calls)


def test_missing_package_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # hollywood cannot be installed: mc, htop and wget still install, which
    # is 75 percent of the set, above the 70 percent threshold, so the task
    # succeeds and hollywood is reported as a warning.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] == "hollywood"
        ):
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "3/4" in (result.message or "")
    assert result.error is None
    assert any("hollywood" in warning for warning in result.warnings)


def test_all_packages_missing_is_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No package can be installed at all: the task reports the reasons.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        raise subprocess.CalledProcessError(100, command)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert any("mc" in warning for warning in result.warnings)
    assert any("hollywood" in warning for warning in result.warnings)


def test_update_failure_still_installs_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The apt index refresh fails (e.g. a broken repository), but the first
    # retried package is already in the local cache, so the install still
    # succeeds and the refresh failure is reported as a warning.
    calls: list[list[str]] = []
    first_install = True

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal first_install
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install" and first_install:
            first_install = False
            raise subprocess.CalledProcessError(100, command)
        if command[0] == "apt-get" and command[1] == "update":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "installed" in (result.message or "")
    assert any("apt index refresh" in warning for warning in result.warnings)


def test_retries_transient_install_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # mc fails once (transient error), then installs on the first retry:
    # exactly two install attempts for mc and one index refresh.
    calls: list[list[str]] = []
    mc_attempts = 0

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal mc_attempts
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install" and command[-1] == "mc":
            mc_attempts += 1
            if mc_attempts == 1:
                raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "4/4" in (result.message or "")
    mc_installs = [
        call
        for call in calls
        if call[0] == "apt-get" and call[1] == "install" and call[-1] == "mc"
    ]
    assert len(mc_installs) == 2
    updates = [call for call in calls if call[0] == "apt-get" and call[1] == "update"]
    assert len(updates) == 1


def test_gives_up_after_configured_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    # hollywood always fails: the task tries one initial attempt plus three
    # retries, then reports the failure and keeps the other packages.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] == "hollywood"
        ):
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert any("hollywood" in warning for warning in result.warnings)
    hollywood_installs = [
        call
        for call in calls
        if call[0] == "apt-get" and call[1] == "install" and call[-1] == "hollywood"
    ]
    assert len(hollywood_installs) == 4


def test_no_retries_when_configured_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # retries=0 means a single attempt per package: a failing package is
    # attempted once and reported without a retry.
    monkeypatch.setattr(lite_values, "PACKAGES", ("mc",))
    monkeypatch.setattr(common_values, "PACKAGE_INSTALL_RETRIES", 0)
    ctx = make_context()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    installs = [call for call in calls if call[0] == "apt-get" and call[1] == "install"]
    assert len(installs) == 1
    assert result.warnings


def test_skip_apt_update_skips_index_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # skip_apt_update=True disables the index refresh entirely: only
    # installs run, so a test run never waits for apt-get update.
    ctx = make_context(skip_apt_update=True)
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"mc"})
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "apt-get" and call[1] == "update" for call in calls)
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "mc"]]


def test_skip_apt_update_still_retries_installs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the refresh skipped, a transient install failure still succeeds
    # on a retry without any apt-get update call.
    monkeypatch.setattr(lite_values, "PACKAGES", ("mc",))
    ctx = make_context(skip_apt_update=True)
    calls: list[list[str]] = []
    mc_attempts = 0

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal mc_attempts
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install":
            mc_attempts += 1
            if mc_attempts == 1:
                raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    assert "1/1" in (result.message or "")
    assert not any(call[0] == "apt-get" and call[1] == "update" for call in calls)
    installs = [call for call in calls if call[0] == "apt-get" and call[1] == "install"]
    assert len(installs) == 2


def test_single_failure_within_threshold_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One of four packages fails: 75 percent installed, above the 70
    # percent threshold, so the task succeeds and reports the failure.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] == "hollywood"
        ):
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "3/4" in (result.message or "")
    assert result.error is None
    assert any("hollywood" in warning for warning in result.warnings)


def test_below_threshold_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # Three of four packages fail: only 25 percent installed, far below the
    # 70 percent threshold, so the task reports the reasons as warnings.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install" and command[-1] != "mc":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(_ctx())
    assert result.success is True
    assert any("htop" in warning for warning in result.warnings)
    assert any("hollywood" in warning for warning in result.warnings)


def test_exactly_at_threshold_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # One of two packages installs: exactly 50 percent, at the 50 percent
    # threshold, so the task succeeds (failure only below the threshold).
    monkeypatch.setattr(lite_values, "PACKAGES", ("mc", "htop"))
    monkeypatch.setattr(lite_values, "PACKAGE_SUCCESS_THRESHOLD_PERCENT", 50)
    ctx = make_context()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] == "htop"
        ):
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    assert "1/2" in (result.message or "")


def test_zero_threshold_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero threshold means the task never fails on missing packages.
    monkeypatch.setattr(lite_values, "PACKAGES", ("mc",))
    monkeypatch.setattr(lite_values, "PACKAGE_SUCCESS_THRESHOLD_PERCENT", 0)
    ctx = make_context()

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools_lite_setup.task(ctx)
    assert result.success is True
    assert "0/1" in (result.message or "")
    assert any("mc" in warning for warning in result.warnings)
