"""Unit tests for the rustdesk_setup task.

All external resources (curl, rustdesk CLI, dpkg, apt-get, systemctl)
are mocked via monkeypatch; the tests never touch the real system
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import Config, RustdeskOptionConfig, load_config
from pyntara.context import Context
from pyntara.tasks import rustdesk_setup

# The real catalog and config from the repository; the mode-membership
# and dependency tests use them so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks

RELEASE_TAG = "1.4.9"
ASSET_NAME = f"rustdesk-{RELEASE_TAG}-x86_64.deb"
MACHINE_ID = "12345678"

PASSWORD_WORDS_RE = re.compile(r"^[a-z]{5}( [a-z]{5}){5}$")


def _release_json(tag: str = RELEASE_TAG, arch: str = "x86_64") -> str:
    """The GitHub latest-release payload with one matching deb asset."""

    release = {
        "tag_name": tag,
        "assets": [
            {
                "name": f"rustdesk-{tag}-{arch}.deb",
                "browser_download_url": f"https://example.com/rustdesk-{tag}-{arch}.deb",
            }
        ],
    }
    return json.dumps(release)


class _FakeEntry:
    """One fake KeePass entry for the fake vault."""

    def __init__(self, password: str = "") -> None:
        self.password = password
        self.username = ""
        self.url = None
        self.notes = ""


class _FakeVault:
    """A fake runtime vault: one entry, saved path recorded."""

    def __init__(self, password: str | None = None) -> None:
        self.root_group = object()
        self._entry = _FakeEntry(password) if password is not None else None
        self.saved_to: str | None = None

    def find_entries(self, **kwargs: object) -> _FakeEntry | None:
        return self._entry

    def add_entry(
        self,
        group: object,
        title: str,
        username: str,
        password: str,
        notes: str | None = None,
    ) -> None:
        self._entry = _FakeEntry(password)

    def save(self, filename: str | None = None) -> None:
        self.saved_to = filename


def _vault(monkeypatch: pytest.MonkeyPatch, password: str | None = None) -> _FakeVault:
    """Install the fake vault as the runtime vault opener; return it."""

    fake = _FakeVault(password)
    monkeypatch.setattr(
        rustdesk_setup.metrics, "open_runtime_vault", lambda cfg: fake
    )
    return fake


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed_version: str | None = RELEASE_TAG,
    machine_id: str = MACHINE_ID,
    dpkg_arch: str = "amd64",
    release_payload: str | None = None,
    option_values: dict[str, str] | None = None,
    service_enabled: bool = True,
    service_active: bool = True,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl answers the releases API with release_payload and succeeds on
    downloads; rustdesk --version, --get-id and the --option get/set
    paths answer from the given values; dpkg prints the architecture;
    systemctl reports the given service states; apt-get and every other
    command succeed. A nonzero return with check=True raises exactly
    like the real subprocess.run.
    """

    calls: list[list[str]] = []
    values = dict(option_values or {})
    current_installed = installed_version

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal current_installed
        calls.append(list(command))
        cmd = list(command)
        rc = 0
        stdout = ""
        if cmd[0] == "curl" and "releases/latest" in " ".join(cmd):
            stdout = release_payload if release_payload is not None else _release_json()
        elif cmd[0] == "curl":
            pass  # the deb download succeeds
        elif cmd[0] == "rustdesk" and cmd[1] == "--version":
            stdout = f"{current_installed}\n" if current_installed else ""
            rc = 0 if current_installed else 1
        elif cmd[0] == "rustdesk" and cmd[1] == "--get-id":
            stdout = f"{machine_id}\n"
        elif cmd[0] == "rustdesk" and cmd[1] == "--option":
            if len(cmd) == 3:
                stdout = f"{values.get(cmd[2], '')}\n"
            else:
                values[cmd[2]] = cmd[3]
        elif cmd[0] == "rustdesk" and cmd[1] == "--password":
            pass
        elif cmd[0] == "dpkg" and cmd[1] == "--print-architecture":
            stdout = f"{dpkg_arch}\n"
        elif cmd[0] == "systemctl" and cmd[1] == "is-enabled":
            stdout = "enabled\n" if service_enabled else "disabled\n"
            rc = 0 if service_enabled else 1
        elif cmd[0] == "systemctl" and cmd[1] == "is-active":
            stdout = "active\n" if service_active else "inactive\n"
            rc = 0 if service_active else 1
        elif cmd[0] == "systemctl":
            pass  # enable, start, stop succeed
        elif cmd[0] == "apt-get":
            pass  # update and install succeed
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout)
        return _FakeProc(rc, stdout)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _config(*, tmp_path: Path, options: tuple = ()) -> Config:
    return make_config(
        rustdesk_download_dir=tmp_path / "download",
        rustdesk_id_file_path=tmp_path / "rustdesk_id",
        rustdesk_config_dir=tmp_path / "rustdesk-config",
        rustdesk_options=options,
    )


def _ctx(*, tmp_path: Path, config: Config, force: bool = False) -> Context:
    return make_context(
        config=config,
        force_tasks=frozenset({"rustdesk_setup"}) if force else frozenset(),
        skip_apt_update=True,
    )


def test_rustdesk_setup_in_desktop_and_server_modes() -> None:
    # Remote control makes sense on interactive machines; the minimal
    # mode has no desktop session to capture.
    for mode in ("desktop", "server"):
        assert "rustdesk_setup" in task_catalog.default_tasks(mode, REAL_TASKS)
    assert "rustdesk_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)


