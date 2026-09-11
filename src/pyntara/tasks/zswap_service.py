"""Task zswap_service: configure the zswap compressed swap cache.

Zswap stores pages that are in the process of being swapped out in a
compressed RAM pool before they reach the backing swapfile, trading CPU
cycles for reduced swap I/O. The task writes the configured parameters
into /sys/module/zswap/parameters and installs a systemd oneshot service
that repeats the same writes at every boot. Kernel 7.0 (Kubuntu 26.04)
exposes exactly five parameters: enabled, compressor, max_pool_percent,
accept_threshold_percent and shrinker_enabled; the zpool and
same_filled_pages_enabled attributes no longer exist because zsmalloc is
the only pool and same-filled page handling is always on. The unit file
is rendered from the template at task_data/zswap_service/zswap.service
with the ExecStart block substituted (string.Template); the service never
reads config.toml itself. The task is idempotent: it skips when every
parameter already equals the configured value and the service is enabled;
force mode rewrites all parameters.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from string import Template

from pyntara.config import ZswapServiceConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command, service_is_enabled, task_data_dir


def _parameter_paths(cfg: ZswapServiceConfig) -> dict[str, Path]:
    """The kernel attribute file of every configured parameter."""

    return {
        name: cfg.parameters_dir_path / name for name in cfg.parameter_names
    }


def _target_values(cfg: ZswapServiceConfig) -> dict[str, str]:
    """Canonical target values keyed by parameter name.

    The value of a parameter is the key of the section with the same name:
    a boolean key is written as the Y/N spelling the sysfs attributes
    report, a number and a string as they are, so the rendered unit, the
    idempotency comparison and the read-back verification all share one
    representation.
    """

    values: dict[str, str] = {}
    for name in cfg.parameter_names:
        value = getattr(cfg, name)
        if isinstance(value, bool):
            values[name] = "Y" if value else "N"
        else:
            values[name] = str(value)
    return values


def _normalize(expected: str, value: str) -> str:
    """Canonical spelling of one read-back value.

    A boolean attribute is reported by the kernel as Y or N but also
    accepts 1 and 0; the expected value decides whether the attribute is a
    boolean, so the comparison maps every accepted spelling to the
    canonical Y/N form and never depends on kernel quirks.
    """

    if expected in ("Y", "N"):
        return "Y" if value.upper() in ("Y", "1") else "N"
    return value


def _read_value(path: Path) -> str | None:
    """Current value of one zswap parameter, stripped, or None when absent."""

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_sysfs(path: Path, value: str) -> None:
    """Write one value into a sysfs attribute file.

    Raises OSError when the attribute does not exist or the kernel rejects
    the value.
    """

    path.write_text(value, encoding="utf-8")


def _render_unit(
    template_path: Path, target: dict[str, str], paths: dict[str, Path]
) -> str:
    """Render the service unit template with the ExecStart block substituted.

    One ExecStart line per parameter writes the exact configured value, so
    the boot service reproduces the install-time configuration. The block
    is fully expanded here, so the template carries no shell variables of
    its own and substitute cannot trip on stray dollar signs.
    """

    lines = [
        f"ExecStart=/bin/sh -c 'echo {value} > {paths[name]}'"
        for name, value in target.items()
    ]
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines="\n".join(lines))


def _write_unit_file(unit_dir: Path, service_name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / service_name).write_text(content, encoding="utf-8")


def task(ctx: Context) -> TaskResult:
    """Write the zswap parameters and install the boot service; skip when done.

    The goal is reached when every parameter in /sys/module/zswap/parameters
    already equals the configured value and the service is enabled; the task
    then returns changed=False. Otherwise it writes the mismatching
    parameters (all of them in force mode), verifies them by reading back,
    writes the unit file and enables the service. Every step is reported to
    stdout: measurements and decisions as single lines that include their
    result, long-running commands as a line before and a line after. Any
    failure is returned as an error TaskResult: the runner continues with
    the remaining tasks and never stops here.
    """

    cfg = ctx.config.zswap_service
    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    service_name = cfg.service_unit_name
    target = _target_values(cfg)
    paths = _parameter_paths(cfg)

    current: dict[str, str | None] = {}
    for name in cfg.parameter_names:
        value = _read_value(paths[name])
        current[name] = value
        shown = "absent" if value is None else value
        _log(f"reading {paths[name]}: {shown}")

    mismatches: list[str] = []
    for name in cfg.parameter_names:
        value = current[name]
        if value is None or _normalize(target[name], value) != target[name]:
            mismatches.append(name)

    enabled = service_is_enabled(service_name, timeout)
    _log(
        f"checking autorun service {service_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )

    if not force and not mismatches and enabled:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    for name in cfg.parameter_names:
        if force or name in mismatches:
            _log(f"writing {paths[name]}: {target[name]}")
            try:
                _write_sysfs(paths[name], target[name])
            except OSError as exc:
                return TaskResult(success=False, error=f"cannot write {name}: {exc}")
            changed = True

    if changed:
        _log("verifying zswap parameters")
        problems: list[str] = []
        for name in cfg.parameter_names:
            value = _read_value(paths[name])
            if value is None or _normalize(target[name], value) != target[name]:
                problems.append(f"{name} mismatch")
        if problems:
            return TaskResult(success=False, changed=True, error="; ".join(problems))
        _log("verification passed")

    if not enabled:
        changed = True

    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / cfg.unit_template_file_name
    )
    _log(f"rendering unit template from {template_path}")
    try:
        content = _render_unit(template_path, target, paths)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot read unit template: {exc}"
        )
    unit_dir = ctx.config.engine.systemd_unit_dir
    _log(f"writing unit file {unit_dir / service_name}")
    try:
        _write_unit_file(unit_dir, service_name, content)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot write unit file: {exc}"
        )
    _log("unit file written")
    try:
        _log("reloading systemd: systemctl daemon-reload")
        run_command(["systemctl", "daemon-reload"], timeout=timeout)
        _log("systemd reloaded")
        _log(f"enabling service: systemctl enable {service_name}")
        run_command(["systemctl", "enable", service_name], timeout=timeout)
        _log("service enabled")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False, changed=True, error=f"systemd setup failed: {exc}"
        )
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"zswap configured: compressor {cfg.compressor}, "
            f"max pool {cfg.max_pool_percent}%, accept threshold "
            f"{cfg.accept_threshold_percent}%, shrinker "
            f"{'on' if cfg.shrinker_enabled else 'off'}"
        ),
    )
