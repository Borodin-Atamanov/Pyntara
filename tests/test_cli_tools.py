"""Unit tests for the cli_tools task.

All external resources (dpkg-query, apt-get) are mocked via monkeypatch;
the tests never touch the real system (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import MODES, Config, load_config
from pyntara.context import Context
from pyntara.tasks import cli_tools

# Package set used by the tests; mirrors the real config but stays small.
# Four packages keep the 70 percent threshold math clean: one failure gives
# 75 percent, above the threshold.
TEST_PACKAGES = ("mc", "htop", "hollywood", "wget")

# The real catalog from the repository config; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks


def _test_config(
    packages: tuple[str, ...] = TEST_PACKAGES,
    *,
    threshold: int = 70,
) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return make_config(cli_tools_packages=packages, cli_tools_threshold=threshold)


def _ctx() -> Context:
    return make_context(config=_test_config())


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


def test_cli_tools_is_in_every_mode_default_set() -> None:
    for mode in MODES:
        assert "cli_tools" in task_catalog.default_tasks(mode, REAL_TASKS)


def test_cli_tools_depends_on_add_extra_repos() -> None:
    # cli_tools needs universe and multiverse enabled before its packages
    # can resolve, so add_extra_repos is a hard dependency.
    task_def = task_catalog.by_name("cli_tools", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_real_config_names_exiftool_by_its_real_package() -> None:
    # exiftool is a virtual name provided by libimage-exiftool-perl.
    # dpkg-query cannot see virtual names, so listing exiftool would make
    # the task consider it missing forever and reinstall it on every run.
    # The real config must name the real package.
    config = load_config(REPO_ROOT / "config")
    assert "libimage-exiftool-perl" in config.cli_tools.packages
    assert "exiftool" not in config.cli_tools.packages


def test_all_installed_skips_apt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only mc is missing; apt must install exactly that package.
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"mc"})
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "4/4" in (result.message or "")
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "mc"]]


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
    result = cli_tools.task(_ctx())
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


def test_apt_failure_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every package fails and the index refresh fails too: nothing could be
    # installed, so the task reports a failure with the reasons.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        raise subprocess.CalledProcessError(100, command)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is False
    assert result.changed is False
    assert result.error
    assert "mc" in (result.error or "")
    assert "htop" in (result.error or "")


def test_apt_hang_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hung apt-get must not block the other packages; the timed-out
    # package is reported and the rest still installs.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install" and command[-1] == "htop":
            raise subprocess.TimeoutExpired(command, timeout=1800)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "3/4" in (result.message or "")
    assert "failed: htop" in (result.message or "")
    assert "htop" in (result.error or "")


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
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    updates = [call for call in calls if call[0] == "apt-get" and call[1] == "update"]
    assert len(updates) == 1
    apt_calls = [call for call in calls if call[0] == "apt-get"]
    assert apt_calls[0] == ["apt-get", "update"]


def test_force_mode_keeps_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force mode reruns the task but does not change the outcome when the
    # target state is already reached.
    ctx = Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset({"cli_tools"}),
        task_data_root=Path("/tmp"),
        skip_apt_update=False,
        config=_test_config(),
    )
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert not any(call[0] == "apt-get" for call in calls)


def test_missing_package_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # hollywood cannot be installed: mc, htop and wget still install, which
    # is 75 percent of the set, above the 70 percent threshold, so the task
    # succeeds and hollywood is reported as failed.
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
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "3/4" in (result.message or "")
    assert "failed: hollywood" in (result.message or "")


def test_all_packages_missing_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # No package can be installed at all: the task must fail with reasons.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        raise subprocess.CalledProcessError(100, command)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is False
    assert result.changed is False
    assert result.error
    assert "mc" in (result.error or "")
    assert "hollywood" in (result.error or "")


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
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "installed" in (result.message or "")
    assert "apt index refresh" in (result.message or "")


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
    result = cli_tools.task(_ctx())
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
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert "hollywood" in (result.error or "")
    hollywood_installs = [
        call
        for call in calls
        if call[0] == "apt-get"
        and call[1] == "install"
        and call[-1] == "hollywood"
    ]
    assert len(hollywood_installs) == 4


def test_no_retries_when_configured_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # retries=0 means a single attempt per package: a failing package is
    # attempted once and reported without a retry.
    ctx = make_context(
        config=make_config(cli_tools_packages=("mc",), cli_tools_retries=0)
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(ctx)
    assert result.success is False
    installs = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(installs) == 1


def test_skip_apt_update_skips_index_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # skip_apt_update=True disables the index refresh entirely: only
    # installs run, so a test run never waits for apt-get update.
    ctx = make_context(skip_apt_update=True, config=_test_config())
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"mc"})
    result = cli_tools.task(ctx)
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
    ctx = make_context(
        skip_apt_update=True,
        config=make_config(cli_tools_packages=("mc",), cli_tools_retries=3),
    )
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
    result = cli_tools.task(ctx)
    assert result.success is True
    assert "1/1" in (result.message or "")
    assert not any(call[0] == "apt-get" and call[1] == "update" for call in calls)
    installs = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
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
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "3/4" in (result.message or "")
    assert "failed: hollywood" in (result.message or "")


def test_below_threshold_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    # Three of four packages fail: only 25 percent installed, far below the
    # 70 percent threshold, so the task fails with the reasons.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if (
            command[0] == "apt-get"
            and command[1] == "install"
            and command[-1] != "mc"
        ):
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is False
    assert "htop" in (result.error or "")
    assert "hollywood" in (result.error or "")


def test_exactly_at_threshold_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # One of two packages installs: exactly 50 percent, at the 50 percent
    # threshold, so the task succeeds (failure only below the threshold).
    ctx = Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset(),
        task_data_root=Path("/tmp"),
        skip_apt_update=True,
        config=_test_config(packages=("mc", "htop"), threshold=50),
    )
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
    result = cli_tools.task(ctx)
    assert result.success is True
    assert "1/2" in (result.message or "")


def test_zero_threshold_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero threshold means the task never fails on missing packages.
    ctx = Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset(),
        task_data_root=Path("/tmp"),
        skip_apt_update=True,
        config=_test_config(packages=("mc",), threshold=0),
    )

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(ctx)
    assert result.success is True
    assert "0/1" in (result.message or "")
    assert "failed: mc" in (result.message or "")
