"""Unit tests for the add_extra_repos task.

The task reads the apt source files, the keep-debs file and the body it
carries from pyntara.values.add_extra_repos, and runs apt-get through
run_command; subprocess is monkeypatched, so the tests only touch temporary
fixtures (docs/guides/developer-guide.md). The fixtures mirror the real files
on a Kubuntu system, including comments and Signed-By lines.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara.context import Context
from pyntara.tasks import add_extra_repos
from pyntara.values import add_extra_repos as values

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

# The two drop-in bodies the task must write, spelled out here: a test that
# compared the file with the shipped text alone would follow a wrong value.
KEEP_DEBS_TRUE_BODY = (
    "# Written by pyntara add_extra_repos\n"
    'APT::Keep-Downloaded-Packages "true";\n'
    'Unattended-Upgrade::Keep-Debs-After-Install "true";\n'
)
KEEP_DEBS_FALSE_BODY = (
    "# Written by pyntara add_extra_repos\n"
    'APT::Keep-Downloaded-Packages "false";\n'
    'Unattended-Upgrade::Keep-Debs-After-Install "false";\n'
)

# Another drop-in template the proof test puts into the values module; the task
# must write exactly what that template renders, never a value of its own.
OTHER_KEEP_DEBS_BODY = 'APT::Keep-Downloaded-Packages "true";\n'


def _keep_debs_body(keep_downloaded_debs: bool) -> str:
    """The drop-in body the task must write for one mode."""

    return KEEP_DEBS_TRUE_BODY if keep_downloaded_debs else KEEP_DEBS_FALSE_BODY


@pytest.fixture(autouse=True)
def _point_the_values_at_temporary_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file temporary apt source and drop-in paths.

    The three paths are values of the task, so the fixture points them at the
    temporary directory of the test and the shipped values come back
    afterwards. The paths are the same in every test, which keeps the call
    sites of _ctx free of them.
    """

    monkeypatch.setattr(values, "LEGACY_SOURCES_FILE", tmp_path / "sources.list")
    monkeypatch.setattr(values, "SOURCES_LIST_D", tmp_path / "sources.list.d")
    monkeypatch.setattr(
        values, "KEEP_DEBS_FILE", tmp_path / "apt.conf.d" / "99keep-debs.conf"
    )