def test_rustdesk_setup_depends_on_local_vault_setup() -> None:
    # The permanent password lives in the runtime vault, so the vault
    # must exist before the task runs.
    task_def = task_catalog.by_name("rustdesk_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("local_vault_setup",)


def test_real_config_keeps_public_server_defaults() -> None:
    # The whole point of the global-ID setup: no custom server options,
    # so the machine registers with the public RustDesk server and every
    # default client reaches it by ID alone.
    config = load_config(REPO_ROOT / "config")
    keys = [option.key for option in config.rustdesk_setup.options]
    assert "custom-rendezvous-server" not in keys
    assert "relay-server" not in keys
    assert "key" not in keys
    assert "enable-udp-punch" in keys
    assert "direct-server" in keys


def test_installed_latest_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    config.rustdesk_setup.id_file_path.parent.mkdir(parents=True, exist_ok=True)
    config.rustdesk_setup.id_file_path.write_text(f"{MACHINE_ID}\n", encoding="utf-8")
    calls = _fake_run(monkeypatch, installed_version=RELEASE_TAG)
    _vault(monkeypatch, password="kofub vifuf midot nudog zodum hobir")
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert result.changed is False
    # the release lookup runs, but no download or install happens
    assert not any(call[0] == "curl" and "--output" in call for call in calls)
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    calls = _fake_run(monkeypatch, installed_version=None)
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "apt-get" and call[1] == "install" for call in calls)
    assert not (config.rustdesk_setup.download_dir / ASSET_NAME).exists()


def test_no_asset_for_unknown_architecture_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    _fake_run(
        monkeypatch,
        installed_version=None,
        dpkg_arch="s390x",
        release_payload=_release_json(),
    )
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is False
    assert "no rustdesk deb asset" in (result.error or "")


def test_applies_options_idempotently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    option = RustdeskOptionConfig(key="enable-udp-punch", value="Y")
    config = _config(tmp_path=tmp_path, options=(option,))
    calls = _fake_run(
        monkeypatch, option_values={"enable-udp-punch": "Y"}
    )
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    # the option was only read, never written
    assert not any(
        call[0] == "rustdesk" and call[1] == "--option" and len(call) == 4
        for call in calls
    )


def test_sets_missing_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    option = RustdeskOptionConfig(key="enable-udp-punch", value="Y")
    config = _config(tmp_path=tmp_path, options=(option,))
    calls = _fake_run(monkeypatch, option_values={"enable-udp-punch": ""})
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert result.changed is True
    assert ["rustdesk", "--option", "enable-udp-punch", "Y"] in calls


def test_generates_and_stores_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    calls = _fake_run(monkeypatch)
    fake = _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert fake.saved_to is not None
    assert fake._entry is not None
    assert PASSWORD_WORDS_RE.match(fake._entry.password)
    # the password was applied through rustdesk --password
    assert any(
        call[0] == "rustdesk" and call[1] == "--password" for call in calls
    )


def test_reuses_stored_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    stored = "kofub vifuf midot nudog zodum hobir"
    calls = _fake_run(monkeypatch)
    fake = _vault(monkeypatch, password=stored)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert fake._entry is not None
    assert fake._entry.password == stored
    assert ["rustdesk", "--password", stored] in calls


def test_force_regenerates_password_and_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    identity_dir = config.rustdesk_setup.config_dir
    identity_dir.mkdir(parents=True)
    identity = identity_dir / "RustDesk.toml"
    identity.write_text("old identity", encoding="utf-8")
    calls = _fake_run(monkeypatch)
    fake = _vault(monkeypatch, password="old password words")
    result = rustdesk_setup.task(
        _ctx(tmp_path=tmp_path, config=config, force=True)
    )
    assert result.success is True
    assert not identity.exists()
    assert fake._entry is not None
    assert fake._entry.password != "old password words"
    assert any(
        call == ["systemctl", "stop", config.rustdesk_setup.service_unit_name]
        for call in calls
    )


def test_writes_machine_id_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    _fake_run(monkeypatch, machine_id=MACHINE_ID)
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert config.rustdesk_setup.id_file_path.read_text(encoding="utf-8").strip() == (
        MACHINE_ID
    )
    assert config.rustdesk_setup.id_file_path.stat().st_mode & 0o777 == 0o644


def test_id_file_stable_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    config.rustdesk_setup.id_file_path.parent.mkdir(parents=True, exist_ok=True)
    config.rustdesk_setup.id_file_path.write_text(f"{MACHINE_ID}\n", encoding="utf-8")
    before = config.rustdesk_setup.id_file_path.read_text(encoding="utf-8")
    _fake_run(monkeypatch, machine_id=MACHINE_ID)
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    # a matching ID file is left exactly as it was
    assert config.rustdesk_setup.id_file_path.read_text(encoding="utf-8") == before


def test_vault_unavailable_warns_without_changing_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path=tmp_path)
    calls = _fake_run(monkeypatch)
    monkeypatch.setattr(
        rustdesk_setup.metrics, "open_runtime_vault", lambda cfg: None
    )
    result = rustdesk_setup.task(_ctx(tmp_path=tmp_path, config=config))
    assert result.success is True
    assert result.warnings
    assert "runtime vault unavailable" in result.warnings[0]
    assert not any(
        call[0] == "rustdesk" and call[1] == "--password" for call in calls
    )
