"""Unit tests for the zswap_service task.

All external resources (subprocess and the kernel attribute files) are
mocked; the parameter files and the unit template live in temporary
fixtures whose paths come from the config, so the task is exercised the
way it runs on a machine (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.config import ZswapServiceConfig
from pyntara.context import Context
from pyntara.tasks import zswap_service

UNIT_TEMPLATE = """\
[Unit]
Description=Configure zswap compressed swap cache
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
$exec_lines

[Install]
WantedBy=multi-user.target
"""

# Target parameter values derived from the _ctx config below.
TARGET = {
    "enabled": "Y",
    "compressor": "zstd",
    "max_pool_percent": "50",
    "accept_threshold_percent": "100",
    "shrinker_enabled": "Y",
}

# Kernel defaults on Kubuntu: zswap on with lzo at a 20 percent pool.
DEFAULTS = {
    "enabled": "Y",
    "compressor": "lzo",
    "max_pool_percent": "20",
    "accept_threshold_percent": "90",
    "shrinker_enabled": "Y",
}


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        task_name="zswap_service",
        install_mode="server",
        force_tasks=frozenset({"zswap_service"}) if force else frozenset(),
        task_data_root=tmp_path,
        repo_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            task_data_root=tmp_path,
            systemd_unit_dir=tmp_path / "systemd",
            zswap_parameters_dir_path=tmp_path / "sys" / "module" / "zswap" / "parameters",
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
        ),
    )


def _install_fixtures(
    tmp_path: Path,
    *,
    current: dict[str, str] | None = None,
    settings: ZswapServiceConfig | None = None,
) -> ZswapFixtures:
    """Point the task at temporary fixtures; return the fixture paths.

    The parameter files live under the directory the section names, so the
    task reads the configured path and no module constant has to be
    replaced; the current values default to the kernel defaults, which
    mismatch the target so most tests exercise the write path. A test that
    wants another layout passes its own settings.
    """

    values = dict(DEFAULTS if current is None else current)
    if settings is None:
        settings = make_config(
            zswap_parameters_dir_path=(
                tmp_path / "sys" / "module" / "zswap" / "parameters"
            )
        ).zswap_service
    params_dir = settings.parameters_dir_path
    params_dir.mkdir(parents=True, exist_ok=True)
    for name in settings.parameter_names:
        (params_dir / name).write_text(
            f"{values.get(name, '')}\n", encoding="utf-8"
        )
    template = (
        tmp_path
        / "task_data"
        / "zswap_service"
        / settings.unit_template_file_name
    )
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    return {"params_dir": params_dir, "template": template}


class ZswapFixtures(TypedDict):
    """Temporary sysfs mirror and template paths."""

    params_dir: Path
    template: Path


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    fixtures: ZswapFixtures,
    *,
    enabled: bool,
    fail: Callable[[list[str]], bool] | None = None,
    fail_write: frozenset[str] = frozenset(),
) -> tuple[list[list[str]], list[tuple[str, str]]]:
    """Install subprocess and sysfs fakes; return (calls, writes).

    systemctl is-enabled answers from the enabled flag, daemon-reload and
    enable succeed unless matched by fail; the sysfs write fake stores the
    value into the fixture file, mirroring how the kernel echoes it back,
    and raises OSError for parameters named in fail_write.
    """

    calls: list[list[str]] = []
    writes: list[tuple[str, str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if fail is not None and fail(command):
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "systemctl" and command[1] == "is-enabled":
            if enabled:
                return _FakeProc(0, "enabled\n")
            return _FakeProc(1, "disabled")
        return _FakeProc(0)

    def fake_write_sysfs(path: Path, value: str) -> None:
        writes.append((path.name, value))
        if path.name in fail_write:
            raise OSError(f"cannot write {path.name}")
        if not path.exists():
            # A missing attribute file is how the kernel reports an absent
            # or rejected parameter; the write must fail like on real sysfs.
            raise OSError(f"cannot write {path.name}")
        path.write_text(f"{value}\n", encoding="utf-8")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    monkeypatch.setattr(zswap_service, "_write_sysfs", fake_write_sysfs)
    return calls, writes


def _expected_unit(
    target: dict[str, str],
    params_dir: Path,
    settings: ZswapServiceConfig | None = None,
) -> str:
    """The unit file the task must render for the given target."""

    if settings is None:
        settings = make_config().zswap_service
    lines = [
        f"ExecStart=/bin/sh -c 'echo {target[name]} > "
        f"{params_dir / name}'"
        for name in settings.parameter_names
    ]
    return UNIT_TEMPLATE.replace("$exec_lines", "\n".join(lines))


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every parameter already matches and the service is enabled: the task
    # skips and runs only the status queries.
    fixtures = _install_fixtures(tmp_path, current=TARGET)
    calls, writes = _install_fake(monkeypatch, fixtures, enabled=True)
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert all(call[0] == "systemctl" for call in calls)
    assert writes == []


def test_writes_parameters_and_installs_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The kernel defaults are active (lzo at 20 percent) and the service is
    # not enabled: the task writes the mismatching parameters, verifies them
    # by reading back, renders the unit template and enables the service.
    fixtures = _install_fixtures(tmp_path)
    calls, writes = _install_fake(monkeypatch, fixtures, enabled=False)
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    # enabled and shrinker_enabled already match; only the compressor and
    # the two percentages are rewritten.
    assert writes == [
        ("compressor", "zstd"),
        ("max_pool_percent", "50"),
        ("accept_threshold_percent", "100"),
    ]
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "zswap.service"] in calls
    unit = tmp_path / "systemd" / "zswap.service"
    assert unit.read_text(encoding="utf-8") == _expected_unit(
        TARGET, fixtures["params_dir"]
    )
    assert "compressor zstd" in (result.message or "")
    captured = capsys.readouterr()
    assert "max_pool_percent: 50" in captured.out


def test_force_mode_rewrites_everything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured, but the task is forced: every
    # parameter is written again.
    fixtures = _install_fixtures(tmp_path, current=TARGET)
    calls, writes = _install_fake(monkeypatch, fixtures, enabled=True)
    result = zswap_service.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert writes == [
        ("enabled", "Y"),
        ("compressor", "zstd"),
        ("max_pool_percent", "50"),
        ("accept_threshold_percent", "100"),
        ("shrinker_enabled", "Y"),
    ]
    assert ["systemctl", "enable", "zswap.service"] in calls


def test_parameters_match_but_service_disabled_installs_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The parameters already match, only the boot service is missing: no
    # sysfs writes happen, but the unit is installed and enabled.
    fixtures = _install_fixtures(tmp_path, current=TARGET)
    calls, writes = _install_fake(monkeypatch, fixtures, enabled=False)
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert writes == []
    assert ["systemctl", "enable", "zswap.service"] in calls
    unit = tmp_path / "systemd" / "zswap.service"
    assert unit.read_text(encoding="utf-8") == _expected_unit(
        TARGET, fixtures["params_dir"]
    )


def test_normalize_maps_bool_spellings() -> None:
    # The kernel reads boolean parameters back as Y or N, but also accepts
    # 1 and 0; the expected value decides which form is canonical, so the
    # comparison treats every spelling as equal.
    assert zswap_service._normalize("Y", "Y") == "Y"
    assert zswap_service._normalize("N", "1") == "Y"
    assert zswap_service._normalize("N", "0") == "N"
    assert zswap_service._normalize("Y", "y") == "Y"
    assert zswap_service._normalize("zstd", "zstd") == "zstd"


def test_nonstandard_bool_spelling_still_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The parameters match except that enabled reports the unusual "1"
    # spelling: normalization maps it to Y, so the task still skips.
    current = dict(TARGET)
    current["enabled"] = "1"
    fixtures = _install_fixtures(tmp_path, current=current)
    calls, writes = _install_fake(monkeypatch, fixtures, enabled=True)
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert all(call[0] == "systemctl" for call in calls)
    assert writes == []


def test_parameter_names_and_directory_come_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another parameter list, in another order, in another directory: a
    # task that kept the kernel interface in code would write the shipped
    # five parameters into the shipped path instead.
    settings = replace(
        make_config().zswap_service,
        parameter_names=("enabled", "compressor"),
        parameters_dir_path=tmp_path / "fixture" / "parameters",
    )
    fixtures = _install_fixtures(
        tmp_path,
        current={"enabled": "N", "compressor": "lzo"},
        settings=settings,
    )
    _calls, writes = _install_fake(monkeypatch, fixtures, enabled=False)
    ctx = _ctx(tmp_path)
    ctx = replace(ctx, config=replace(ctx.config, zswap_service=settings))
    result = zswap_service.task(ctx)
    assert result.success is True
    assert writes == [("enabled", "Y"), ("compressor", "zstd")]
    unit = tmp_path / "systemd" / settings.service_unit_name
    assert unit.read_text(encoding="utf-8") == _expected_unit(
        {"enabled": "Y", "compressor": "zstd"},
        fixtures["params_dir"],
        settings,
    )


def test_write_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The kernel rejects the compressor value: the task reports the error
    # and stops before touching the remaining parameters.
    fixtures = _install_fixtures(tmp_path)
    calls, writes = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        fail_write=frozenset({"compressor"}),
    )
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is False
    assert "cannot write compressor" in (result.error or "")
    # enabled matches the target and is not written; compressor is the
    # first mismatch and the only write attempt.
    assert writes == [("compressor", "zstd")]
    assert ["systemctl", "enable", "zswap.service"] not in calls


def test_missing_parameter_file_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A parameter attribute is absent (no zswap support or a removed
    # attribute): the read reports None, the write attempt fails and the
    # task reports the error.
    fixtures = _install_fixtures(tmp_path)
    (fixtures["params_dir"] / "max_pool_percent").unlink()
    _ = _install_fake(monkeypatch, fixtures, enabled=False)
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is False
    assert "cannot write max_pool_percent" in (result.error or "")


def test_missing_template_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the parameters are configured but the
    # service cannot be written, so the task reports the error.
    fixtures = _install_fixtures(tmp_path)
    fixtures["template"].unlink()
    calls, _ = _install_fake(monkeypatch, fixtures, enabled=False)
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is True
    assert "template" in (result.error or "")
    assert ["systemctl", "enable", "zswap.service"] not in calls


def test_systemctl_enable_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # systemctl enable fails after the parameters were configured: the task
    # reports the error and marks the run as changed.
    fixtures = _install_fixtures(tmp_path)
    _calls, writes = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        fail=lambda command: command[:2] == ["systemctl", "enable"],
    )
    result = zswap_service.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is True
    assert "systemd setup failed" in (result.error or "")
    assert ("compressor", "zstd") in writes
