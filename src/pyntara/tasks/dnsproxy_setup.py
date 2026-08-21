# Install and configure dnsproxy as the system-wide DNS resolver.

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from string import Template

from pyntara import metrics
from pyntara.config import DnsproxySetupConfig
from pyntara.config_edit import sync_directives_by_key
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.nextdns_profile import select_profile_from_vault
from pyntara.tasks.local_vault_setup import open_source_vault
from pyntara.utils import (
    dpkg_architecture,
    ensure_root_owner,
    run_command,
    service_is_active,
    service_is_enabled,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OS_RELEASE_PATH = Path("/etc/os-release")
VERSION_PATTERN = re.compile(r"v?(\d+\.\d+\.\d+)")


def _release_json(repo: str, timeout: float) -> dict[str, object]:
    result = run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            f"https://api.github.com/repos/{repo}/releases/latest",
        ],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot fetch dnsproxy release: exit {result.returncode}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise TypeError("dnsproxy release response is not an object")
    return value


def _asset_for_architecture(release: dict[str, object], arch: str) -> tuple[str, str]:
    tag = release.get("tag_name")
    assets = release.get("assets")
    if not isinstance(tag, str) or not tag or not isinstance(assets, list):
        raise RuntimeError("dnsproxy release has no usable tag or assets")
    suffix = {"amd64": "amd64", "arm64": "arm64", "armhf": "arm7"}.get(arch)
    if suffix is None:
        raise RuntimeError(f"unsupported dnsproxy architecture: {arch}")
    expected = f"dnsproxy-linux-{suffix}-{tag}.tar.gz"
    for asset in assets:
        if (
            isinstance(asset, dict)
            and asset.get("name") == expected
            and isinstance(asset.get("browser_download_url"), str)
        ):
            return expected, asset["browser_download_url"]
    raise RuntimeError(f"release {tag} has no asset {expected}")


def _installed_version(path: Path, timeout: float) -> str | None:
    try:
        result = run_command(
            [str(path), "--version"], check=False, capture=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = VERSION_PATTERN.search(result.stdout + result.stderr)
    return match.group(1) if match else None


def _version_from_tag(tag: str) -> str:
    match = VERSION_PATTERN.search(tag)
    if not match:
        raise RuntimeError(f"cannot parse dnsproxy release version: {tag}")
    return match.group(1)


def _download_binary(
    cfg: DnsproxySetupConfig, url: str, name: str, timeout: float
) -> Path:
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    archive = cfg.download_dir / name
    run_command(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--output",
            str(archive),
            url,
        ],
        timeout=timeout,
    )
    extract_dir = cfg.download_dir / "extract"
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir()
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(extract_dir, filter="data")
    candidates = list(extract_dir.rglob("dnsproxy"))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise RuntimeError("dnsproxy archive does not contain exactly one binary")
    staged = cfg.download_dir / "dnsproxy.staged"
    shutil.copyfile(candidates[0], staged)
    staged.chmod(0o755)
    return staged


def _upstreams(cfg: DnsproxySetupConfig, profile_id: str) -> tuple[str, ...]:
    return tuple(
        format_string.format(profile_id=profile_id)
        for format_string in (
            cfg.doh_url_format,
            cfg.dot_host_format,
            cfg.doq_host_format,
        )
    )


def _command(cfg: DnsproxySetupConfig, profile_id: str) -> list[str]:
    command = [
        str(cfg.binary_path),
        "--listen=" + cfg.listen_address,
        "--port=" + str(cfg.listen_port),
    ]
    for upstream in _upstreams(cfg, profile_id):
        command.append("--upstream=" + upstream)
    for fallback in cfg.fallback_resolvers:
        command.append("--fallback=" + fallback)
    for bootstrap in cfg.bootstrap_resolvers:
        command.append("--bootstrap=" + bootstrap)
    command.extend(
        (
            "--upstream-mode=" + cfg.upstream_mode,
            "--output=" + str(cfg.query_log_path),
            "--verbose",
        )
    )
    if cfg.cache_enabled:
        command.append("--cache")
    return command


def _render_service(cfg: DnsproxySetupConfig, profile_id: str) -> str:
    template_path = REPO_ROOT / cfg.service_template_path
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_start=" ".join(_command(cfg, profile_id)),
        service_restart_seconds=cfg.service_restart_seconds,
    )


def _write_profile_id(cfg: DnsproxySetupConfig, profile_id: str) -> None:
    cfg.profile_id_file_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.profile_id_file_path.write_text(f"{profile_id}\n", encoding="utf-8")
    cfg.profile_id_file_path.chmod(cfg.profile_id_file_mode)
    ensure_root_owner(cfg.profile_id_file_path)


