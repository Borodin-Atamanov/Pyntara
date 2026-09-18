"""Unit tests for the rustdesk_setup task.

All external resources (curl, rustdesk CLI, dpkg, apt-get, systemctl)
are mocked via monkeypatch; the tests never touch the real system
(docs/guides/developer-guide.md). The download directory, the ID file, the
client configuration directory and the option list are values, so one autouse
fixture points them at the temporary directory of the test.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import rustdesk_setup
from pyntara.utils import curl_flags
from pyntara.values import engine as engine_values
from pyntara.values import rustdesk_setup as values
from pyntara.values import tasks as tasks_values

# The real catalog from the values package; the mode-membership and
# dependency tests use it so they cover the actual task set.
REAL_TASKS = tasks_values.CATALOG

RELEASE_TAG = "1.4.9"
ASSET_NAME = f"rustdesk-{RELEASE_TAG}-x86_64.deb"
MACHINE_ID = "12345678"

PASSWORD_WORDS_RE = re.compile(r"^[a-z]{5}( [a-z]{5}){5}$")

# The shipped option list as it is declared, captured at import time: the two
# tests that check the values a real run reads need it, while the autouse fixture
# of this file replaces the list for the tests that drive the task.
SHIPPED_OPTIONS = values.OPTIONS


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

    def __init__(self, password: str = "", username: str = "") -> None:
        self.password = password
        self.username = username
        self.url = None
        self.notes = ""


class _FakeVault:
    """A fake runtime vault: one entry, saved path recorded."""

    def __init__(self, password: str | None = None, username: str = "") -> None:
        self.root_group = object()
        self._entry = _FakeEntry(password, username) if password is not None else None
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
        self._entry = _FakeEntry(password, username)
        self._entry.notes = notes or ""

    def save(self, filename: str | None = None) -> None:
        self.saved_to = filename


def _vault(
    monkeypatch: pytest.MonkeyPatch,
    password: str | None = None,
    username: str = MACHINE_ID,
) -> _FakeVault:
    """Install the fake vault as the runtime vault opener; return it.

    The stored entry carries username by default, so a fully configured
    vault is already current and an unchanged rerun writes nothing.
    """

    fake = _FakeVault(password, username)
    monkeypatch.setattr(rustdesk_setup.metrics, "open_runtime_vault", lambda cfg: fake)
    return fake


class _RecordingClock:
    """A stand-in for the time module that records the pauses asked for."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


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
    service_enabled_sequence: list[bool] | None = None,
    service_active_sequence: list[bool] | None = None,
    service_command_failure: str = "",
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl answers the releases API with release_payload and succeeds on
    downloads; rustdesk --version, --get-id and the --option get/set
    paths answer from the given values; dpkg prints the architecture;
    systemctl reports the given service states; apt-get and every other
    command succeed. service_active_sequence and service_enabled_sequence
    answer the successive is-active and is-enabled queries in order, so a
    test can describe a service that RustDesk stops and disables between
    two checks; the last state of a sequence answers every remaining
    query. A non-empty service_command_failure makes enable and start
    fail with that text on stderr, the way systemctl reports a masked
    unit. A nonzero return with check=True raises exactly like the real
    subprocess.run.
    """

    calls: list[list[str]] = []
    values = dict(option_values or {})
    current_installed = installed_version
    active_states = list(service_active_sequence or [service_active])
    enabled_states = list(service_enabled_sequence or [service_enabled])

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal current_installed
        calls.append(list(command))
        cmd = list(command)
        rc = 0
        stdout = ""
        stderr = ""
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
            state = (
                enabled_states.pop(0) if len(enabled_states) > 1 else enabled_states[0]
            )
            stdout = "enabled\n" if state else "disabled\n"
            rc = 0 if state else 1
        elif cmd[0] == "systemctl" and cmd[1] == "is-active":
            state = active_states.pop(0) if len(active_states) > 1 else active_states[0]
            stdout = "active\n" if state else "inactive\n"
            rc = 0 if state else 1
        elif cmd[0] == "systemctl":
            if service_command_failure and cmd[1] in ("enable", "start"):
                stderr = service_command_failure
                rc = 1
        elif cmd[0] == "apt-get":
            pass  # update and install succeed
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout)
        return _FakeProc(rc, stdout, stderr)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own cache, ID file and configuration.

    The three paths and the option list are values of the section, so the fixture
    points them at the temporary directory of the test: no test touches
    /var/cache, /var/lib or the home of the desktop user. The option list starts
    empty and a test that needs options patches its own. The pause before the
    settled service check is zeroed here, because a test never waits out real
    time to learn an outcome (docs/guides/developer-guide.md); the test that
    checks the pause sets its own value.
    """

    monkeypatch.setattr(values, "DOWNLOAD_DIR", tmp_path / "download")
    monkeypatch.setattr(values, "ID_FILE_PATH", tmp_path / "rustdesk_id")
    monkeypatch.setattr(values, "CONFIG_DIR", tmp_path / "rustdesk-config")
    monkeypatch.setattr(values, "OPTIONS", ())
    monkeypatch.setattr(values, "SERVICE_SETTLE_DELAY_SECONDS", 0.0)


