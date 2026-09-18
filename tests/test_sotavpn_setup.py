"""Tests for the sotavpn_setup task: the subscription of the fastest exit.

The panel is always faked: the task talks to it through pyntara.xui, so
patching those functions exercises the whole task logic without a panel.
The fake refuses the template calls on purpose, because the task owns no
part of the Xray document: the pool and the rules belong to the
three_x_ui_xray_setup task. The bridge itself is faked at the shell
boundary: the installer command and the state query are answered by one
recording stand-in, and the extracted archive is built from a prepared tar.
"""

from __future__ import annotations

import io
import json
import shutil
import stat
import subprocess
import tarfile
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import FakeProc, make_config, make_context

from pyntara.config import Config
from pyntara.context import Context
from pyntara.tasks import sotavpn_setup as sotavpn
from pyntara.values import common as common_values
from pyntara.values import sotavpn_setup as values
from pyntara.values import three_x_ui_xray_setup as panel_values

KEY = "11111111-2222-3333-4444-555555555555"
INSTALLER_NAME = "install_sotavpn_bridge.py"
SETTINGS_NAME = "settings.py"


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own home of the desktop user.

    The home is a shared value and the installation of the bridge lives under
    it, so the fixture points it at the temporary directory of the test: no test
    touches the real home. The panel vocabulary of the three_x_ui_xray_setup
    section stays in the config document until that section is migrated, so the
    helper below still edits it there.
    """

    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path / "home"))


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
) -> Context:
    """Context of the task with the bridge installed into the test tree."""

    config: Config = make_config()
    return make_context(
        task_name="sotavpn_setup",
        install_mode="server",
        repo_root=tmp_path,
        task_data_root=tmp_path,
        vault_password="run-pass",
        force_tasks=frozenset({"sotavpn_setup"}) if force else frozenset(),
        config=config,
    )


def _settings_text(version: str, port: int) -> str:
    return f'PROGRAM_VERSION = "{version}"\nHTTP_PORT = {port}\n'


def _installed_settings_path(ctx: Context) -> Path:
    return (
        Path(common_values.DESKTOP_HOME_DIR)
        / values.USER_INSTALL_RELATIVE_PATH
        / values.SETTINGS_FILE_NAME
    )


def _write_installed(ctx: Context, *, version: str, port: int) -> Path:
    path = _installed_settings_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_settings_text(version, port), encoding="utf-8")
    return path


def _bridge_tree(
    tmp_path: Path, *, version: str = "1.0.9", port: int = 25080
) -> tuple[Path, Path]:
    work_dir = tmp_path / "work"
    root = work_dir / "repo-main"
    root.mkdir(parents=True)
    (root / INSTALLER_NAME).write_text("# the installer\n", encoding="utf-8")
    (root / SETTINGS_NAME).write_text(_settings_text(version, port), encoding="utf-8")
    return work_dir, root


def _fetched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    version: str = "1.0.9",
    port: int = 25080,
) -> Path:
    """Patch the fetch step with a prepared tree and answer its root."""

    work_dir, root = _bridge_tree(tmp_path, version=version, port=port)
    monkeypatch.setattr(
        sotavpn, "_fetch_the_bridge", lambda *_a, **_k: (work_dir, root)
    )
    return root


class _Commands:
    """Record the commands of the task and answer them like the machine.

    The three queries the task makes are answered here: the state of the
    user service, the environment of the desktop session and the installer
    itself (recognized by the user wrapper). Anything else is answered
    with success so a test notices an unexpected call in its record.
    """

    def __init__(self, *, active: bool = True, installer_ok: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.active = active
        self.installer_ok = installer_ok

    def __call__(self, command: Iterable[str], **_kwargs: object) -> FakeProc:
        argv = [str(part) for part in command]
        self.commands.append(argv)
        if "is-active" in argv:
            return FakeProc(0, "active\n") if self.active else FakeProc(3, "inactive\n")
        if argv and argv[0] == "runuser":
            if not self.installer_ok:
                raise subprocess.CalledProcessError(1, argv)
            return FakeProc(0, "")
        if "show-environment" in argv:
            return FakeProc(0, "XDG_RUNTIME_DIR=/run/user/1000\n")
        return FakeProc(0, "")

    def installer_runs(self) -> list[list[str]]:
        return [c for c in self.commands if c and c[0] == "runuser"]

    def installer_run(self) -> list[str] | None:
        runs = self.installer_runs()
        return runs[0] if runs else None


def _vault(monkeypatch: pytest.MonkeyPatch, *, key: str | None) -> None:
    """Patch the source vault: an entry with the key, or nothing."""

    entry = SimpleNamespace(password=key)
    vault = SimpleNamespace(
        root_group=object(),
        find_entries=lambda **_kwargs: entry if key is not None else None,
    )
    monkeypatch.setattr(
        sotavpn,
        "open_source_vault",
        lambda *_a, **_k: (vault, Path("/clone/secrets/production.vault")),
    )


def _no_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sotavpn, "open_source_vault", lambda *_a, **_k: None)


class _Panel:
    """Stateful stand-in for the panel functions the task calls.

    The template calls raise on purpose: the task must never touch the
    Xray document, so a test that reaches one fails instead of passing on
    an unread answer. counts, when set, is the queue of outbound counts
    the subscription answers after the refresh, which is how a test makes
    the wait for the node list take more than one read.
    """

    def __init__(
        self,
        *,
        last_error: str = "",
        outbound_count: int = 2,
        status: list[dict[str, object]] | None = None,
        counts: list[int] | None = None,
    ) -> None:
        self.subscription: dict[str, object] | None = None
        self.last_error = last_error
        self.outbound_count = outbound_count
        self.counts = counts
        self.refreshed_once = False
        self.pool_tag = panel_values.POOL_BALANCER_TAG
        self.status = (
            status
            if status is not None
            else [
                {
                    "tag": self.pool_tag,
                    "running": True,
                    "override": "",
                    "selected": ["sota-node-1"],
                }
            ]
        )
        self.upserts: list[dict[str, object]] = []
        self.refreshed: list[object] = []
        self.status_tags: list[object] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Panel:
        monkeypatch.setattr(
            "pyntara.xui.panel_environment", lambda _timeout: {"port": "3579"}
        )
        monkeypatch.setattr("pyntara.xui.read_xray_template", self._refuse_the_template)
        monkeypatch.setattr(
            "pyntara.xui.write_xray_template", self._refuse_the_template
        )
        monkeypatch.setattr(
            "pyntara.xui.find_outbound_subscription_by_remark", self._find
        )
        monkeypatch.setattr("pyntara.xui.upsert_outbound_subscription", self._upsert)
        monkeypatch.setattr("pyntara.xui.refresh_outbound_subscription", self._refresh)
        monkeypatch.setattr("pyntara.xui.list_balancer_status", self._status)
        return self

    def _refuse_the_template(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("the task must not read or write the Xray template")

    def _find(
        self, _env: object, _remark: object, _timeout: object
    ) -> dict[str, object] | None:
        if self.subscription is None:
            return None
        if self.refreshed_once and self.counts:
            self.subscription["outboundCount"] = self.counts.pop(0)
        return dict(self.subscription)

    def _upsert(
        self,
        _env: object,
        payload: dict[str, object],
        _timeout: object,
    ) -> tuple[bool, str]:
        self.upserts.append(json.loads(json.dumps(payload)))
        self.subscription = {"id": 7, **payload}
        return True, "subscription created"

    def _refresh(
        self, _env: object, subscription_id: object, _timeout: object
    ) -> tuple[bool, str]:
        self.refreshed.append(subscription_id)
        assert self.subscription is not None
        self.subscription["lastError"] = self.last_error
        self.subscription["outboundCount"] = self.outbound_count
        self.refreshed_once = True
        return True, "refreshed"

    def _status(
        self, _env: object, tags: object, _timeout: object
    ) -> list[dict[str, object]]:
        self.status_tags.append(tags)
        return [dict(item) for item in self.status]


class TestGate:
    """The task is off unless the source vault carries the key."""

    def test_no_vault_means_no_change(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _no_vault(monkeypatch)
        monkeypatch.setattr(
            sotavpn,
            "run_command",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("no command may run")
            ),
        )
        result = sotavpn.task(_ctx(tmp_path))
        assert result.success is True
        assert result.changed is False
        assert "not configured" in (result.message or "")

    def test_a_missing_entry_means_no_change(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _vault(monkeypatch, key=None)
        monkeypatch.setattr(
            sotavpn,
            "run_command",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("no command may run")
            ),
        )
        result = sotavpn.task(_ctx(tmp_path))
        assert result.changed is False
        assert "not configured" in (result.message or "")

    def test_an_empty_password_means_no_change(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _vault(monkeypatch, key="")
        monkeypatch.setattr(
            sotavpn,
            "run_command",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("no command may run")
            ),
        )
        result = sotavpn.task(_ctx(tmp_path))
        assert result.changed is False
        assert "not configured" in (result.message or "")


class TestInstallAndSubscription:
    """The installer, the subscription and the wait of a full run."""

    def _prepare(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        installed_port: int = 25080,
        active: bool = True,
        installer_ok: bool = True,
        panel: _Panel | None = None,
    ) -> tuple[Context, _Commands, _Panel, Path]:
        ctx = _ctx(tmp_path)
        _vault(monkeypatch, key=KEY)
        # The settings of the installed bridge decide nothing: the
        # installer runs as it is on every run of the task.
        _write_installed(ctx, version="1.0.0", port=installed_port)
        root = _fetched(monkeypatch, tmp_path)
        commands = _Commands(active=active, installer_ok=installer_ok)
        monkeypatch.setattr(sotavpn, "run_command", commands)
        # The session environment of the desktop user is read through the
        # user manager, which is a real systemctl call: the stand-in hands
        # the one variable the installer needs.
        monkeypatch.setattr(
            sotavpn,
            "user_session_environment",
            lambda *_a, **_k: {"XDG_RUNTIME_DIR": "/run/user/1000"},
        )
        monkeypatch.setattr(sotavpn, "port_listener_pid", lambda *_a, **_k: 4321)
        panel = panel or _Panel()
        panel.install(monkeypatch)
        return ctx, commands, panel, root

    def test_the_installer_runs_as_the_account_and_the_subscription_is_written(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        panel = _Panel()
        ctx, commands, panel, root = self._prepare(monkeypatch, tmp_path, panel=panel)
        result = sotavpn.task(ctx)
        assert result.success is True
        assert result.changed is True
        assert not result.warnings

        installer = commands.installer_run()
        assert installer is not None
        assert installer[:5] == [
            "runuser",
            "-u",
            common_values.DESKTOP_USERNAME,
            "--",
            "env",
        ]
        assert f"HOME={common_values.DESKTOP_HOME_DIR}" in installer
        assert str(root / INSTALLER_NAME) in installer
        assert installer[-1] == "install"

        assert len(panel.upserts) == 1
        payload = panel.upserts[0]
        assert payload["remark"] == values.SUBSCRIPTION_REMARK
        assert payload["url"] == f"http://127.0.0.1:25080/sub/{KEY}/raw"
        assert payload["tagPrefix"] == panel_values.POOL_MEMBER_PREFIX
        assert payload["updateInterval"] == (
            values.SUBSCRIPTION_UPDATE_INTERVAL_SECONDS
        )
        assert payload["allowPrivate"] is True
        assert payload["enabled"] is True
        assert panel.refreshed == [7]
        assert panel.status_tags == [(panel_values.POOL_BALANCER_TAG,)]
        assert "the panel lists 2 nodes" in (result.message or "")

    def test_a_second_run_installs_again_and_writes_no_subscription(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        panel = _Panel()
        ctx, commands, panel, _root = self._prepare(monkeypatch, tmp_path, panel=panel)
        first = sotavpn.task(ctx)
        assert first.changed is True
        assert len(panel.upserts) == 1
        # The fetch step removes its temporary tree and the machine keeps
        # its own settings file, which is what the second run reads.
        _write_installed(ctx, version="1.0.9", port=25080)
        _fetched(monkeypatch, tmp_path)
        second = sotavpn.task(ctx)
        assert second.success is True
        # The installer runs as it is on every run, so a machine that
        # changed nothing is installed again.
        assert second.changed is True
        assert len(commands.installer_runs()) == 2
        assert not second.warnings
        assert len(panel.upserts) == 1
        assert panel.refreshed == [7, 7]
        assert "the panel lists 2 nodes" in (second.message or "")

    def test_the_installer_runs_as_it_is_without_looking_at_the_settings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The task compares nothing and reads no version: the installer of
        # the archive runs as it is, whatever the settings carry.
        panel = _Panel()
        ctx, commands, _panel, _root = self._prepare(monkeypatch, tmp_path, panel=panel)
        result = sotavpn.task(ctx)
        assert result.success is True
        assert result.changed is True
        assert len(commands.installer_runs()) == 1

    def test_a_failed_installer_is_a_warning_and_the_rest_continues(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        panel = _Panel()
        ctx, _commands, panel, _root = self._prepare(
            monkeypatch, tmp_path, installer_ok=False, panel=panel
        )
        result = sotavpn.task(ctx)
        assert result.success is True
        assert any("installer failed" in warning for warning in result.warnings)
        assert panel.upserts


class TestSubscriptionState:
    """What the task reports about the list and the pool."""

    def _prepare(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        panel: _Panel,
        *,
        force: bool = False,
        **sections: dict[str, object] | None,
    ) -> tuple[Context, _Panel]:
        ctx = _ctx(
            tmp_path,
            force=force,
        )
        for name, value in (sections.get("three_x_ui") or {}).items():
            monkeypatch.setattr(panel_values, name.upper(), value)
        _vault(monkeypatch, key=KEY)
        _write_installed(ctx, version="1.0.9", port=25080)
        _fetched(monkeypatch, tmp_path)
        commands = _Commands()
        monkeypatch.setattr(sotavpn, "run_command", commands)
        monkeypatch.setattr(
            sotavpn,
            "user_session_environment",
            lambda *_a, **_k: {"XDG_RUNTIME_DIR": "/run/user/1000"},
        )
        monkeypatch.setattr(sotavpn, "port_listener_pid", lambda *_a, **_k: 4321)
        panel.install(monkeypatch)
        return ctx, panel

    def test_the_panel_fetch_error_is_reported_without_the_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        broken = f"cannot fetch http://127.0.0.1:25080/sub/{KEY}/raw"
        ctx, _panel = self._prepare(
            monkeypatch,
            tmp_path,
            _Panel(last_error=broken, outbound_count=0),
        )
        result = sotavpn.task(ctx)
        assert result.success is True
        assert any("[secret]" in warning for warning in result.warnings)
        assert all(KEY not in warning for warning in result.warnings)
        assert KEY not in (result.message or "")
        assert "has no node list yet" in (result.message or "")

    def test_the_node_list_is_waited_for(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The bridge answers from its cache and asks the vendor when that
        # cache is cold, so the panel may answer the first question with an
        # empty list. The task asks again instead of reporting a failure.
        panel = _Panel(counts=[0, 2])
        ctx, _panel = self._prepare(monkeypatch, tmp_path, panel)
        sleeps: list[float] = []
        monkeypatch.setattr(sotavpn.time, "sleep", sleeps.append)
        result = sotavpn.task(ctx)
        assert sleeps == [values.READINESS_CHECK_DELAY_SECONDS]
        assert not [w for w in result.warnings if "node list" in w]
        assert "the panel lists 2 nodes" in (result.message or "")

    def test_a_missing_node_list_is_reported_not_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The budget runs out while the panel is still fetching: the
        # subscription is already written and the panel fetches it again on
        # its own schedule, so the fact is reported without an alarm.
        panel = _Panel(outbound_count=0)
        monkeypatch.setattr(values, "SUBSCRIPTION_FETCH_WAIT_SECONDS", 0)
        ctx, _panel = self._prepare(monkeypatch, tmp_path, panel)
        result = sotavpn.task(ctx)
        assert not [w for w in result.warnings if "node list" in w]
        assert "has no node list yet" in (result.message or "")

    def test_force_writes_the_subscription_that_already_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A rerun whose subscription already carries the wanted values
        # writes nothing; force writes it again, which is the lever an
        # operator has when the panel and the machine disagree in a way the
        # fields do not show.
        panel = _Panel()
        ctx, _panel = self._prepare(monkeypatch, tmp_path, panel)
        first = sotavpn.task(ctx)
        assert first.changed is True
        assert len(panel.upserts) == 1
        _write_installed(ctx, version="1.0.9", port=25080)
        _fetched(monkeypatch, tmp_path)
        plain = sotavpn.task(ctx)
        assert plain.success is True
        assert len(panel.upserts) == 1
        forced_ctx, _panel = self._prepare(monkeypatch, tmp_path, panel, force=True)
        forced = sotavpn.task(forced_ctx)
        assert forced.success is True
        assert len(panel.upserts) == 2

    def test_the_pool_is_waited_for(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The panel rebuilds the core when the outbounds of a subscription
        # arrive, so the first question about the pool can land before it
        # answers again. The task waits for the configured budget.
        panel = _Panel(status=[])
        ctx, _panel = self._prepare(
            monkeypatch,
            tmp_path,
            panel,
            three_x_ui={
                "core_ready_wait_seconds": 5,
                "readiness_check_delay_seconds": 2,
            },
        )
        calls = {"count": 0}

        def answer_once(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
            calls["count"] += 1
            if calls["count"] == 1:
                return []
            return [
                {
                    "tag": panel_values.POOL_BALANCER_TAG,
                    "running": True,
                    "override": "",
                    "selected": ["sota-node-1"],
                }
            ]

        monkeypatch.setattr("pyntara.xui.list_balancer_status", answer_once)
        sleeps: list[float] = []
        monkeypatch.setattr(sotavpn.time, "sleep", sleeps.append)
        result = sotavpn.task(ctx)
        assert sleeps == [2]
        assert not [w for w in result.warnings if "pool" in w]

    def test_a_core_that_lacks_the_pool_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ctx, _panel = self._prepare(
            monkeypatch,
            tmp_path,
            _Panel(status=[]),
            three_x_ui={"core_ready_wait_seconds": 0},
        )
        result = sotavpn.task(ctx)
        assert any("does not report the pool" in warning for warning in result.warnings)


class TestReadinessAndSettings:
    """The wait for the bridge and the values read from its settings."""

    def _prepare(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        panel: _Panel,
    ) -> Context:
        ctx = _ctx(tmp_path)
        _vault(monkeypatch, key=KEY)
        _write_installed(ctx, version="1.0.9", port=25080)
        _fetched(monkeypatch, tmp_path)
        commands = _Commands(active=True)
        monkeypatch.setattr(sotavpn, "run_command", commands)
        # The installer runs on every run of the task and reads the session
        # environment of the desktop user through the user manager, which
        # is a real systemctl call: the stand-in keeps it off the machine.
        monkeypatch.setattr(
            sotavpn,
            "user_session_environment",
            lambda *_a, **_k: {"XDG_RUNTIME_DIR": "/run/user/1000"},
        )
        monkeypatch.setattr(sotavpn, "port_listener_pid", lambda *_a, **_k: 4321)
        panel.install(monkeypatch)
        return ctx

    def test_the_wait_gives_up_and_reports(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        panel = _Panel()
        monkeypatch.setattr(values, "BRIDGE_READY_WAIT_SECONDS", 0)
        ctx = _ctx(tmp_path)
        _vault(monkeypatch, key=KEY)
        _write_installed(ctx, version="1.0.9", port=25080)
        _fetched(monkeypatch, tmp_path)
        monkeypatch.setattr(sotavpn, "run_command", _Commands(active=True))
        monkeypatch.setattr(
            sotavpn,
            "user_session_environment",
            lambda *_a, **_k: {"XDG_RUNTIME_DIR": "/run/user/1000"},
        )
        monkeypatch.setattr(sotavpn, "port_listener_pid", lambda *_a, **_k: None)
        panel.install(monkeypatch)
        result = sotavpn.task(ctx)
        assert any("within 0 s" in warning for warning in result.warnings)
        assert panel.upserts

    def test_the_wait_pauses_and_succeeds_on_the_second_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        panel = _Panel()
        monkeypatch.setattr(values, "BRIDGE_READY_WAIT_SECONDS", 5)
        monkeypatch.setattr(values, "READINESS_CHECK_DELAY_SECONDS", 2)
        ctx = _ctx(tmp_path)
        _vault(monkeypatch, key=KEY)
        _write_installed(ctx, version="1.0.9", port=25080)
        _fetched(monkeypatch, tmp_path)
        commands = _Commands(active=True)
        monkeypatch.setattr(sotavpn, "run_command", commands)
        monkeypatch.setattr(
            sotavpn,
            "user_session_environment",
            lambda *_a, **_k: {"XDG_RUNTIME_DIR": "/run/user/1000"},
        )
        answers = {"count": 0}

        def fake_listener(*_args: object, **_kwargs: object) -> int | None:
            answers["count"] += 1
            return None if answers["count"] == 1 else 4321

        monkeypatch.setattr(sotavpn, "port_listener_pid", fake_listener)
        sleeps: list[float] = []
        monkeypatch.setattr(sotavpn.time, "sleep", sleeps.append)
        panel.install(monkeypatch)
        result = sotavpn.task(ctx)
        assert sleeps == [2]
        assert not [w for w in result.warnings if "did not answer" in w]
        assert panel.upserts

    def test_settings_that_lack_the_port_stop_before_the_panel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ctx = _ctx(tmp_path)
        _vault(monkeypatch, key=KEY)
        monkeypatch.setattr(sotavpn, "_fetch_the_bridge", lambda *_a, **_k: None)
        monkeypatch.setattr(
            sotavpn, "run_command", lambda *_a, **_k: FakeProc(0, "active\n")
        )
        monkeypatch.setattr(
            "pyntara.xui.panel_environment",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("the panel must not be asked")
            ),
        )
        result = sotavpn.task(ctx)
        assert result.success is True
        assert any("HTTP_PORT" in warning for warning in result.warnings)

    def test_a_value_that_is_not_a_literal_answers_none(self, tmp_path: Path) -> None:
        computed = tmp_path / "computed.py"
        computed.write_text("HTTP_PORT = 25080 + 0\n", encoding="utf-8")
        assert sotavpn._settings_value(computed, "HTTP_PORT") is None
        assert sotavpn._settings_value(tmp_path / "missing.py", "HTTP_PORT") is None

    def test_an_unreachable_panel_is_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ctx = self._prepare(monkeypatch, tmp_path, _Panel())
        monkeypatch.setattr(
            "pyntara.xui.panel_environment",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("panel unreachable")),
        )
        result = sotavpn.task(ctx)
        assert result.success is True
        # The bridge is installed on every run, so the run counts as a
        # change even when the panel could not be reached afterwards.
        assert result.changed is True
        assert any("panel unreachable" in warning for warning in result.warnings)


class TestFetchTheBridge:
    """The download and the extraction of the branch archive."""

    def _archive(self, tmp_path: Path) -> Path:
        archive = tmp_path / "prepared.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            for name, body in (
                (f"repo-main/{INSTALLER_NAME}", "# the installer\n"),
                (f"repo-main/{SETTINGS_NAME}", _settings_text("1.0.9", 25080)),
            ):
                data = body.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                package.addfile(info, io.BytesIO(data))
        return archive

    def test_the_archive_is_downloaded_and_extracted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        prepared = self._archive(tmp_path)
        seen: list[list[str]] = []

        def fake_run(command: Iterable[str], **_kwargs: object) -> FakeProc:
            argv = [str(part) for part in command]
            seen.append(argv)
            target = Path(next(part for part in argv if part.endswith(".tar.gz")))
            shutil.copyfile(prepared, target)
            return FakeProc(0, "")

        monkeypatch.setattr(sotavpn, "run_command", fake_run)
        warnings: list[str] = []
        fetched = sotavpn._fetch_the_bridge(30.0, warnings)
        assert fetched is not None
        work_dir, root = fetched
        assert not warnings
        assert (root / INSTALLER_NAME).is_file()
        assert (root / SETTINGS_NAME).is_file()
        assert (
            sotavpn._settings_value(root / SETTINGS_NAME, "PROGRAM_VERSION") == "1.0.9"
        )
        shutil.rmtree(work_dir, ignore_errors=True)

    def test_the_work_directory_stays_private_and_belongs_to_the_account(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The installer runs as the desktop user and reads the extracted
        # tree, so the temporary directory keeps its private mode and gets
        # that account as its owner instead of being opened to everyone.
        prepared = self._archive(tmp_path)

        def fake_run(command: Iterable[str], **_kwargs: object) -> FakeProc:
            argv = [str(part) for part in command]
            target = Path(next(part for part in argv if part.endswith(".tar.gz")))
            shutil.copyfile(prepared, target)
            return FakeProc(0, "")

        monkeypatch.setattr(sotavpn, "run_command", fake_run)
        warnings: list[str] = []
        fetched = sotavpn._fetch_the_bridge(30.0, warnings)
        assert fetched is not None
        work_dir, root = fetched
        assert stat.S_IMODE(work_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / SETTINGS_NAME).stat().st_mode) & 0o444
        shutil.rmtree(work_dir, ignore_errors=True)

    def test_an_unknown_account_is_reported_and_the_fetch_continues(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        prepared = self._archive(tmp_path)

        def fake_run(command: Iterable[str], **_kwargs: object) -> FakeProc:
            argv = [str(part) for part in command]
            target = Path(next(part for part in argv if part.endswith(".tar.gz")))
            shutil.copyfile(prepared, target)
            return FakeProc(0, "")

        monkeypatch.setattr(sotavpn, "run_command", fake_run)
        warnings: list[str] = []
        monkeypatch.setattr(common_values, "DESKTOP_USERNAME", "no-such-account")
        fetched = sotavpn._fetch_the_bridge(30.0, warnings)
        assert fetched is not None
        work_dir, _root = fetched
        shutil.rmtree(work_dir, ignore_errors=True)

    def test_an_unusable_archive_is_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fake_run(command: Iterable[str], **_kwargs: object) -> FakeProc:
            argv = [str(part) for part in command]
            target = Path(next(part for part in argv if part.endswith(".tar.gz")))
            target.write_text("not an archive", encoding="utf-8")
            return FakeProc(0, "")

        monkeypatch.setattr(sotavpn, "run_command", fake_run)
        warnings: list[str] = []
        assert sotavpn._fetch_the_bridge(30.0, warnings) is None
        assert any("not extracted" in warning for warning in warnings)

    def test_a_dead_address_is_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            sotavpn,
            "run_command",
            lambda *_a, **_k: (_ for _ in ()).throw(OSError("no route")),
        )
        warnings: list[str] = []
        assert sotavpn._fetch_the_bridge(30.0, warnings) is None
        assert any("not downloaded" in warning for warning in warnings)
