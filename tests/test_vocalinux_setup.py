"""Unit tests for the vocalinux_setup task.

All external resources (subprocess, package state, the user manager) are
mocked via monkeypatch; the tests only touch temporary fixtures. The fake
run_command inspects the command shape and answers per command. The cache
directory of the section and the home of the desktop user are values, so one
autouse fixture points them at the temporary directory of the test.
"""

from __future__ import annotations

import errno
import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import package_set, task_catalog
from pyntara.tasks import vocalinux_setup as task_module
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import tasks as tasks_values
from pyntara.values import vocalinux_setup as values


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own cache directory and user home.

    Both are values: the download cache of this section and the home of the
    desktop user, which comes from the shared module. The fixture points them
    at the temporary directory of the test and the shipped values come back
    afterwards, so no test writes into a real cache or a real home.
    """

    monkeypatch.setattr(values, "DOWNLOAD_DIR", tmp_path / "cache")
    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path))


ASSET = "Vocalinux-0.16.2-x86_64.AppImage"
CONFIG_CONTENT = '{"speech_recognition": {"engine": "whisper_cpp"}}\n'
ECHO_CONTENT = (
    "[Desktop Entry]\nExec=echo '' > /dev/null\nName=nada\n"
    "NoDisplay=true\nType=Application\n"
    "X-KDE-GlobalAccel-CommandShortcut=true\n"
)
AUTOSTART_CONTENT = (
    "[Desktop Entry]\nVersion=1.0\nType=Application\nName=Vocalinux\n"
    "Exec=$appimage --start-minimized\nIcon=vocalinux\n"
    "Comment=Voice dictation for Linux\nTerminal=false\n"
    "StartupNotify=false\nX-GNOME-Autostart-enabled=true\n"
)


def _write_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write the task data templates the task reads.

    The file names come from the values module, so a renamed template key is
    what the task looks for and the test stays honest. The unused monkeypatch
    argument is kept because the eleven call sites of this file spell the call
    the same way.
    """

    template_dir = tmp_path / "task_data" / "vocalinux_setup"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / values.APP_CONFIG_TEMPLATE_FILE_NAME).write_text(
        CONFIG_CONTENT, encoding="utf-8"
    )
    (template_dir / values.AUTOSTART_TEMPLATE_FILE_NAME).write_text(
        AUTOSTART_CONTENT, encoding="utf-8"
    )
    (template_dir / values.ECHO_DESKTOP_TEMPLATE_FILE_NAME).write_text(
        ECHO_CONTENT, encoding="utf-8"
    )


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
) -> Any:
    """Context safe for unit tests; the real paths are never touched."""

    return make_context(
        task_name="vocalinux_setup",
        install_mode="desktop",
        force_tasks=frozenset({"vocalinux_setup"}) if force else frozenset(),
        repo_root=tmp_path,
        task_data_root=tmp_path,
    )