def _ctx(*, force: bool = False) -> Context:
    return make_context(
        task_name="rustdesk_setup",
        force_tasks=frozenset({"rustdesk_setup"}) if force else frozenset(),
        skip_apt_update=True,
    )


def test_rustdesk_setup_in_desktop_mode_only() -> None:
    # Remote control needs the desktop session of the primary user, so
    # the task runs in the desktop mode only; the server and minimal
    # modes carry no desktop session to control.
    assert "rustdesk_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "rustdesk_setup" not in task_catalog.default_tasks("server", REAL_TASKS)
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
    keys = [option.key for option in SHIPPED_OPTIONS]
    assert "custom-rendezvous-server" not in keys
    assert "relay-server" not in keys
    assert "key" not in keys
    assert "enable-udp-punch" in keys
    assert "direct-server" in keys


def test_installed_latest_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values.ID_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.ID_FILE_PATH.write_text(f"{MACHINE_ID}\n", encoding="utf-8")
    calls = _fake_run(monkeypatch, installed_version=RELEASE_TAG)
    fake = _vault(monkeypatch, password="kofub vifuf midot nudog zodum hobir")
    ctx = _ctx()
    result = rustdesk_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert fake.saved_to is None
    # the release lookup runs with the configured curl timeout and
    # retries, but no download or install happens
    expected_flags = curl_flags(
        engine_values.CURL_TIMEOUT_SECONDS,
        engine_values.CURL_RETRIES,
        engine_values.CURL_CONNECT_TIMEOUT_SECONDS,
        engine_values.CURL_RETRY_MAX_TIME_SECONDS,
        engine_values.CURL_RETRY_DELAY_SECONDS,
    )
    release_calls = [
        call
        for call in calls
        if call[0] == "curl" and "releases/latest" in " ".join(call)
    ]
    assert release_calls
    assert all(flag in release_calls[0] for flag in expected_flags)
    assert not any(call[0] == "curl" and "--output" in call for call in calls)
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run(monkeypatch, installed_version=None)
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "apt-get" and call[1] == "install" for call in calls)
    assert not (values.DOWNLOAD_DIR / ASSET_NAME).exists()


def test_client_commands_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another command set in the values module is the argv the task runs: the
    # option query and write carry their key and value as data, and the
    # service commands carry the unit name, so the client interface is a value
    # and not code.
    monkeypatch.setattr(
        values,
        "OPTIONS",
        (values.RustdeskOption(key="enable-udp-punch", value="Y"),),
    )
    monkeypatch.setattr(values, "GET_OPTION_COMMAND", ("rustdesk", "--query", "{key}"))
    monkeypatch.setattr(
        values,
        "SET_OPTION_COMMAND",
        ("rustdesk", "--apply", "{key}", "{value}"),
    )
    monkeypatch.setattr(
        values,
        "SERVICE_START_COMMAND",
        ("systemctl", "--user", "start", "{service_unit_name}"),
    )
    calls = _fake_run(monkeypatch, installed_version=RELEASE_TAG, service_active=False)
    _vault(monkeypatch, password="kofub vifuf midot nudog zodum hobir")
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert ["rustdesk", "--query", "enable-udp-punch"] in calls
    assert ["rustdesk", "--apply", "enable-udp-punch", "Y"] in calls
    assert [
        "systemctl",
        "--user",
        "start",
        values.SERVICE_UNIT_NAME,
    ] in calls


def test_no_asset_for_unknown_architecture_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The release carries no deb for this architecture: the install is
    # skipped with the reason while the service and the credentials are
    # still handled.
    calls = _fake_run(
        monkeypatch,
        installed_version=None,
        dpkg_arch="s390x",
        release_payload=_release_json(),
    )
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert any("no rustdesk deb asset" in warning for warning in result.warnings)
    assert not any(call[0] == "apt-get" and call[1] == "install" for call in calls)


