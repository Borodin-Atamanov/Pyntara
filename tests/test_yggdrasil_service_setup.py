"""Unit tests for the yggdrasil_service_setup task.

All external resources (subprocess, filesystem paths) are mocked via
monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.context import Context
from pyntara.tasks import yggdrasil_service_setup

# The newest release tag and the version without the leading v; the asset
# and the version output use the bare version.
TAG = "v0.5.14"
VERSION = "0.5.14"
DEB_CONTENT = b"fake yggdrasil binary\n"


def _release_json(
    *,
    tag: str = TAG,
    arch_asset: str = "amd64",
    include_asset: bool = True,
) -> str:
    """The GitHub releases API payload used by the curl fake."""

    assets: list[dict[str, str]] = []
    if include_asset:
        name = f"yggdrasil-{VERSION}-{arch_asset}.deb"
        assets.append(
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/yggdrasil-network/yggdrasil-go/"
                    f"releases/download/{tag}/{name}"
                ),
            }
        )
    return json.dumps({"tag_name": tag, "assets": assets})


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    retries: int = 3,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=(
            frozenset({"yggdrasil_service_setup"}) if force else frozenset()
        ),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            task_data_root=tmp_path,
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
            yggdrasil_download_dir=tmp_path / "download",
            yggdrasil_install_retries=retries,
        ),
    )


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed_version: str | None = VERSION,
    enabled: bool = True,
    active: bool = True,
    release_json: str = _release_json(),
    arch: str = "amd64",
    fail_install: int = 0,
    active_becomes: bool = True,
    missing_binary: bool = False,
    version_output: str | None = None,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg reports the architecture, yggdrasil -version the installed
    version (None means not installed), curl answers the release API and
    writes the fixture package file, apt-get install fails the first
    fail_install attempts, and systemctl reports the enabled and active
    state from the flags. With active_becomes, the service turns active
    after the first start or restart. With missing_binary, the yggdrasil
    call raises FileNotFoundError like a real missing executable.
    version_output overrides the raw -version output.
    """

    calls: list[list[str]] = []
    install_attempts = 0
    started = False

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal install_attempts, started
        del kwargs
        calls.append(list(command))
        if command[0] == "dpkg" and command[1] == "--print-architecture":
            return _FakeProc(0, f"{arch}\n")
        if command[0] == "yggdrasil":
            if missing_binary:
                raise FileNotFoundError(command[0])
            if installed_version is None:
                return _FakeProc(1, "")
            if version_output is not None:
                return _FakeProc(0, version_output)
            return _FakeProc(0, f"Build version: {installed_version}\n")
        if command[0] == "curl":
            if "--output" in command:
                path = Path(command[command.index("--output") + 1])
                path.write_bytes(DEB_CONTENT)
                return _FakeProc(0)
            return _FakeProc(0, release_json)
        if command[0] == "apt-get":
            if command[1] == "install":
                install_attempts += 1
                if install_attempts <= fail_install:
                    raise subprocess.CalledProcessError(100, command)
            return _FakeProc(0)
        if command[0] == "systemctl":
            if command[1] == "is-enabled":
                if enabled:
                    return _FakeProc(0, "enabled\n")
                return _FakeProc(1, "disabled\n")
            if command[1] == "is-active":
                if active or (active_becomes and started):
                    return _FakeProc(0, "active\n")
                return _FakeProc(1, "inactive\n")
            if command[1] in ("start", "restart"):
                started = True
            return _FakeProc(0)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installed version equals the newest release and the service is
    # enabled and active: the task skips and runs only the status queries.
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, installed_version=VERSION, enabled=True, active=True)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "apt-get" for call in calls)
    assert not any(
        call[0] == "systemctl" and call[1] not in ("is-enabled", "is-active")
        for call in calls
    )


