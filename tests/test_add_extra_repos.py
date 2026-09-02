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
from support import FakeProc as _FakeProc
from support import make_config, make_context

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


def _ctx(
    tmp_path: Path,
    *,
    skip_apt_update: bool = False,
    keep_downloaded_debs: bool = True,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        task_data_root=tmp_path,
        skip_apt_update=skip_apt_update,
        config=make_config(
            task_data_root=tmp_path,
            cli_tools_packages=("mc",),
            add_extra_repos_components=CONFIGURED,
            add_extra_repos_keep_downloaded_debs=keep_downloaded_debs,
            swapfile_path=tmp_path / "swapfile",
        ),
    )


def _install_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: dict[str, str],
) -> Path:
    """Point the task at temporary sources and keep-debs paths.

    The apt keep-debs drop-in is pre-created in its exact target state, so
    the component scenarios below never change it. Retention scenarios
    reset the drop-in state through _install_keep_debs.
    """

    sources_dir = tmp_path / "sources.list.d"
    sources_dir.mkdir()
    for name, content in files.items():
        (sources_dir / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(add_extra_repos, "SOURCES_LIST_D", sources_dir)
    legacy = tmp_path / "sources.list"
    monkeypatch.setattr(add_extra_repos, "LEGACY_SOURCES_FILE", legacy)
    _install_keep_debs(monkeypatch, tmp_path, create=True)
    return sources_dir


def _install_keep_debs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    create: bool,
) -> Path:
    """Point the task at a temporary apt keep-debs drop-in path.

    When create is true the drop-in already carries the exact target
    content; when false any existing drop-in is removed and the task must
    create it from scratch.
    """

    path = tmp_path / "apt.conf.d" / "99keep-debs.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    if create:
        path.write_text(add_extra_repos.APT_KEEP_DEBS_CONTENT, encoding="utf-8")
    elif path.exists():
        path.unlink()
    monkeypatch.setattr(add_extra_repos, "APT_KEEP_DEBS_FILE", path)
    return path


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


def _satisfied_ubuntu() -> str:
    """The Ubuntu sources with every configured component already listed."""

    return UBUNTU_DEB822.replace(
        "Components: main\n", "Components: main restricted universe multiverse\n"
    )


def test_keep_debs_dropin_created_even_when_sources_satisfied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Components are already satisfied but the keep-debs drop-in is missing:
    # the early satisfied return must not skip the drop-in step.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    keep_debs = _install_keep_debs(monkeypatch, tmp_path, create=False)
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert "already satisfied" in (result.message or "")
    assert "enabled" in (result.message or "")
    assert (
        keep_debs.read_text(encoding="utf-8")
        == add_extra_repos.APT_KEEP_DEBS_CONTENT
    )


def test_keep_debs_dropin_normalized_when_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An older drop-in with only the apt line is rewritten to the exact
    # content, which also carries the unattended-upgrades line.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    keep_debs = _install_keep_debs(monkeypatch, tmp_path, create=False)
    keep_debs.write_text('APT::Keep-Downloaded-Packages "true";\n', encoding="utf-8")
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert (
        keep_debs.read_text(encoding="utf-8")
        == add_extra_repos.APT_KEEP_DEBS_CONTENT
    )


def test_keep_debs_dropin_unchanged_when_exact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The drop-in already matches and the sources are satisfied: nothing
    # changes and the plain already-satisfied message is kept.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already satisfied"


def test_keep_debs_dropin_removed_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The config turns retention off: the existing drop-in is removed and
    # the result reports the disabled state.
    keep_debs = _install_keep_debs(monkeypatch, tmp_path, create=True)
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    result = add_extra_repos.task(_ctx(tmp_path, keep_downloaded_debs=False))
    assert result.success is True
    assert result.changed is True
    assert not keep_debs.exists()
    assert "disabled" in (result.message or "")


def test_keep_debs_dropin_write_error_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The drop-in path is a directory, so the write fails: the task reports
    # an error and never touches the sources.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": UBUNTU_DEB822})
    bad = tmp_path / "apt.conf.d" / "99keep-debs.conf"
    bad.unlink()
    bad.mkdir()
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is False
    assert "cannot update" in (result.error or "")