def test_applies_options_idempotently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        values,
        "OPTIONS",
        (values.RustdeskOption(key="enable-udp-punch", value="Y"),),
    )
    calls = _fake_run(monkeypatch, option_values={"enable-udp-punch": "Y"})
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    # the option was only read, never written
    assert not any(
        call[0] == "rustdesk" and call[1] == "--option" and len(call) == 4
        for call in calls
    )


def test_sets_missing_option(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        values,
        "OPTIONS",
        (values.RustdeskOption(key="enable-udp-punch", value="Y"),),
    )
    calls = _fake_run(monkeypatch, option_values={"enable-udp-punch": ""})
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert ["rustdesk", "--option", "enable-udp-punch", "Y"] in calls


def test_generates_and_stores_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run(monkeypatch)
    fake = _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert fake.saved_to is not None
    assert fake._entry is not None
    assert PASSWORD_WORDS_RE.match(fake._entry.password)
    # the machine ID lands in the username field of the same entry
    assert fake._entry.username == MACHINE_ID
    # the password was applied through rustdesk --password
    assert any(call[0] == "rustdesk" and call[1] == "--password" for call in calls)


def test_reuses_stored_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stored = "kofub vifuf midot nudog zodum hobir"
    calls = _fake_run(monkeypatch)
    fake = _vault(monkeypatch, password=stored)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert fake._entry is not None
    assert fake._entry.password == stored
    assert fake._entry.username == MACHINE_ID
    assert fake.saved_to is None
    assert ["rustdesk", "--password", stored] in calls


def test_fills_stored_machine_id_into_stale_vault_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stored = "kofub vifuf midot nudog zodum hobir"
    calls = _fake_run(monkeypatch)
    # an entry created before the machine ID was stored carries the
    # password but an empty username; the first run fills the username
    fake = _vault(monkeypatch, password=stored, username="")
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert fake.saved_to is not None
    assert fake._entry is not None
    assert fake._entry.password == stored
    assert fake._entry.username == MACHINE_ID
    assert ["rustdesk", "--password", stored] in calls


def test_force_regenerates_password_and_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    identity_dir = values.CONFIG_DIR
    identity_dir.mkdir(parents=True)
    identity = identity_dir / "RustDesk.toml"
    identity.write_text("old identity", encoding="utf-8")
    calls = _fake_run(monkeypatch)
    fake = _vault(monkeypatch, password="old password words")
    result = rustdesk_setup.task(_ctx(force=True))
    assert result.success is True
    assert not identity.exists()
    assert fake._entry is not None
    assert fake._entry.password != "old password words"
    assert fake._entry.username == MACHINE_ID
    assert any(
        call == ["systemctl", "stop", values.SERVICE_UNIT_NAME] for call in calls
    )


def test_force_stores_regenerated_machine_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # the identity reset makes the daemon report a fresh ID
    _fake_run(monkeypatch, machine_id="99999999")
    fake = _vault(monkeypatch, password="old words", username=MACHINE_ID)
    result = rustdesk_setup.task(_ctx(force=True))
    assert result.success is True
    assert fake._entry is not None
    assert fake._entry.username == "99999999"
    assert fake._entry.password != "old words"
    assert values.ID_FILE_PATH.read_text(encoding="utf-8").strip() == "99999999"


def test_writes_machine_id_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_run(monkeypatch, machine_id=MACHINE_ID)
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert values.ID_FILE_PATH.read_text(encoding="utf-8").strip() == MACHINE_ID
    assert values.ID_FILE_PATH.stat().st_mode & 0o777 == 0o644


def test_id_file_stable_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values.ID_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.ID_FILE_PATH.write_text(f"{MACHINE_ID}\n", encoding="utf-8")
    before = values.ID_FILE_PATH.read_text(encoding="utf-8")
    _fake_run(monkeypatch, machine_id=MACHINE_ID)
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    # a matching ID file is left exactly as it was
    assert values.ID_FILE_PATH.read_text(encoding="utf-8") == before


def test_vault_unavailable_warns_without_changing_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run(monkeypatch)
    monkeypatch.setattr(rustdesk_setup.metrics, "open_runtime_vault", lambda cfg: None)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.warnings
    assert "runtime vault unavailable" in result.warnings[0]
    assert not any(call[0] == "rustdesk" and call[1] == "--password" for call in calls)