class Fakes:
    """Recording of the calls the fakes answered."""

    def __init__(self) -> None:
        self.kwrites: list[list[str]] = []
        self.curls: list[list[str]] = []
        self.usermods: list[list[str]] = []
        self.service_enables: list[list[str]] = []


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_shortcut: str = "",
    installed: bool = False,
    fail_install: bool = False,
    group_present: bool = False,
    fail_group: bool = False,
    service_active: bool = False,
    fail_service: bool = False,
    fail_download: bool = False,
) -> Fakes:
    """Replace run_command, package state and the user manager.

    current_shortcut is the value kreadconfig6 reports for the Meta+S key,
    so a matching value skips the shortcut write. The AppImage download is
    simulated by creating the --output file the task then renames into the
    cache and copies into the user home.
    """

    fakes = Fakes()

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner[0] == "mkdir":
                return _FakeProc(0, "")
            if inner[0] == "kreadconfig6":
                return _FakeProc(0, current_shortcut)
            if inner[0] == "kwriteconfig6":
                fakes.kwrites.append(list(command))
                return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        if command[0] == "id" and command[1:3] == ["-nG", "i"]:
            memberships = "i input" if group_present else "i"
            return _FakeProc(0, memberships)
        if command[0] == "usermod":
            if fail_group:
                raise subprocess.CalledProcessError(1, command)
            fakes.usermods.append(list(command))
            return _FakeProc(0, "")
        if command[0] == "systemctl":
            if command[1:] == [
                "--user",
                "--machine",
                "i@.host",
                "is-active",
                "ydotool.service",
            ]:
                if service_active:
                    return _FakeProc(0, "active")
                return _FakeProc(1, "inactive")
            if command[1:] == [
                "--user",
                "--machine",
                "i@.host",
                "enable",
                "--now",
                "ydotool.service",
            ]:
                if fail_service:
                    raise subprocess.CalledProcessError(1, command)
                fakes.service_enables.append(list(command))
                return _FakeProc(0, "")
        if command[0] == "curl":
            output_index = command.index("--output")
            output_path = Path(command[output_index + 1])
            fakes.curls.append(list(command))
            if fail_download:
                raise subprocess.CalledProcessError(22, command)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("appimage-bytes", encoding="utf-8")
            return _FakeProc(0, "Downloaded 13 bytes")
        raise AssertionError(f"unexpected command: {command}")

    def fake_installed(package: str, timeout: float) -> bool:
        return installed

    def fake_install(
        packages: list[str],
        *,
        install_timeout: float,
        update_timeout: float,
        retries: int,
        skip_update: bool,
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        if fail_install:
            return [], [(package, "cannot install") for package in packages], []
        return packages, [], []

    monkeypatch.setattr(task_module, "run_command", fake_run)
    monkeypatch.setattr(package_set, "package_is_installed", fake_installed)
    monkeypatch.setattr(package_set, "install_packages", fake_install)
    monkeypatch.setattr(task_module, "dpkg_architecture", lambda _timeout: "amd64")
    return fakes


def _appimage_target(tmp_path: Path) -> Path:
    """The install path of the AppImage under the fake home."""

    return tmp_path / values.APPIMAGE_DIR_RELATIVE_PATH / ASSET


def _user_file(tmp_path: Path, relative_path: str) -> Path:
    """One deployed user file under the fake home."""

    return tmp_path / relative_path


def _seed_installed(tmp_path: Path) -> None:
    """Place the AppImage into the fake user home install directory."""

    target = _appimage_target(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("appimage-bytes", encoding="utf-8")


def _seed_user_files(tmp_path: Path) -> None:
    """Write the matching user files the idempotent rerun expects."""

    config_path = _user_file(tmp_path, values.APP_CONFIG_RELATIVE_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(CONFIG_CONTENT, encoding="utf-8")
    autostart_path = _user_file(tmp_path, values.AUTOSTART_RELATIVE_PATH)
    autostart_path.parent.mkdir(parents=True, exist_ok=True)
    autostart_path.write_text(
        task_module._autostart_content(AUTOSTART_CONTENT, _appimage_target(tmp_path)),
        encoding="utf-8",
    )
    echo_path = _user_file(tmp_path, values.ECHO_DESKTOP_RELATIVE_PATH)
    echo_path.parent.mkdir(parents=True, exist_ok=True)
    echo_path.write_text(ECHO_CONTENT, encoding="utf-8")


def test_fresh_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine without Vocalinux gets the app and all the pieces."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    fakes = _install_fakes(monkeypatch)

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    target = _appimage_target(tmp_path)
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "appimage-bytes"
    assert (tmp_path / "cache" / ASSET).is_file()
    assert (tmp_path / ".config" / "vocalinux" / "config.json").read_text(
        encoding="utf-8"
    ) == CONFIG_CONTENT
    autostart = (tmp_path / ".config" / "autostart" / "vocalinux.desktop").read_text(
        encoding="utf-8"
    )
    assert f"Exec={target} --start-minimized" in autostart
    assert (
        tmp_path / ".local" / "share" / "applications" / "net.local.echo.desktop"
    ).read_text(encoding="utf-8") == ECHO_CONTENT
    assert fakes.curls and fakes.curls[0][0] == "curl"
    assert fakes.usermods and fakes.usermods[0][:3] == ["usermod", "-aG", "input"]
    assert fakes.service_enables
    assert fakes.kwrites
    assert fakes.kwrites[0][-2:] == ["_launch", "Meta+S"]


def test_idempotent_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fully configured machine changes nothing on a rerun."""

    _write_templates(tmp_path, monkeypatch)
    _seed_installed(tmp_path)
    _seed_user_files(tmp_path)
    ctx = _ctx(tmp_path)
    fakes = _install_fakes(
        monkeypatch,
        current_shortcut="Meta+S",
        installed=True,
        group_present=True,
        service_active=True,
    )

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is False
    assert result.warnings == ()
    assert fakes.curls == []
    assert fakes.usermods == []
    assert fakes.service_enables == []
    assert fakes.kwrites == []


def test_package_install_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package that cannot be installed is reported and the user files
    are still written, because the AppImage is self-contained."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=False, fail_install=True)

    result = task_module.task(ctx)

    assert result.success is True
    assert result.warnings
    assert (
        Path(common_values.DESKTOP_HOME_DIR) / values.APP_CONFIG_RELATIVE_PATH
    ).is_file()


def test_download_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed AppImage download skips the autostart entry alone."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=True, fail_download=True)

    result = task_module.task(ctx)

    assert result.success is True
    assert any("cannot download" in warning for warning in result.warnings)
    home = Path(common_values.DESKTOP_HOME_DIR)
    assert not (home / values.AUTOSTART_RELATIVE_PATH).exists()
    assert (home / values.APP_CONFIG_RELATIVE_PATH).is_file()


def test_recoverable_steps_report_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed group or service step is a warning, not an error."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(
        monkeypatch,
        installed=True,
        group_present=False,
        fail_group=True,
        service_active=False,
        fail_service=True,
    )

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is True
    assert len(result.warnings) == 2
    assert any("input" in warning for warning in result.warnings)
    assert any("ydotool.service" in warning for warning in result.warnings)
    assert (tmp_path / ".config" / "vocalinux" / "config.json").is_file()


def test_a_failed_user_file_write_is_a_warning_and_skips_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One file that cannot be written does not drop the rest."""

    # The app config cannot be written because its ownership step fails: the
    # task reports that file with its path and still writes the autostart
    # entry, the empty-action file and the shortcut.
    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    fakes = _install_fakes(monkeypatch)
    working_run = task_module.run_command

    def failing_run(command: list[str], **kwargs: Any) -> Any:
        if command[0] == "chown" and command[-1].endswith("config.json"):
            raise subprocess.CalledProcessError(1, command)
        return working_run(command, **kwargs)

    monkeypatch.setattr(task_module, "run_command", failing_run)
    result = task_module.task(ctx)

    assert result.success is True
    assert any("config.json" in warning for warning in result.warnings)
    assert _user_file(tmp_path, values.AUTOSTART_RELATIVE_PATH).is_file()
    assert _user_file(tmp_path, values.ECHO_DESKTOP_RELATIVE_PATH).is_file()
    assert fakes.kwrites


def test_a_failed_shortcut_write_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shortcut that cannot be registered is a warning of a done task."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch)
    working_run = task_module.run_command

    def failing_run(command: list[str], **kwargs: Any) -> Any:
        if "kwriteconfig6" in command:
            raise subprocess.CalledProcessError(1, command)
        return working_run(command, **kwargs)

    monkeypatch.setattr(task_module, "run_command", failing_run)
    result = task_module.task(ctx)

    assert result.success is True
    assert any("_launch" in warning for warning in result.warnings)
    assert _user_file(tmp_path, values.APP_CONFIG_RELATIVE_PATH).is_file()


def test_force_rewrites_matching_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force mode rewrites files and reinstalls even when already set."""

    _write_templates(tmp_path, monkeypatch)
    _seed_installed(tmp_path)
    cache = tmp_path / "cache" / ASSET
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("appimage-bytes", encoding="utf-8")
    _seed_user_files(tmp_path)
    ctx = _ctx(tmp_path, force=True)
    fakes = _install_fakes(
        monkeypatch,
        current_shortcut="Meta+S",
        installed=True,
        group_present=True,
        service_active=True,
    )

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is True
    assert fakes.curls == []
    assert fakes.kwrites  # force re-registers the shortcut


def test_a_forced_rerun_does_not_rewrite_the_installed_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force leaves an installed image that already matches the release."""

    # A forced run rewrites the files and re-registers the shortcut, but the
    # AppImage bytes are the wanted ones, and a running app refuses a rewrite
    # of the same file with a busy error: the copy is not attempted at all.
    _write_templates(tmp_path, monkeypatch)
    _seed_installed(tmp_path)
    cache = tmp_path / "cache" / ASSET
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("appimage-bytes", encoding="utf-8")
    _seed_user_files(tmp_path)
    ctx = _ctx(tmp_path, force=True)
    _install_fakes(
        monkeypatch,
        current_shortcut="Meta+S",
        installed=True,
        group_present=True,
        service_active=True,
    )

    def refuse_copy(source: object, destination: object) -> None:
        raise AssertionError("the installed image must not be rewritten")

    monkeypatch.setattr(task_module.shutil, "copyfile", refuse_copy)
    result = task_module.task(ctx)

    assert result.success is True
    assert result.warnings == ()
    assert (tmp_path / "cache" / ASSET).is_file()


def test_a_busy_installed_image_is_reported_with_the_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An image a running app holds is a warning that names the remedy."""

    # The installed file differs from the release and the app runs: the copy
    # fails with the busy error, which the task reports in words that say what
    # to do, and the autostart entry is written anyway because the file is
    # there.
    _write_templates(tmp_path, monkeypatch)
    _seed_installed(tmp_path)
    target = _appimage_target(tmp_path)
    target.write_text("older-bytes", encoding="utf-8")
    cache = tmp_path / "cache" / ASSET
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("appimage-bytes", encoding="utf-8")
    ctx = _ctx(tmp_path, force=True)
    _install_fakes(monkeypatch, installed=True, group_present=True, service_active=True)

    def busy(source: object, destination: object) -> None:
        raise OSError(errno.ETXTBSY, "Text file busy")

    monkeypatch.setattr(task_module.shutil, "copyfile", busy)
    result = task_module.task(ctx)

    assert result.success is True
    assert any("while Vocalinux runs" in warning for warning in result.warnings)
    assert any("close the app and rerun" in warning for warning in result.warnings)
    assert target.read_text(encoding="utf-8") == "older-bytes"
    assert (
        Path(common_values.DESKTOP_HOME_DIR) / values.AUTOSTART_RELATIVE_PATH
    ).is_file()


def test_missing_config_template_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing config template skips that file alone."""

    _write_templates(tmp_path, monkeypatch)
    (tmp_path / "task_data" / "vocalinux_setup" / "config.json").unlink()
    _seed_installed(tmp_path)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=True, group_present=True, service_active=True)

    result = task_module.task(ctx)

    assert result.success is True
    assert any("config template" in warning for warning in result.warnings)
    home = Path(common_values.DESKTOP_HOME_DIR)
    assert not (home / values.APP_CONFIG_RELATIVE_PATH).exists()
    assert (home / values.AUTOSTART_RELATIVE_PATH).is_file()


def test_release_download_url_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The repository pair and the host template are declared values: another
    # pair and another host are the URL the task downloads from, so a mirror
    # needs no code change.
    monkeypatch.setattr(
        engine_values,
        "GITHUB_RELEASE_DOWNLOAD_URL",
        "https://mirror.example/{repo}/v{version}/{asset_name}",
    )
    url = task_module._release_download_url(
        "Owner/App", "1.2.3", "App-1.2.3-x86_64.AppImage"
    )
    assert url == ("https://mirror.example/Owner/App/v1.2.3/App-1.2.3-x86_64.AppImage")


def test_user_command_prefix_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The wrapper that runs a command as the target user is a value of the
    # section: another wrapper is the argv the task builds.
    monkeypatch.setattr(values, "RUNUSER_COMMAND", ("sudo", "-u", "{username}", "--"))
    assert task_module._as_user_command(
        ["kwriteconfig6", "--file", "kglobalshortcutsrc"]
    ) == [
        "sudo",
        "-u",
        common_values.DESKTOP_USERNAME,
        "--",
        "kwriteconfig6",
        "--file",
        "kglobalshortcutsrc",
    ]


def test_kconfig_calls_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The writer and the two selectors are values: another set of commands is
    # what the task builds for the shortcut file of the shared module.
    monkeypatch.setattr(
        values, "KWRITECONFIG_COMMAND", ("my-writer", "--config", "{file_name}")
    )
    monkeypatch.setattr(values, "CONFIG_GROUP_FLAG", ("--section", "{group}"))
    monkeypatch.setattr(values, "CONFIG_KEY_FLAG", ("--entry", "{key}"))
    assert task_module._kconfig_command(
        values.KWRITECONFIG_COMMAND, ("services",), "myservice"
    ) == [
        "my-writer",
        "--config",
        common_values.SHORTCUTS_FILE_NAME,
        "--section",
        "services",
        "--entry",
        "myservice",
    ]


def test_file_operations_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The maker of the parent directory, the owner writer and the mode writer
    # are values: another program in the section is the argv the task runs
    # around a user file.
    monkeypatch.setattr(values, "MKDIR_COMMAND", ("mymkdir", "--parents", "{path}"))
    monkeypatch.setattr(
        values, "CHOWN_COMMAND", ("mychown", "--owner", "{owner}", "{path}")
    )
    monkeypatch.setattr(
        values, "CHMOD_COMMAND", ("mychmod", "--mode", "{file_mode}", "{path}")
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        seen.append(list(command))
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    assert task_module._write_user_file(
        ".config/systemd/user/ydotool.service",
        "body\n",
        file_mode=0o644,
        timeout=30.0,
        force=True,
    )
    target = tmp_path / ".config" / "systemd" / "user" / "ydotool.service"
    assert seen[0][4:] == ["mymkdir", "--parents", str(target.parent)]
    assert seen[1] == [
        "mychown",
        "--owner",
        (f"{common_values.DESKTOP_USERNAME}:{common_values.DESKTOP_USERNAME}"),
        str(target),
    ]
    assert seen[2] == ["mychmod", "--mode", f"{0o644:o}", str(target)]


def test_group_commands_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reader of the membership and the group writer are values: another
    # program in the section is the argv the task runs when the user is not
    # yet in the group.
    monkeypatch.setattr(
        values, "GROUP_MEMBERS_COMMAND", ("myid", "--groups", "{username}")
    )
    monkeypatch.setattr(
        values,
        "GROUP_ADD_COMMAND",
        ("myusermod", "--append", "{input_group}", "{username}"),
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        seen.append(list(command))
        if command[0] == "myid":
            return _FakeProc(0, f"{common_values.DESKTOP_USERNAME}\n")
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    assert task_module._ensure_input_group(timeout=30.0) == (True, None)
    assert seen == [
        ["myid", "--groups", common_values.DESKTOP_USERNAME],
        [
            "myusermod",
            "--append",
            values.INPUT_GROUP,
            common_values.DESKTOP_USERNAME,
        ],
    ]


def test_user_service_commands_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The state query and the enable of the user unit are values: another
    # program in the section is the argv the task runs through the user
    # manager of the desktop session.
    monkeypatch.setattr(
        values,
        "SERVICE_ACTIVE_COMMAND",
        (
            "mysystemctl",
            "--user",
            "is-active",
            "{service_unit_name}",
            "--machine",
            "{username}",
        ),
    )
    monkeypatch.setattr(
        values,
        "SERVICE_ENABLE_COMMAND",
        (
            "mysystemctl",
            "--user",
            "enable",
            "--now",
            "{service_unit_name}",
            "--machine",
            "{username}",
        ),
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        seen.append(list(command))
        if "is-active" in command:
            return _FakeProc(1, "inactive")
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    assert task_module._enable_user_service(timeout=30.0) == (True, None)
    assert seen == [
        [
            "mysystemctl",
            "--user",
            "is-active",
            values.SERVICE_UNIT_NAME,
            "--machine",
            common_values.DESKTOP_USERNAME,
        ],
        [
            "mysystemctl",
            "--user",
            "enable",
            "--now",
            values.SERVICE_UNIT_NAME,
            "--machine",
            common_values.DESKTOP_USERNAME,
        ],
    ]


def test_the_running_state_word_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The word that means "the unit runs" belongs to the answer of the
    # configured query: with another word in the module the same answer counts
    # as not running and the task enables the unit again.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        if "is-active" in command:
            return _FakeProc(0, "running")
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    monkeypatch.setattr(values, "SERVICE_ACTIVE_STATE", "running")
    assert task_module._enable_user_service(timeout=30.0) == (False, None)
    assert len(calls) == 1

    calls.clear()
    monkeypatch.setattr(values, "SERVICE_ACTIVE_STATE", "active")
    assert task_module._enable_user_service(timeout=30.0) == (True, None)
    assert len(calls) == 2


def test_vocalinux_setup_stays_out_of_the_quick_mode() -> None:
    # The dictation app downloads a release and a speech model, so the quick
    # set leaves it out while the desktop mode keeps it.
    catalog = tasks_values.CATALOG
    assert "vocalinux_setup" in task_catalog.default_tasks("desktop", catalog)
    assert "vocalinux_setup" not in task_catalog.default_tasks("fast_desktop", catalog)