def _ctx(
    tmp_path: Path,
    *,
    skip_apt_update: bool = False,
    delete_packages_after_install: bool = False,
) -> Context:
    """Context safe for unit tests; the real files are never touched.

    The deletion of the downloads is off by default here, because the
    component scenarios pre-create the keep-debs drop-in and must leave it
    alone; the scenarios about the drop-in set the flag themselves.
    """

    return make_context(
        task_data_root=tmp_path,
        skip_apt_update=skip_apt_update,
        delete_packages_after_install=delete_packages_after_install,
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
        path.write_text(_keep_debs_body(True), encoding="utf-8")
    elif path.exists():
        path.unlink()
    return path


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every subprocess call made through run_command."""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_already_satisfied_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_appends_missing_components(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert (
        "deb http://archive.ubuntu.com/ubuntu/ resolute main universe restricted multiverse"
        in text
    )
    assert (
        "deb http://security.ubuntu.com/ubuntu/ resolute-security main universe "
        "restricted multiverse # security" in text
    )


def test_the_line_keywords_and_schemes_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The keywords that open a one-line source line and the schemes that
    # mark its archive URI belong to the format of the foreign file: with
    # another keyword and another scheme in the values module the task
    # rewrites the line of that format and leaves the shipped one alone.
    legacy = tmp_path / "sources.list"
    legacy.write_text(
        "repo mirror://archive.ubuntu.com/ubuntu/ resolute main\n"
        "deb http://archive.ubuntu.com/ubuntu/ resolute main\n",
        encoding="utf-8",
    )
    _install_sources(monkeypatch, tmp_path, {})
    _record_calls(monkeypatch)
    monkeypatch.setattr(values, "LEGACY_SOURCE_TYPE_KEYWORDS", ("repo ",))
    monkeypatch.setattr(values, "SOURCE_URL_SCHEMES", ("mirror://",))
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    text = legacy.read_text(encoding="utf-8")
    assert (
        "repo mirror://archive.ubuntu.com/ubuntu/ resolute main universe "
        "restricted multiverse" in text
    )
    assert "deb http://archive.ubuntu.com/ubuntu/ resolute main\n" in text


def test_no_ubuntu_section_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only a third-party source exists: there is no Ubuntu archive section
    # to manage, so the task reports the reason and changes nothing.
    _install_sources(
        monkeypatch, tmp_path, {"google-chrome.sources": THIRD_PARTY_DEB822}
    )
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert any(
        "no Ubuntu archive section found" in warning for warning in result.warnings
    )


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


def test_ubuntu_section_without_components_line_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An Ubuntu section without a Components line cannot be repaired by
    # rewriting: the task reports the problem and changes nothing.
    broken = UBUNTU_DEB822.replace("Components: main\n", "")
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": broken})
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert any("without a Components line" in warning for warning in result.warnings)


def test_unreadable_source_file_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory masquerading as a source file cannot be read: the task
    # reports the read error and changes nothing.
    sources_dir = _install_sources(monkeypatch, tmp_path, {})
    (sources_dir / "broken.sources").mkdir()
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert any("cannot read" in warning for warning in result.warnings)


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


def test_the_deb822_field_names_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The two deb822 field names the task reads are values: a file that
    # spells them differently is still recognized and rewritten, and the
    # field the task reads stays the value of the module.
    renamed = (
        "Types: deb\n"
        "Archive-URIs: http://archive.ubuntu.com/ubuntu/\n"
        "Suites: resolute\n"
        "Parts: main\n"
    )
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": renamed})
    monkeypatch.setattr(values, "URIS_FIELD_NAME", "archive-uris:")
    monkeypatch.setattr(values, "COMPONENTS_FIELD_NAME", "parts:")
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    text = (tmp_path / "sources.list.d" / "ubuntu.sources").read_text(encoding="utf-8")
    assert "Parts: main universe restricted multiverse\n" in text


def test_the_source_file_suffixes_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The two extensions apt reads in the sources directory are values:
    # with another deb822 suffix the task rewrites the file that carries
    # it, while the shipped .sources name is not an apt source and is left
    # alone.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.apt": UBUNTU_DEB822})
    monkeypatch.setattr(values, "DEB822_SOURCE_SUFFIX", ".apt")
    monkeypatch.setattr(values, "LEGACY_SOURCE_SUFFIX", ".sources")
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    text = (tmp_path / "sources.list.d" / "ubuntu.apt").read_text(encoding="utf-8")
    assert text.count("Components: main universe restricted multiverse") == 2

    shipped_dir = tmp_path / "shipped"
    shipped_dir.mkdir()
    (shipped_dir / "ubuntu.apt").write_text(UBUNTU_DEB822, encoding="utf-8")
    # Back to the shipped suffixes: a .apt file is then no apt source at
    # all, which is what the second half proves.
    monkeypatch.setattr(values, "DEB822_SOURCE_SUFFIX", ".sources")
    monkeypatch.setattr(values, "LEGACY_SOURCE_SUFFIX", ".list")
    monkeypatch.setattr(values, "SOURCES_LIST_D", shipped_dir)
    untouched = add_extra_repos.task(_ctx(shipped_dir))
    assert untouched.success is True
    assert "no apt source files found" in untouched.warnings
    assert (shipped_dir / "ubuntu.apt").read_text(encoding="utf-8") == UBUNTU_DEB822


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
    assert keep_debs.read_text(encoding="utf-8") == _keep_debs_body(True)


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
    assert keep_debs.read_text(encoding="utf-8") == _keep_debs_body(True)


def test_keep_debs_body_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another body in the values module is the body the task writes, so the
    # drop-in content is a value and nothing else in the task holds it.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    keep_debs = _install_keep_debs(monkeypatch, tmp_path, create=False)
    monkeypatch.setattr(
        values, "KEEP_DEBS_DROPIN_TEMPLATE", OTHER_KEEP_DEBS_BODY
    )
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert keep_debs.read_text(encoding="utf-8") == OTHER_KEEP_DEBS_BODY


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


def test_keep_debs_dropin_switched_to_delete_when_the_run_deletes_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The run deletes the downloads: the existing drop-in is rewritten with the
    # answer of this run, so apt stops keeping the packages it downloaded, and
    # the result reports the disabled state.
    keep_debs = _install_keep_debs(monkeypatch, tmp_path, create=True)
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    result = add_extra_repos.task(
        _ctx(tmp_path, delete_packages_after_install=True)
    )
    assert result.success is True
    assert result.changed is True
    assert keep_debs.read_text(encoding="utf-8") == _keep_debs_body(False)
    assert "disabled" in (result.message or "")


def test_keep_debs_dropin_written_when_the_run_deletes_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The run deletes the downloads and no drop-in exists: the task writes the
    # answer of this run, because an absent file leaves apt on its own default,
    # which is to keep every downloaded package.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": _satisfied_ubuntu()})
    keep_debs = _install_keep_debs(monkeypatch, tmp_path, create=False)
    result = add_extra_repos.task(
        _ctx(tmp_path, delete_packages_after_install=True)
    )
    assert result.success is True
    assert result.changed is True
    assert keep_debs.read_text(encoding="utf-8") == _keep_debs_body(False)
    assert "disabled" in (result.message or "")


def test_keep_debs_dropin_write_error_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The drop-in path is a directory, so the write fails: the task reports
    # the reason and still handles the apt sources.
    _install_sources(monkeypatch, tmp_path, {"ubuntu.sources": UBUNTU_DEB822})
    bad = tmp_path / "apt.conf.d" / "99keep-debs.conf"
    bad.unlink()
    bad.mkdir()
    result = add_extra_repos.task(_ctx(tmp_path))
    assert result.success is True
    assert any("cannot update" in warning for warning in result.warnings)
    assert result.changed is True
