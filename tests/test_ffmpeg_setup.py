"""Unit tests for the ffmpeg_setup task.

All external resources (dpkg-query, apt-get, pkg-config, gcc) are mocked
via monkeypatch; the tests never touch the real system
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import ffmpeg_setup
from pyntara.values import ffmpeg_setup as ffmpeg_values
from pyntara.values import tasks as tasks_values

# Package set used by the tests; mirrors the real config but stays small.
TEST_PACKAGES = ("ffmpeg",)

# The real catalog from the values package; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = tasks_values.CATALOG

# Clone root the ffmpeg fixtures use: _wayrecord_env writes the C sources
# under it, and _ctx hands it to the task through the Context.
_CLONE_ROOT = REPO_ROOT
_FIXTURE_REPO: Path | None = None

# The bytes the fake gcc writes to its output file; a deployed engine that
# carries these bytes counts as already built.
WAYRECORD_BINARY = b"\x7fELF-sentinel-wayrecord-binary\n"
WAYRECORD_C = "int main(void) { return 0; }\n"
ZKDE_CLIENT_C = "/* generated wayland protocol stubs */\n"

# The Wayland interface names the capture engine binds by name; the
# desktop entry must grant exactly these, so the two copies cannot drift.
_ZKDE_INTERFACE_NAME = re.compile(r'"(zkde_[a-z0-9_]+)"')

# The desktop entry template the fixture clone carries; it mirrors the
# shipped task_data/ffmpeg_setup/pyntara-wayrecord.desktop.
DESKTOP_TEMPLATE = (
    "[Desktop Entry]\n"
    "Name=Pyntara Wayrecord\n"
    "Comment=Wayland screen capture source for ffmpeg\n"
    "Exec=$bin_path\n"
    "Icon=camera-video\n"
    "Type=Application\n"
    "NoDisplay=true\n"
    "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1\n"
)


def _desktop_template_path() -> Path:
    """The desktop entry template of the clone the fixture points at."""
    repo = _FIXTURE_REPO or _CLONE_ROOT
    name = ffmpeg_values.WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME
    return repo / "task_data" / "ffmpeg_setup" / name


def _wayrecord_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    """Point the C sources and the deploy targets at tmp; return (bin, desktop).

    The fixture clone carries the wayrecord C sources under
    task_data/ffmpeg_setup/ and enters the task through the Context, and the
    target binary plus the desktop entry live in the tmp tree so the real
    /usr and /usr/local are never touched.
    """

    global _FIXTURE_REPO
    repo = tmp_path / "repo"
    template_dir = repo / "task_data" / "ffmpeg_setup"
    template_dir.mkdir(parents=True)
    (template_dir / "wayrecord.c").write_text(WAYRECORD_C, encoding="utf-8")
    (template_dir / "zkde-screencast-client.c").write_text(
        ZKDE_CLIENT_C, encoding="utf-8"
    )
    template_name = ffmpeg_values.WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME
    (template_dir / template_name).write_text(DESKTOP_TEMPLATE, encoding="utf-8")
    _FIXTURE_REPO = repo
    return (
        tmp_path / "bin" / "pyntara-wayrecord",
        tmp_path / "applications" / "pyntara-wayrecord.desktop",
    )


def _ctx(
    monkeypatch: pytest.MonkeyPatch,
    wayrecord_bin_path: Path,
    wayrecord_desktop_path: Path,
    *,
    skip_apt_update: bool = False,
    repo_root: Path | None = None,
) -> Context:
    """Context of the task with its values pointed at the fixture tree.

    The values are module constants, so the helper patches them for the test
    that calls it; monkeypatch puts the shipped values back afterwards,
    whether the test passed or failed, so no test can leak into the next one
    of the same worker.
    """

    monkeypatch.setattr(ffmpeg_values, "PACKAGES", TEST_PACKAGES)
    monkeypatch.setattr(ffmpeg_values, "WAYRECORD_BIN_PATH", wayrecord_bin_path)
    monkeypatch.setattr(ffmpeg_values, "WAYRECORD_DESKTOP_PATH", wayrecord_desktop_path)
    return make_context(
        task_name="ffmpeg_setup",
        repo_root=repo_root or _FIXTURE_REPO or _CLONE_ROOT,
        skip_apt_update=skip_apt_update,
    )


def _command_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: set[str],
    install_rc: int = 0,
    build_rc: int = 0,
    pkgconfig_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, apt-get install answers with
    install_rc, pkg-config returns the build flags and gcc writes the
    sentinel WAYRECORD_BINARY to its -o target. Every command is recorded.
    A nonzero return with check=True raises exactly like the real
    subprocess.run.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        rc = 0
        stdout = ""
        if command[0] == "dpkg-query":
            if command[-1] in installed:
                return _FakeProc(0, "install ok installed\n")
            rc = 1
        elif command[0] == "apt-get" and command[1] == "install":
            rc = install_rc
        elif command[0] == "pkg-config":
            stdout = (
                "-I/usr/include/pipewire-0.3 -I/usr/include/spa-0.2 "
                "-lwayland-client -lpipewire-0.3"
            )
            rc = pkgconfig_rc
        elif command[0] == "gcc":
            out_index = command.index("-o") + 1
            Path(command[out_index]).write_bytes(WAYRECORD_BINARY)
            rc = build_rc
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout)
        return _FakeProc(rc, stdout)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_ffmpeg_setup_is_in_every_mode_default_set() -> None:
    for mode in tasks_values.MODES:
        assert "ffmpeg_setup" in task_catalog.default_tasks(mode, REAL_TASKS)


def test_ffmpeg_setup_depends_on_add_extra_repos() -> None:
    # ffmpeg lives in universe, so add_extra_repos is a hard dependency,
    # the same as imagemagick_setup and cli_tools_lite_setup.
    task_def = task_catalog.by_name("ffmpeg_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_the_shipped_values_name_the_meta_package() -> None:
    # The shipped values must name the real package ffmpeg, not a virtual
    # name, so dpkg-query sees it as installed.
    assert "ffmpeg" in ffmpeg_values.PACKAGES
    assert ffmpeg_values.WAYRECORD_BIN_PATH.name == "pyntara-wayrecord"
    assert ffmpeg_values.WAYRECORD_DESKTOP_PATH.name == ("pyntara-wayrecord.desktop")
    # The build toolchain is part of the package set.
    for build_dep in ("gcc", "libwayland-dev", "libpipewire-0.3-dev", "pkgconf"):
        assert build_dep in ffmpeg_values.PACKAGES


def test_all_installed_skips_apt_and_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    wayrecord_bin_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_bin_path.write_bytes(WAYRECORD_BINARY)
    wayrecord_bin_path.chmod(0o755)
    wayrecord_desktop_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_desktop_path.write_text(
        ffmpeg_setup._desktop_content(_desktop_template_path(), wayrecord_bin_path),
        encoding="utf-8",
    )
    calls = _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)
    # The engine is always rebuilt to check staleness, but a matching
    # target is left untouched.
    assert any(call[0] == "gcc" for call in calls)
    assert wayrecord_bin_path.read_bytes() == WAYRECORD_BINARY


def test_installs_missing_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    calls = _command_fake(monkeypatch, installed=set())
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is True
    assert "ffmpeg" in (result.message or "")
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert len(update_calls) == 1
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "ffmpeg"]]


def test_skip_apt_update_skips_the_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    calls = _command_fake(monkeypatch, installed=set())
    result = ffmpeg_setup.task(
        _ctx(
            monkeypatch,
            wayrecord_bin_path,
            wayrecord_desktop_path,
            skip_apt_update=True,
        )
    )
    assert result.success is True
    assert result.changed is True
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert update_calls == []


def test_install_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package install fails: the reason is reported and the engine is
    # still built from the sources of the repository.
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    _command_fake(monkeypatch, installed=set(), install_rc=1)
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert any("failed to install" in warning for warning in result.warnings)


def test_wayrecord_built_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is True
    assert wayrecord_bin_path.read_bytes() == WAYRECORD_BINARY
    assert wayrecord_bin_path.stat().st_mode & 0o777 == 0o755
    assert "engine" in (result.message or "")


def test_wayrecord_idempotent_when_matching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    wayrecord_bin_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_bin_path.write_bytes(WAYRECORD_BINARY)
    wayrecord_bin_path.chmod(0o755)
    wayrecord_desktop_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_desktop_path.write_text(
        ffmpeg_setup._desktop_content(_desktop_template_path(), wayrecord_bin_path),
        encoding="utf-8",
    )
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"


def test_desktop_template_name_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The fixture clone carries only the template name the values give, so
    # a name written in the code could not find a template at all.
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    template_dir = _desktop_template_path().parent
    (template_dir / "pyntara-wayrecord.desktop").unlink()
    (template_dir / "other.desktop").write_text(
        DESKTOP_TEMPLATE.replace("Pyntara Wayrecord", "Other Wayrecord"),
        encoding="utf-8",
    )
    ctx = _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    monkeypatch.setattr(
        ffmpeg_values, "WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME", "other.desktop"
    )
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(ctx)
    assert result.success is True
    content = wayrecord_desktop_path.read_text(encoding="utf-8")
    assert "Other Wayrecord" in content
    assert f"Exec={wayrecord_bin_path}" in content


def test_wayrecord_rebuilt_when_different(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    wayrecord_bin_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_bin_path.write_bytes(b"old stale engine")
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is True
    assert wayrecord_bin_path.read_bytes() == WAYRECORD_BINARY


def test_build_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The engine build fails: the reason is reported and the desktop entry
    # is still deployed.
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES), build_rc=1)
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert any("cannot build wayrecord" in warning for warning in result.warnings)
    assert wayrecord_desktop_path.is_file()


def test_desktop_written_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(monkeypatch, tmp_path)
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(monkeypatch, wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    expected = ffmpeg_setup._desktop_content(
        _desktop_template_path(), wayrecord_bin_path
    )
    assert wayrecord_desktop_path.read_text(encoding="utf-8") == expected
    assert "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1" in expected
    assert "desktop entry" in (result.message or "")


def test_the_desktop_entry_grants_the_interface_the_engine_binds() -> None:
    # The capture engine binds the KWin screencast protocol by the name the
    # C source carries, and the desktop entry grants the interfaces it
    # lists: two copies of one name that must agree, because a rename in
    # one of them would leave the engine without the grant and break the
    # capture without a word. The name itself stays in both files: it is
    # the identity of the protocol the generated client implements, not a
    # value of the machine (config content spec, Exceptions).
    task_data = REPO_ROOT / "task_data" / "ffmpeg_setup"
    source = (task_data / "wayrecord.c").read_text(encoding="utf-8")
    bound = sorted(set(_ZKDE_INTERFACE_NAME.findall(source)))
    assert bound, "the C source no longer names the KWin screencast interface"
    template = (
        task_data / ffmpeg_values.WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME
    ).read_text(encoding="utf-8")
    granted = [
        line.removeprefix("X-KDE-Wayland-Interfaces=")
        for line in template.splitlines()
        if line.startswith("X-KDE-Wayland-Interfaces=")
    ]
    assert granted, "the desktop entry grants no Wayland interface"
    granted_names = granted[0].split(";")
    for name in bound:
        assert name in granted_names, (
            f"the desktop entry grants {granted_names}, but the engine binds {name}"
        )


def test_wayrecord_missing_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The engine sources are missing: the build step is reported and the
    # task completes, because the packages are the other half of its work.
    repo = tmp_path / "repo"
    template_dir = repo / "task_data" / "ffmpeg_setup"
    template_dir.mkdir(parents=True)
    wayrecord_bin_path = tmp_path / "bin" / "pyntara-wayrecord"
    wayrecord_desktop_path = tmp_path / "applications" / "pyntara-wayrecord.desktop"
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(
            monkeypatch,
            wayrecord_bin_path,
            wayrecord_desktop_path,
            repo_root=repo,
        )
    )
    assert result.success is True
    assert any("missing wayrecord source" in warning for warning in result.warnings)