def test_real_config_clears_the_service_stopped_flag() -> None:
    # RustDesk disables and stops its own unit while the stop-service flag
    # is set, so the shipped values carry the running value of the flag:
    # without it a provisioned machine is registered with the public server
    # and still unreachable.
    shipped = {option.key: option.value for option in SHIPPED_OPTIONS}
    assert shipped.get("stop-service") == ""


def test_clears_the_stopped_service_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The running value of the flag comes from the values module and reaches
    # the client as one argv element of its own, the empty value included.
    monkeypatch.setattr(
        values,
        "OPTIONS",
        (values.RustdeskOption(key="stop-service", value=""),),
    )
    calls = _fake_run(monkeypatch, option_values={"stop-service": "Y"})
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert ["rustdesk", "--option", "stop-service", ""] in calls


def test_restarts_the_service_the_stopped_flag_left_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The flag made RustDesk stop the unit after the start: the run starts
    # it once more once the flag is cleared and reports the machine ready,
    # because the settled check sees the service active.
    monkeypatch.setattr(
        values,
        "OPTIONS",
        (values.RustdeskOption(key="stop-service", value=""),),
    )
    calls = _fake_run(
        monkeypatch,
        option_values={"stop-service": "Y"},
        service_active_sequence=[True, False, True],
    )
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    assert [
        "systemctl",
        "start",
        values.SERVICE_UNIT_NAME,
    ] in calls
    assert result.message is not None
    assert result.message.startswith("rustdesk ready, ID")


def test_reports_a_service_that_does_not_stay_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit stays down even after the restart: the run says the machine
    # is not reachable instead of claiming readiness, and reports the
    # finding as a warning of a completed task.
    _fake_run(monkeypatch, service_active_sequence=[True, False, False])
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.message is not None
    assert "rustdesk not reachable" in result.message
    assert "ready" not in result.message
    assert any(
        "is not running after the configuration steps" in warning
        for warning in result.warnings
    )


def test_repairs_a_service_the_armed_flag_left_disabled_and_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The state a live machine showed: the flag made RustDesk disable and
    # stop the unit at its start, so the run finds a unit that is neither
    # enabled nor running and leaves both the reachability and the boot
    # state of the machine in place.
    monkeypatch.setattr(
        values,
        "OPTIONS",
        (values.RustdeskOption(key="stop-service", value=""),),
    )
    calls = _fake_run(
        monkeypatch,
        option_values={"stop-service": "Y"},
        service_enabled_sequence=[False, False, True],
        service_active_sequence=[False, False, True],
    )
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert result.warnings == ()
    unit = values.SERVICE_UNIT_NAME
    assert calls.count(["systemctl", "enable", unit]) == 2
    assert calls.count(["systemctl", "start", unit]) == 2
    assert result.message is not None
    assert result.message.startswith("rustdesk ready, ID")


def test_reports_the_words_of_a_failed_service_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reason a warned step gives is what systemd said, so the operator
    # of a machine without a developer reads "Unit ... is masked" and not
    # the repr of a Python exception.
    _fake_run(
        monkeypatch,
        service_active_sequence=[True, False],
        service_command_failure="Unit rustdesk.service is masked.",
    )
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert any(
        "Unit rustdesk.service is masked." in warning for warning in result.warnings
    )
    assert result.message is not None
    assert "rustdesk not reachable" in result.message


def test_reports_a_service_that_runs_without_being_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A unit that runs now and is disabled for boot is reachable today and
    # not after a reboot: the run reports that as a finding instead of
    # hiding it behind the readiness of the moment.
    _fake_run(monkeypatch, service_enabled_sequence=[True, False])
    _vault(monkeypatch)
    result = rustdesk_setup.task(_ctx())
    assert result.success is True
    assert any("is not enabled for boot" in warning for warning in result.warnings)
    assert result.message is not None
    assert result.message.startswith("rustdesk ready, ID")


def test_settle_delay_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The pause before the final state check is a value: another value in the
    # module is another pause, so a machine that needs longer to show its
    # state is tuned without touching the code.
    clock = _RecordingClock()
    monkeypatch.setattr(rustdesk_setup, "time", clock)
    for configured in (2.5, 7.0):
        monkeypatch.setattr(values, "SERVICE_SETTLE_DELAY_SECONDS", configured)
        _fake_run(monkeypatch)
        _vault(monkeypatch)
        result = rustdesk_setup.task(_ctx())
        assert result.success is True
    assert clock.sleeps == [2.5, 7.0]
