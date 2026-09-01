"""Unit tests for the imagemagick_setup task.

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
from pyntara.tasks import imagemagick_setup

# Package set used by the tests; mirrors the real config but stays small.
TEST_PACKAGES = ("imagemagick",)

# The real catalog from the repository config; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks

POLICY_CONTENT = (
    '<policymap><policy domain="resource" name="memory" value="128GiB"/></policymap>'
)


def _policy_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the template and the policy target at tmp; return the target.

    REPO_ROOT is monkeypatched to a fixture clone that carries the policy
    template under task_data/imagemagick_setup/, and the target policy file
    lives in the tmp tree so the real /etc is never touched.
    """

    repo = tmp_path / "repo"
    template_dir = repo / "task_data" / "imagemagick_setup"
    template_dir.mkdir(parents=True)
    (template_dir / "policy.xml").write_text(POLICY_CONTENT, encoding="utf-8")
    monkeypatch.setattr(imagemagick_setup, "REPO_ROOT", repo)
    return tmp_path / "policy.xml"


def _test_config(policy_path: Path) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return make_config(
        imagemagick_setup_packages=TEST_PACKAGES,
        imagemagick_setup_policy_path=policy_path,
    )


def _ctx(*, skip_apt_update: bool = False, policy_path: Path) -> Context:
    return make_context(
        config=_test_config(policy_path), skip_apt_update=skip_apt_update
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
    for mode in MODES:
        assert "imagemagick_setup" in task_catalog.default_tasks(mode, REAL_TASKS)


def test_imagemagick_setup_depends_on_add_extra_repos() -> None:
    # imagemagick lives in universe, so add_extra_repos is a hard
    # dependency, the same as cli_tools.
    task_def = task_catalog.by_name("imagemagick_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_real_config_names_the_meta_package() -> None:
    # The real config must name the real meta package imagemagick, not a
    # virtual name, so dpkg-query sees it as installed.
    config = load_config(REPO_ROOT / "config")
    assert "imagemagick" in config.imagemagick_setup.packages


def test_all_installed_skips_apt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text(POLICY_CONTENT, encoding="utf-8")
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text(POLICY_CONTENT, encoding="utf-8")
    calls = _install_fake(monkeypatch, installed=set())
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
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
    result = imagemagick_setup.task(
        _ctx(skip_apt_update=True, policy_path=policy_path)
    )
    assert result.success is True
    assert result.changed is True
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert update_calls == []


def test_install_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    _install_fake(monkeypatch, installed=set(), install_rc=1)
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
    assert result.success is False
    assert "failed to install" in (result.error or "")


def test_policy_written_and_backed_up_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    policy_path.write_text("package original policy", encoding="utf-8")
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
    assert result.success is True
    assert result.changed is True
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT
    backup = policy_path.with_name(f"{policy_path.name}.bak")
    assert backup.read_text(encoding="utf-8") == "package original policy"
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
    assert result.success is True
    assert result.changed is False
    assert backup.read_text(encoding="utf-8") == "package original policy"


def test_policy_created_when_target_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = _policy_env(monkeypatch, tmp_path)
    _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
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
    result = imagemagick_setup.task(_ctx(policy_path=policy_path))
    assert result.success is True
    assert result.changed is True
    assert policy_path.read_text(encoding="utf-8") == POLICY_CONTENT
    assert backup.read_text(encoding="utf-8") == "original backup"
