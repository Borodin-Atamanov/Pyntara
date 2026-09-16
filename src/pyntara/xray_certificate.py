"""HTTPS of the panel: a trusted certificate or a self-signed one.

The panel must never serve plain HTTP, so this module owns stage 4 of the
task: it decides whether the Let's Encrypt HTTP-01 challenge can reach
this machine, issues a trusted certificate through acme.sh when it can,
generates a self-signed certificate with openssl when it cannot, and
replaces a self-signed certificate with a trusted one as soon as port 80
becomes reachable. A certificate the task does not own is left alone
(docs/spec/3x-ui.md).
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import time
from pathlib import Path

from pyntara import xui as xui_client
from pyntara.config import EngineConfig, ThreeXuiXraySetupConfig
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    ensure_port_free,
    install_package_once,
    package_is_installed,
    run_command,
    substituted_command,
)
from pyntara.xray_facts import _detect_server_ip, _RunFacts
from pyntara.xray_panel import _panel_command


def _is_private_ipv4(address: str, networks: tuple[str, ...]) -> bool:
    """True when the address falls inside one of the configured networks.

    The networks are the private IPv4 ranges of the section, the RFC1918
    set by default. A machine whose own interface carries only an address
    from such a range cannot serve the Let's Encrypt HTTP-01 challenge on
    the ACME port unless the router forwards it. The standard library
    decides the containment, so the notation of the config is the usual
    CIDR notation and no prefix arithmetic lives here.
    """

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(
        parsed in ipaddress.ip_network(network, strict=True)
        for network in networks
    )


def _probe_port_80_forward(
    cfg: ThreeXuiXraySetupConfig, timeout: float, facts: _RunFacts
) -> bool:
    """True when external port 80 is forwarded back to this machine.

    Binds a temporary HTTP listener on the ACME port and connects to the
    detected public address from the machine itself. A successful
    connection means the router forwards external port 80 here; any
    failure (no forward, no hairpin NAT, busy port, missing python3) is
    treated as not confirmed.
    """

    public_ip = _detect_server_ip(facts)
    if public_ip is None:
        return False
    _log(
        f"probing whether external port {cfg.acme_port} reaches "
        f"{public_ip} here"
    )
    try:
        listener = subprocess.Popen(
            substituted_command(
                cfg.acme_port_listener_command,
                {"port": str(cfg.acme_port)},
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        time.sleep(cfg.probe_listener_start_seconds)
        probe_url = cfg.port_forward_probe_url_format.format(
            host=public_ip, port=cfg.acme_port
        )
        try:
            result = run_command(
                substituted_command(
                    cfg.port_forward_probe_command,
                    {
                        "timeout_seconds": str(
                            cfg.probe_port_80_timeout_seconds
                        )
                    },
                )
                + [probe_url],
                check=False,
                capture=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            _log(f"port {cfg.acme_port} did not answer: no forward confirmed")
            return False
        if result.returncode != 0:
            _log(f"port {cfg.acme_port} did not answer: no forward confirmed")
        else:
            _log(f"port {cfg.acme_port} answered: the forward is confirmed")
        return result.returncode == 0
    finally:
        listener.terminate()
        try:
            listener.wait(timeout=cfg.probe_timeout_seconds)
        except subprocess.TimeoutExpired:
            listener.kill()


def _ssl_reachable(
    cfg: ThreeXuiXraySetupConfig, timeout: float, facts: _RunFacts
) -> bool:
    """Whether the Let's Encrypt HTTP-01 challenge can be served.

    A machine with a public address on an interface (a VPS) serves the
    challenge directly. A machine behind NAT can only when the router
    forwards external port 80 back to it, which the self-test confirms.
    When the local address cannot be determined the attempt is allowed,
    so a working setup is never skipped by accident.
    """

    local = facts.local_addresses
    if not local or not _is_private_ipv4(local[0], cfg.private_ipv4_networks):
        return True
    return _probe_port_80_forward(cfg, timeout, facts)


def _acme_path(cfg: ThreeXuiXraySetupConfig) -> Path:
    """The acme.sh binary under the current user's home directory.

    The directory and the file name of the tool are config values, so a
    release that installs itself elsewhere is a config change.
    """

    return Path.home() / cfg.acme_dir_relative_path / cfg.acme_file_name


def _ensure_acme(cfg: ThreeXuiXraySetupConfig, timeout: float) -> bool:
    """Install acme.sh via get.acme.sh when it is not present yet.

    True when the acme.sh binary exists after the call. A missing binary
    after an install attempt is a failure.
    """

    acme = _acme_path(cfg)
    if acme.is_file():
        return True
    try:
        run_command(
            list(cfg.acme_install_command),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return acme.is_file()


def _issue_ip_certificate(
    cfg: ThreeXuiXraySetupConfig, ip: str, timeout: float
) -> tuple[bool, str]:
    """Issue and install a Let's Encrypt IP certificate for the address.

    Mirrors the official installer's setup_ip_certificate: runs acme.sh
    with the shortlived profile over the standalone HTTP-01 listener,
    installs the certificate under /root/cert/ip and points the panel at
    it through `x-ui cert`. Returns (ok, message).
    """

    if not _ensure_acme(cfg, timeout):
        return False, "acme.sh install failed"
    # acme.sh installcert does not create the certificate directory
    # itself; the installer creates it with mkdir -p before the call.
    cfg.cert_dir.mkdir(parents=True, exist_ok=True)
    acme = str(_acme_path(cfg))
    reload_cmd = cfg.acme_reload_command.format(
        service_unit_name=cfg.service_unit_name
    )
    steps = [
        substituted_command(cfg.acme_set_default_ca_command, {"acme": acme}),
        substituted_command(
            cfg.acme_issue_command,
            {"acme": acme, "domain": ip, "http_port": str(cfg.acme_port)},
        ),
        substituted_command(
            cfg.acme_installcert_command,
            {
                "acme": acme,
                "domain": ip,
                "key_file": str(cfg.cert_privkey),
                "fullchain_file": str(cfg.cert_fullchain),
                "reload_command": reload_cmd,
            },
        ),
        substituted_command(cfg.acme_upgrade_command, {"acme": acme}),
    ]
    for command in steps:
        try:
            result = run_command(
                command, check=False, capture=True, timeout=timeout
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"acme.sh step failed: {exc}"
        if result.returncode != 0:
            return False, (
                f"acme.sh step failed (exit {result.returncode}): "
                f"{' '.join(command[1:3])}"
            )
    if not cfg.cert_fullchain.is_file() or not cfg.cert_privkey.is_file():
        return False, "certificate files missing after acme.sh installcert"
    try:
        os.chmod(cfg.cert_privkey, cfg.cert_privkey_file_mode)
        os.chmod(cfg.cert_fullchain, cfg.cert_fullchain_file_mode)
    except OSError as exc:
        return False, f"cannot secure certificate permissions: {exc}"
    try:
        run_command(
            _panel_command(
                cfg,
                cfg.panel_certificate_command,
                fullchain=str(cfg.cert_fullchain),
                privkey=str(cfg.cert_privkey),
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot point panel at certificate: {exc}"
    # The panel only serves TLS after a restart; the acme.sh reloadcmd
    # already restarted it before the certificate paths were set, so the
    # restart must happen again after x-ui cert.
    try:
        run_command(
            substituted_command(
                cfg.service_restart_command,
                {"service_unit_name": cfg.service_unit_name},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot restart {cfg.service_unit_name}: {exc}"
    return True, "certificate issued"


def _ensure_openssl(engine: EngineConfig, timeout: float) -> bool:
    """True when the openssl binary is present, installing it if needed.

    The self-signed certificate is generated with the external openssl
    tool (project rule: use a ready tool instead of writing a
    generator); a missing package is installed through the shared
    helper, so the setup never depends on a preinstalled package.
    """

    if package_is_installed(engine, "openssl", timeout):
        return True
    _log("openssl is missing, installing the package")
    ok, _ = install_package_once(engine, "openssl", timeout)
    if ok:
        _log("openssl installed")
    return ok


def _certificate_subject_name(
    cfg: ThreeXuiXraySetupConfig, timeout: float, facts: _RunFacts
) -> str:
    """The CN for the self-signed certificate: a known address if any.

    The detected public address is preferred, then the local IPv4, then
    a fixed name. The certificate is self-signed and untrusted either
    way, so the subject only labels it and does not affect the handshake.
    """

    ip = _detect_server_ip(facts)
    if ip is None and facts.local_addresses:
        ip = facts.local_addresses[0]
    return ip or "3x-ui"


def _self_signed_not_expired(
    cfg: ThreeXuiXraySetupConfig, timeout: float
) -> bool:
    """True when the self-signed certificate is still valid.

    A missing or broken openssl is treated as valid so a rerun never
    churns a working panel: without openssl the certificate cannot be
    regenerated anyway.
    """

    try:
        result = run_command(
            substituted_command(
                cfg.openssl_check_command,
                {"fullchain": str(cfg.self_signed_cert_fullchain)},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True
    return result.returncode == 0


def _ensure_self_signed_cert(
    engine: EngineConfig,
    cfg: ThreeXuiXraySetupConfig,
    timeout: float,
    facts: _RunFacts,
) -> tuple[bool, str]:
    """Ensure the panel serves HTTPS with a self-signed certificate.

    When the panel already points at our self-signed files and they are
    still valid, nothing changes. Otherwise the files are generated with
    openssl (the package is installed when absent), the panel is pointed
    at them through `x-ui cert` and restarted. Returns (changed,
    message): (False, "") when nothing was needed, (True, "self-signed
    certificate configured") after the setup, and (False, error) when
    the panel cannot be brought to HTTPS and stays on HTTP.
    """

    self_signed_path = str(cfg.self_signed_cert_fullchain)
    current = xui_client.panel_cert_value(cfg, timeout)
    if current is not None and current != self_signed_path:
        return False, ""
    files_ok = (
        cfg.self_signed_cert_fullchain.is_file()
        and cfg.self_signed_cert_privkey.is_file()
    )
    needs_generation = not files_ok
    if files_ok:
        needs_generation = not _self_signed_not_expired(cfg, timeout)
    if current == self_signed_path and not needs_generation:
        _log("self-signed certificate already configured")
        return False, ""
    if needs_generation:
        _log("generating a self-signed certificate")
        if not _ensure_openssl(engine, timeout):
            return (
                False,
                "openssl unavailable: cannot generate a self-signed certificate",
            )
        cfg.self_signed_cert_dir.mkdir(parents=True, exist_ok=True)
        subject = _certificate_subject_name(cfg, timeout, facts)
        try:
            run_command(
                substituted_command(
                    cfg.openssl_generate_command,
                    {
                        "subject": cfg.openssl_subject_template.format(
                            subject=subject
                        ),
                        "key_file": str(cfg.self_signed_cert_privkey),
                        "fullchain_file": str(cfg.self_signed_cert_fullchain),
                    },
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"cannot generate a self-signed certificate: {exc}"
        try:
            os.chmod(cfg.self_signed_cert_privkey, cfg.cert_privkey_file_mode)
            os.chmod(cfg.self_signed_cert_fullchain, cfg.cert_fullchain_file_mode)
        except OSError as exc:
            return False, f"cannot secure certificate permissions: {exc}"
    try:
        run_command(
            _panel_command(
                cfg,
                cfg.panel_certificate_command,
                fullchain=str(cfg.self_signed_cert_fullchain),
                privkey=str(cfg.self_signed_cert_privkey),
            ),
            timeout=timeout,
        )
        run_command(
            substituted_command(
                cfg.service_restart_command,
                {"service_unit_name": cfg.service_unit_name},
            ),
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot point panel at the self-signed certificate: {exc}"
    _log("self-signed certificate configured")
    return True, "self-signed certificate configured"


def _issue_trusted_cert(
    engine: EngineConfig, cfg: ThreeXuiXraySetupConfig, ip: str, timeout: float
) -> TaskResult | None:
    """Issue a trusted Let's Encrypt certificate and report the result.

    Frees the ACME port, issues the certificate through acme.sh and
    returns a TaskResult carrying the change or the warning. Shared by
    the no-certificate and the self-signed-upgrade paths of the HTTPS
    stage.
    """

    _log(f"issuing Let's Encrypt IP certificate for {ip}")
    try:
        freed = ensure_port_free(
            engine,
            cfg.acme_port,
            cfg.service_unit_name,
            timeout,
            service_process_name=cfg.service_process_name,
        )
    except RuntimeError as exc:
        return TaskResult(success=True, changed=False, warnings=(str(exc),))
    if freed:
        _log(f"ACME port {cfg.acme_port}: {freed}")
    ok, message = _issue_ip_certificate(cfg, ip, timeout)
    if not ok:
        return TaskResult(
            success=True,
            changed=False,
            warnings=(f"SSL certificate setup failed: {message}",),
        )
    _log(f"SSL certificate configured ({message})")
    return TaskResult(
        success=True, changed=True, message="SSL certificate configured"
    )


def _stage_ssl(
    engine: EngineConfig,
    cfg: ThreeXuiXraySetupConfig,
    timeout: float,
    facts: _RunFacts,
) -> TaskResult | None:
    """Ensure the panel serves HTTPS; returns a TaskResult when changed.

    When ssl_enabled is set, the panel must serve HTTPS, never plain
    HTTP. A trusted Let's Encrypt IP certificate is issued when the
    HTTP-01 challenge can reach the machine; when it cannot (the machine
    is behind NAT without a port-80 forward) a self-signed certificate
    is generated instead, so the admin traffic stays encrypted. A panel
    that already carries a foreign certificate is left alone; a
    self-signed certificate installed by this task is replaced by a
    trusted one as soon as port 80 becomes reachable. Returns None when
    nothing changed and a TaskResult with the message or the warning
    otherwise, so the caller merges it into the task result.
    """

    if not cfg.ssl_enabled:
        return None
    cert = xui_client.panel_cert_value(cfg, timeout)
    self_signed = str(cfg.self_signed_cert_fullchain)
    if cert is not None and cert != self_signed:
        _log("SSL certificate already configured")
        return None
    if cert == self_signed:
        # Our self-signed certificate is installed; only a now-reachable
        # port 80 justifies replacing it with a trusted one.
        if not _ssl_reachable(cfg, timeout, facts):
            _log("self-signed certificate already configured")
            return None
        ip = _detect_server_ip(facts)
        if ip is not None:
            return _issue_trusted_cert(engine, cfg, ip, timeout)
        return None
    ip = _detect_server_ip(facts)
    if ip is not None and _ssl_reachable(cfg, timeout, facts):
        return _issue_trusted_cert(engine, cfg, ip, timeout)
    ok, message = _ensure_self_signed_cert(engine, cfg, timeout, facts)
    if ok:
        return TaskResult(
            success=True,
            changed=True,
            message=(
                "panel serves HTTPS with a self-signed certificate; "
                "forward port 80 and re-run the task to get a trusted "
                "Let's Encrypt certificate"
            ),
        )
    if message:
        return TaskResult(
            success=True,
            changed=False,
            warnings=(f"SSL setup failed: {message}; panel serves HTTP",),
        )
    return None