def _profile_id(ctx: Context) -> str | None:
    opened = open_source_vault(ctx.config.local_vault_setup, ctx.vault_password)
    kp = opened[0] if opened is not None else metrics.open_runtime_vault(ctx.config)
    if kp is None:
        return None
    return select_profile_from_vault(
        kp, ctx.config.nextdns_setup_system_wide.vault_group_title
    )


def _write_resolver_dropin(cfg: DnsproxySetupConfig) -> bool:
    path = cfg.resolved_conf_dir / cfg.resolved_dropin_file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = sync_directives_by_key(
        path,
        (cfg.resolved_dns_directive, cfg.resolved_domains_directive),
        cfg.resolved_dropin_header,
        cfg.resolved_section,
    )
    path.chmod(cfg.resolved_dropin_file_mode)
    ensure_root_owner(path)
    return changed


def _wait_active(cfg: DnsproxySetupConfig, timeout: float) -> bool:
    import time

    for _ in range(cfg.start_check_attempts):
        time.sleep(cfg.start_check_retry_delay_seconds)
        if service_is_active(cfg.service_unit_name, timeout):
            return True
    return False


def task(ctx: Context) -> TaskResult:
    cfg = ctx.config.dnsproxy_setup
    timeout = ctx.config.engine.command_timeout_seconds
    profile_id = _profile_id(ctx)
    if profile_id is None:
        return TaskResult(success=False, error="cannot select a NextDNS profile")
    try:
        release = _release_json(cfg.github_repo, timeout)
        tag = str(release["tag_name"])
        asset_name, asset_url = _asset_for_architecture(
            release, dpkg_architecture(timeout)
        )
        target_version = _version_from_tag(tag)
    except (
        RuntimeError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        return TaskResult(success=False, error=str(exc))
    installed = _installed_version(cfg.binary_path, timeout)
    changed = False
    try:
        if installed != target_version:
            staged = _download_binary(cfg, asset_url, asset_name, timeout)
            cfg.binary_path.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(cfg.binary_path)
            ensure_root_owner(cfg.binary_path)
            changed = True
        cfg.query_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not cfg.query_log_path.exists():
            cfg.query_log_path.touch(mode=cfg.query_log_mode)
        cfg.query_log_path.chmod(cfg.query_log_mode)
        ensure_root_owner(cfg.query_log_path)
        service_path = cfg.service_unit_path
        service_content = _render_service(cfg, profile_id)
        if (
            not service_path.exists()
            or service_path.read_text(encoding="utf-8") != service_content
        ):
            service_path.write_text(service_content, encoding="utf-8")
            ensure_root_owner(service_path)
            run_command(list(cfg.daemon_reload_command), timeout=timeout)
            changed = True
        if not service_is_enabled(cfg.service_unit_name, timeout):
            run_command(["systemctl", "enable", cfg.service_unit_name], timeout=timeout)
            changed = True
        active = service_is_active(cfg.service_unit_name, timeout)
        if not active or changed or ctx.force_tasks.intersection({"dnsproxy_setup"}):
            run_command(
                ["systemctl", "restart" if active else "start", cfg.service_unit_name],
                timeout=timeout,
            )
            if not _wait_active(cfg, timeout):
                return TaskResult(
                    success=False,
                    changed=True,
                    error="dnsproxy service did not become active",
                )
            changed = True
        if _write_resolver_dropin(cfg):
            run_command(list(cfg.restart_resolved_command), timeout=timeout)
            changed = True
        if cfg.manage_networkmanager:
            check = run_command(
                list(cfg.nmcli_check_command), check=False, timeout=timeout
            )
            if check.returncode == 0:
                connections = run_command(
                    list(cfg.nmcli_list_command), capture=True, timeout=timeout
                ).stdout.splitlines()
                for connection in connections:
                    if connection.strip():
                        command = [
                            part.replace("{connection}", connection).replace(
                                "{value}", "true"
                            )
                            for part in cfg.nmcli_modify_command
                        ]
                        run_command(command, timeout=timeout)
        run_command(list(cfg.verification_command), timeout=timeout)
        _write_profile_id(cfg, profile_id)
    except (OSError, subprocess.SubprocessError, tarfile.TarError, RuntimeError) as exc:
        return TaskResult(
            success=False, changed=changed, error=f"dnsproxy setup failed: {exc}"
        )
    return TaskResult(
        success=True,
        changed=changed,
        message=f"dnsproxy active with NextDNS profile {profile_id}",
    )