def test_missing_binary_is_treated_as_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing yggdrasil binary raises FileNotFoundError from subprocess;
    # the task treats it as not installed and proceeds with the install
    # instead of crashing.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        installed_version=None,
        enabled=False,
        active=False,
        missing_binary=True,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_installs_new_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # yggdrasil is not installed and the service is not enabled: the task
    # downloads the architecture asset, installs it with apt, enables and
    # starts the service.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch, installed_version=None, enabled=False, active=False
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert VERSION in (result.message or "")
    asset = f"yggdrasil-{VERSION}-amd64.deb"
    assert [
        "apt-get",
        "install",
        "-y",
        str(ctx.config.yggdrasil_service_setup.download_dir / asset),
    ] in calls
    assert ["systemctl", "enable", "yggdrasil.service"] in calls
    assert ["systemctl", "start", "yggdrasil.service"] in calls
    # The downloaded file is removed after the successful install.
    assert not (ctx.config.yggdrasil_service_setup.download_dir / asset).exists()


def test_update_reinstalls_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An older release is installed: the task installs the newer one and
    # restarts the running service.
    ctx = _ctx(tmp_path)
    calls = _install_fake(monkeypatch, installed_version="0.5.13", active=True)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "yggdrasil.service"] in calls
    assert ["systemctl", "start", "yggdrasil.service"] not in calls


def test_install_gives_up_after_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # apt always fails: the task tries one initial attempt plus the
    # configured retries, then reports the failure.
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, installed_version=None, fail_install=99)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is False
    assert "cannot install yggdrasil" in (result.error or "")
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 4


def test_install_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The first apt attempt fails, the retry succeeds.
    ctx = _ctx(tmp_path, retries=3)
    calls = _install_fake(monkeypatch, installed_version=None, fail_install=1)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert len(install_calls) == 2


def test_no_matching_asset_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The release has no asset for this architecture: the task reports the
    # missing asset and stops.
    ctx = _ctx(tmp_path)
    release = _release_json(include_asset=False)
    calls = _install_fake(
        monkeypatch, installed_version=None, release_json=release
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is False
    assert "no yggdrasil-" in (result.error or "")
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_release_json_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The releases API fails: the task reports the fetch error.
    ctx = _ctx(tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command[0] == "curl":
            return _FakeProc(22, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is False
    assert "cannot fetch" in (result.error or "")


def test_force_restarts_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured, but the task is forced: the
    # service is restarted, while the matching version is not reinstalled.
    ctx = _ctx(tmp_path, force=True)
    calls = _install_fake(monkeypatch, installed_version=VERSION, active=True)
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "yggdrasil.service"] in calls
    assert not any(
        call[0] == "apt-get" and call[1] == "install" for call in calls
    )


def test_service_never_active_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service is installed but never reports active after start: the
    # task reports the failure.
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=False,
    )
    result = yggdrasil_service_setup.task(ctx)
    assert result.success is False
    assert "did not become active" in (result.error or "")
    starts = [
        call for call in calls if call[0] == "systemctl" and call[1] == "start"
    ]
    assert len(starts) == 1


def test_select_asset_by_architecture() -> None:
    # The asset name uses the bare version and the dpkg architecture;
    # the leading v of the tag is stripped before the lookup.
    release = json.loads(_release_json())
    selected = yggdrasil_service_setup._select_asset(release, VERSION, "amd64")
    assert selected == (
        f"yggdrasil-{VERSION}-amd64.deb",
        (
            "https://github.com/yggdrasil-network/yggdrasil-go/releases/"
            f"download/{TAG}/yggdrasil-{VERSION}-amd64.deb"
        ),
    )
    assert (
        yggdrasil_service_setup._select_asset(release, VERSION, "s390x") is None
    )


def test_installed_version_parsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version triple is extracted from the -version output; a missing
    # binary, a nonzero exit and an unrecognized output report None.
    monkeypatch.setattr(
        "pyntara.utils.subprocess.run",
        lambda *a, **k: _FakeProc(0, "Build version: 0.5.14\n"),
    )
    assert yggdrasil_service_setup._installed_version(10) == "0.5.14"
    monkeypatch.setattr("pyntara.utils.subprocess.run", lambda *a, **k: _FakeProc(1, ""))
    assert yggdrasil_service_setup._installed_version(10) is None
    monkeypatch.setattr("pyntara.utils.subprocess.run", lambda *a, **k: _FakeProc(0, "unknown output"))
    assert yggdrasil_service_setup._installed_version(10) is None
