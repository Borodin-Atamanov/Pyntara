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
import time
from datetime import datetime
from pathlib import Path
from string import Template

from pyntara.config import ZswapServiceConfig
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "task_data" / "zswap_service" / "zswap.service"
ZSWAP_SERVICE_NAME = "zswap.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
ZSWAP_PARAMS_DIR = Path("/sys/module/zswap/parameters")

# The five parameters of kernel 7.0, in the order the task writes them:
# enable the cache first, then the compressor, the pool ceiling, the
# re-accept threshold and the shrinker.
PARAM_ORDER = (
    "enabled",
    "compressor",
    "max_pool_percent",
    "accept_threshold_percent",
    "shrinker_enabled",
)
PARAM_PATHS = {
    "enabled": ZSWAP_PARAMS_DIR / "enabled",
    "compressor": ZSWAP_PARAMS_DIR / "compressor",
    "max_pool_percent": ZSWAP_PARAMS_DIR / "max_pool_percent",
    "accept_threshold_percent": ZSWAP_PARAMS_DIR / "accept_threshold_percent",
    "shrinker_enabled": ZSWAP_PARAMS_DIR / "shrinker_enabled",
}

# The task name from the catalog; the module file name matches it
# (task-model contract), so the prefix is always correct.
TASK_NAME = __name__.rsplit(".", 1)[-1]

# Monotonic time of the previous progress line; presentation state only, not
# business data (architecture contract forbids business data here, not this).
_last_log_time = 0.0


def _log(message: str) -> None:
    """Print one progress line for this task, flushed to stdout.

    A timestamp in the project datetime format YYYY-MM-DD-HH-MM-SS is
    prepended only when more than one second has passed since the previous
    line, so bursts of lines stay compact. inst.sh tees stdout into the
    install log, so every decision and action of the task is visible in the
    terminal and in the log.
    """

    global _last_log_time
    now = time.monotonic()
    if now - _last_log_time >= 1.0:
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        prefix = f"{timestamp} {TASK_NAME}:"
        _last_log_time = now
    else:
        prefix = f"{TASK_NAME}:"
    print(f"{prefix} {message}", flush=True)


def _target_values(cfg: ZswapServiceConfig) -> dict[str, str]:
    """Canonical target values keyed by parameter name.

    Boolean parameters use the Y/N spelling the sysfs attributes report,
    so the rendered unit, the idempotency comparison and the read-back
    verification all share one representation.
    """

    return {
        "enabled": "Y" if cfg.enabled else "N",
        "compressor": cfg.compressor,
        "max_pool_percent": str(cfg.max_pool_percent),
        "accept_threshold_percent": str(cfg.accept_threshold_percent),
        "shrinker_enabled": "Y" if cfg.shrinker_enabled else "N",
    }


def _normalize(name: str, value: str) -> str:
    """Canonical spelling of one read-back value.

    Boolean parameters are reported by the kernel as Y or N but also
    accept 1 and 0; normalization maps every accepted spelling to the
    canonical Y/N form so the comparison never depends on kernel quirks.
    """

    if name in ("enabled", "shrinker_enabled"):
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


def _service_enabled(name: str, timeout: float) -> bool:
    """True when the systemd service is enabled for boot."""

    result = run_command(
        ["systemctl", "is-enabled", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "enabled"


def _render_unit(template_path: Path, target: dict[str, str]) -> str:
    """Render the service unit template with the ExecStart block substituted.

    One ExecStart line per parameter writes the exact configured value, so
    the boot service reproduces the install-time configuration. The block
    is fully expanded here, so the template carries no shell variables of
    its own and substitute cannot trip on stray dollar signs.
    """

    lines = [
        f"ExecStart=/bin/sh -c 'echo {target[name]} > {PARAM_PATHS[name]}'"
        for name in PARAM_ORDER
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
    force = "zswap_service" in ctx.force_tasks
    target = _target_values(cfg)

    current: dict[str, str | None] = {}
    for name in PARAM_ORDER:
        value = _read_value(PARAM_PATHS[name])
        current[name] = value
        shown = "absent" if value is None else value
        _log(f"reading {PARAM_PATHS[name]}: {shown}")

    mismatches: list[str] = []
    for name in PARAM_ORDER:
        value = current[name]
        if value is None or _normalize(name, value) != target[name]:
            mismatches.append(name)

    enabled = _service_enabled(ZSWAP_SERVICE_NAME, timeout)
    _log(
        f"checking autorun service {ZSWAP_SERVICE_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )

    if not force and not mismatches and enabled:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    for name in PARAM_ORDER:
        if force or name in mismatches:
            _log(f"writing {PARAM_PATHS[name]}: {target[name]}")
            try:
                _write_sysfs(PARAM_PATHS[name], target[name])
            except OSError as exc:
                return TaskResult(success=False, error=f"cannot write {name}: {exc}")
            changed = True

    if changed:
        _log("verifying zswap parameters")
        problems: list[str] = []
        for name in PARAM_ORDER:
            value = _read_value(PARAM_PATHS[name])
            if value is None or _normalize(name, value) != target[name]:
                problems.append(f"{name} mismatch")
        if problems:
            return TaskResult(success=False, changed=True, error="; ".join(problems))
        _log("verification passed")

    if not enabled:
        changed = True

    _log(f"rendering unit template from {TEMPLATE_PATH}")
    try:
        content = _render_unit(TEMPLATE_PATH, target)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot read unit template: {exc}"
        )
    _log(f"writing unit file {SYSTEMD_UNIT_DIR / ZSWAP_SERVICE_NAME}")
    try:
        _write_unit_file(SYSTEMD_UNIT_DIR, ZSWAP_SERVICE_NAME, content)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot write unit file: {exc}"
        )
    _log("unit file written")
    try:
        _log("reloading systemd: systemctl daemon-reload")
        run_command(["systemctl", "daemon-reload"], timeout=timeout)
        _log("systemd reloaded")
        _log(f"enabling service: systemctl enable {ZSWAP_SERVICE_NAME}")
        run_command(["systemctl", "enable", ZSWAP_SERVICE_NAME], timeout=timeout)
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
