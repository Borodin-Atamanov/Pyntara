"""Unit tests for the imagemagick_setup task.

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
from pyntara.tasks import imagemagick_setup
from pyntara.values import imagemagick_setup as imagemagick_values
from pyntara.values import tasks as tasks_values

# Package set used by the tests; mirrors the real config but stays small.
TEST_PACKAGES = ("imagemagick",)

# The real catalog from the values package; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = tasks_values.CATALOG

POLICY_CONTENT = (
    '<policymap><policy domain="resource" name="memory" value="128GiB"/></policymap>'
)

# Clone root the policy fixture uses: _policy_env writes the template under
# it, and _ctx hands it to the task through the Context.
_FIXTURE_REPO: Path | None = None


def _policy_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    template_file_name: str = "policy.xml",
) -> Path:
    """Point the template and the policy target at tmp; return the target.

    The fixture clone carries the policy template under
    task_data/imagemagick_setup/, and the target policy file lives in the
    tmp tree so the real /etc is never touched.
    """

    global _FIXTURE_REPO
    repo = tmp_path / "repo"
    template_dir = repo / "task_data" / "imagemagick_setup"
    template_dir.mkdir(parents=True)
    (template_dir / template_file_name).write_text(POLICY_CONTENT, encoding="utf-8")
    _FIXTURE_REPO = repo
    return tmp_path / "policy.xml"


def _ctx(*, skip_apt_update: bool = False) -> Context:
    """Context of the task; the values come from the values module."""

    return make_context(
        task_name="imagemagick_setup",
        repo_root=_FIXTURE_REPO or REPO_ROOT,
        skip_apt_update=skip_apt_update,
    )


def _use_imagemagick_values(
    monkeypatch: pytest.MonkeyPatch,
    policy_path: Path,
    *,
    template_file_name: str = "policy.xml",
    backup_file_suffix: str = ".bak",
) -> None:
    """Point the task values at the fixture tree and the temporary file.

    The values are module constants, so a test patches the module for its
    own duration and monkeypatch restores the shipped values afterwards.
    """

    monkeypatch.setattr(imagemagick_values, "PACKAGES", TEST_PACKAGES)
    monkeypatch.setattr(imagemagick_values, "POLICY_PATH", policy_path)
    monkeypatch.setattr(
        imagemagick_values, "POLICY_TEMPLATE_FILE_NAME", template_file_name
    )
    monkeypatch.setattr(
        imagemagick_values, "POLICY_BACKUP_FILE_SUFFIX", backup_file_suffix
    )


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: set[str],
    install_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, apt-get install answers
    with install_rc and every other command succeeds; all calls are
    recorded. A nonzero return with check=True raises exactly like the
    real subprocess.run, so install failures surface as exceptions.
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
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout)
        return _FakeProc(rc, stdout)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_imagemagick_setup_is_in_every_mode_default_set() -> None:
    for mode in tasks_values.MODES:
        assert "imagemagick_setup" in task_catalog.default_tasks(mode, REAL_TASKS)


def test_imagemagick_setup_depends_on_add_extra_repos() -> None:
    # imagemagick lives in universe, so add_extra_repos is a hard
    # dependency, the same as cli_tools_lite_setup.
    task_def = task_catalog.by_name("imagemagick_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_the_shipped_values_name_the_meta_package() -> None:
    # The shipped values must name the real meta package imagemagick, not a
    # virtual name, so dpkg-query sees it as installed.
    assert "imagemagick" in imagemagick_values.PACKAGES


def test_all_installed_skips_apt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text(POLICY_CONTENT, encoding="utf-8")
    _use_imagemagick_values(monkeypatch, policy_path)
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text(POLICY_CONTENT, encoding="utf-8")
    _use_imagemagick_values(monkeypatch, policy_path)
    calls = _install_fake(monkeypatch, installed=set())
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "imagemagick" in (result.message or "")
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert len(update_calls) == 1
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "imagemagick"]]


def test_skip_apt_update_skips_the_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text(POLICY_CONTENT, encoding="utf-8")
    calls = _install_fake(monkeypatch, installed=set())
    _use_imagemagick_values(monkeypatch, policy_path)
    result = imagemagick_setup.task(_ctx(skip_apt_update=True))
    assert result.success is True
    assert result.changed is True
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert update_calls == []


def test_install_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The package install fails: the task reports the reason and still
    # deploys the policy it owns.
    policy_path = _policy_env(monkeypatch, tmp_path)
    _install_fake(monkeypatch, installed=set(), install_rc=1)
    _use_imagemagick_values(monkeypatch, policy_path)
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert any("failed to install" in warning for warning in result.warnings)
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT


def test_policy_written_and_backed_up_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text("package original policy", encoding="utf-8")
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    _use_imagemagick_values(monkeypatch, policy_path)
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT
    backup = policy_path.with_name(f"{policy_path.name}.bak")
    assert backup.read_text(encoding="utf-8") == "package original policy"
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert backup.read_text(encoding="utf-8") == "package original policy"


def test_policy_created_when_target_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    _use_imagemagick_values(monkeypatch, policy_path)
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT
    assert not policy_path.with_name(f"{policy_path.name}.bak").exists()


def test_policy_backup_never_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text("changed system policy", encoding="utf-8")
    backup = policy_path.with_name(f"{policy_path.name}.bak")
    backup.write_text("original backup", encoding="utf-8")
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    _use_imagemagick_values(monkeypatch, policy_path)
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT
    assert backup.read_text(encoding="utf-8") == "original backup"


def test_policy_template_file_name_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The fixture clone carries only the template name the values give, so
    # a name written in the code could not find a template at all.
    policy_path = _policy_env(monkeypatch, tmp_path, template_file_name="tuned.xml")
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    _use_imagemagick_values(monkeypatch, policy_path, template_file_name="tuned.xml")
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT


def test_policy_backup_file_suffix_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another suffix in the values is the name of the single backup the
    # task writes next to the system policy.
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text("package original policy", encoding="utf-8")
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    _use_imagemagick_values(monkeypatch, policy_path, backup_file_suffix=".orig")
    result = imagemagick_setup.task(_ctx())
    assert result.success is True
    backup = policy_path.with_name(f"{policy_path.name}.orig")
    assert backup.read_text(encoding="utf-8") == "package original policy"
    assert not policy_path.with_name(f"{policy_path.name}.bak").exists()
