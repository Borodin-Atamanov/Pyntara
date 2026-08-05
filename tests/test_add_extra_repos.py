"""Unit tests for the add_extra_repos task.

The task reads apt source files from module-level path constants and runs
apt-get through run_command; both are monkeypatched so the tests only touch
temporary fixtures (docs/guides/developer-guide.md). The fixtures mirror
the real files on a Kubuntu system, including comments and Signed-By lines.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyntara.config import AddExtraReposConfig, CliToolsConfig, Config, EngineConfig
from pyntara.context import Context
from pyntara.tasks import add_extra_repos

# Two Ubuntu sections (base and security) with only main enabled, as on a
# fresh Kubuntu before this task runs.
UBUNTU_DEB822 = """\
# Modernized from /etc/apt/sources.list
Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: resolute
Components: main
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# Modernized from /etc/apt/sources.list
Types: deb
URIs: http://security.ubuntu.com/ubuntu/
Suites: resolute-security
Components: main
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""

THIRD_PARTY_DEB822 = """\
Types: deb
URIs: https://dl.google.com/linux/chrome/deb/
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/google-chrome.gpg
"""

CONFIGURED = ("universe", "restricted", "multiverse")


def _ctx(tmp_path: Path, *, skip_apt_update: bool = False) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=skip_apt_update,
        config=Config(
            engine=EngineConfig(
                task_data_root=tmp_path,
                notice_timeout=7,
                command_timeout_seconds=1800,
                process_check_timeout_seconds=5,
            ),
            cli_tools=CliToolsConfig(
                packages=("mc",),
                package_status_timeout_seconds=30,
                package_install_retries=3,
                package_success_threshold_percent=70,
            ),
            add_extra_repos=AddExtraReposConfig(components=CONFIGURED),
        ),
    )


def _install_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: dict[str, str],
) -> Path:
    """Point the task at a temporary sources directory with the given files."""

    sources_dir = tmp_path / "sources.list.d"
    sources_dir.mkdir()
    for name, content in files.items():
        (sources_dir / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(add_extra_repos, "SOURCES_LIST_D", sources_dir)
    legacy = tmp_path / "sources.list"
    monkeypatch.setattr(add_extra_repos, "LEGACY_SOURCES_FILE", legacy)
    return sources_dir


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every subprocess call made through run_command."""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_already_satisfied_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Every Ubuntu section already lists every configured component: the
    # task skips and never touches apt.
    satisfied = UBUNTU_DEB822.replace(
        "Components: main\n", "Components: main restricted universe multiverse\n"
    )
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": satisfied})
    calls = _record_calls(monkeypatch)
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already satisfied"
    assert not calls


def test_appends_missing_components(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Only main is enabled: the task appends the missing components to both
    # Ubuntu sections, keeps everything else and refreshes the index once.
    sources_dir = _install_sources(
        monkeypatch, tmp_path, {"ubuntu.sources": UBUNTU_DEB822}
    )
    calls = _record_calls(monkeypatch)
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    text = (sources_dir / "ubuntu.sources").read_text(encoding="utf-8")
    assert text.count("Components: main universe restricted multiverse") == 2
    assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in text
    updates = [call for call in calls if call[0] == "apt-get" and call[1] == "update"]
    assert updates == [["apt-get", "update"]]


def test_preserves_third_party_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A third-party source (google chrome) is never touched: only the
    # Ubuntu archive file is rewritten.
    sources_dir = _install_sources(
        monkeypatch,
        tmp_path,
        {"ubuntu.sources": UBUNTU_DEB822, "google-chrome.sources": THIRD_PARTY_DEB822},
    )
    _record_calls(monkeypatch)
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    third_party = (sources_dir / "google-chrome.sources").read_text(encoding="utf-8")
    assert third_party == THIRD_PARTY_DEB822
    ubuntu = (sources_dir / "ubuntu.sources").read_text(encoding="utf-8")
    assert "Components: main universe restricted multiverse" in ubuntu


def test_skip_apt_update_skips_index_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # skip_apt_update=True disables the index refresh: the files are still
    # rewritten, but apt-get update is never called.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": UBUNTU_DEB822})
    calls = _record_calls(monkeypatch)
    result = add_extra_repos.task(_ctx(tmp_path, skip_apt_update=True))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "apt-get" for call in calls)


def test_legacy_sources_list_is_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A legacy /etc/apt/sources.list with Ubuntu deb lines gets the missing
    # components appended, with the trailing comment preserved.
    legacy = tmp_path / "sources.list"
    legacy.write_text(
        "deb http://archive.ubuntu.com/ubuntu/ resolute main\n"
        "deb http://security.ubuntu.com/ubuntu/ resolute-security main # security\n",
        encoding="utf-8",
    )
    _install_sources(monkeypatch, tmp_path, {})
    _record_calls(monkeypatch)
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    text = legacy.read_text(encoding="utf-8")
    assert "deb http://archive.ubuntu.com/ubuntu/ resolute main universe restricted multiverse" in text
    assert (
        "deb http://security.ubuntu.com/ubuntu/ resolute-security main universe "
        "restricted multiverse # security" in text
    )


def test_no_ubuntu_section_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Only a third-party source exists: there is no Ubuntu archive section
    # to manage, so the task reports an error and changes nothing.
    _install_sources(monkeypatch, tmp_path, {"google-chrome.sources": THIRD_PARTY_DEB822})
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is False
    assert "no Ubuntu archive section found" in (result.error or "")


def test_apt_update_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed index refresh is not fatal: the components are in place, the
    # task succeeds and the refresh failure is reported as a warning.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "apt-get" and command[1] == "update":
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": UBUNTU_DEB822})
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert "apt index refresh" in (result.message or "")


def test_ubuntu_section_without_components_line_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An Ubuntu section without a Components line cannot be repaired by
    # rewriting: the task reports the problem and changes nothing.
    broken = UBUNTU_DEB822.replace("Components: main\n", "")
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": broken})
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is False
    assert "without a Components line" in (result.error or "")


def test_unreadable_source_file_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory masquerading as a source file cannot be read: the task
    # reports the read error and changes nothing.
    sources_dir = _install_sources(monkeypatch, tmp_path, {})
    (sources_dir / "broken.sources").mkdir()
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is False
    assert "cannot read" in (result.error or "")


def test_legacy_and_deb822_are_both_updated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Ubuntu sections in both the legacy file and the deb822 file are
    # updated in one run.
    legacy = tmp_path / "sources.list"
    legacy.write_text(
        "deb http://archive.ubuntu.com/ubuntu/ resolute main\n", encoding="utf-8"
    )
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": UBUNTU_DEB822})
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert "main universe restricted multiverse" in legacy.read_text(encoding="utf-8")
    ubuntu_text = (tmp_path / "sources.list.d" / "ubuntu.sources").read_text(
        encoding="utf-8"
    )
    assert "Components: main universe restricted multiverse" in ubuntu_text
