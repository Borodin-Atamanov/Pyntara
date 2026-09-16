"""Client half of the panel: the local proxy and its routing policy.

This module owns stages 6 and 7 of the task: the inbound of this machine
that serves SOCKS5 and HTTP on a loopback address, the policy that
decides where the traffic of that inbound leaves, and the pool of remote
exits the remote classes go through, which carries the remote server of
the profile and the nodes the panel builds from its outbound
subscriptions (docs/spec/3x-ui.md).

The pool exists on every machine, the remote server itself included: the
machine whose profile points at itself never creates the outbound that
would connect it to itself, so its pool covers the subscription nodes
only and falls back to the direct outbound.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace

from pyntara import routing_policy, xray_client
from pyntara import xui as xui_client
from pyntara.config import ThreeXuiXraySetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.tasks.local_vault_setup import open_source_vault
from pyntara.utils import run_command, substituted_command, trim_whitespace
from pyntara.xray_facts import _RunFacts


def _remote_profile(
    cfg: ThreeXuiXraySetupConfig, ctx: Context
) -> tuple[routing_policy.VlessProfile | None, str]:
    """The profile of the remote server, and a reason when it is unavailable.

    The vless link comes from the entry named by
    client_profile_entry_title in the source vault, production first: the
    link is the single source of truth for the profile, because it already
    carries the REALITY public key, the short id and the spider path. The
    reason is an empty string when the profile is returned and a sentence
    for the operator when it is not, so exactly one of the two is set.
    """

    source = open_source_vault(
        ctx.repo_root, ctx.config.local_vault_setup, ctx.vault_password
    )
    if source is None:
        return None, (
            "no source vault could be opened: the local proxy is not configured"
        )
    kp, path = source
    entry = kp.find_entries(
        title=cfg.client_profile_entry_title,
        group=kp.root_group,
        recursive=False,
        first=True,
    )
    link = (entry.url or "").strip() if entry is not None else ""
    if not link:
        return None, (
            f"no vless link in {cfg.client_profile_entry_title} of {path.name}: "
            "the local proxy is not configured"
        )
    profile = routing_policy.parse_vless_link(
        link,
        cfg.remote_link_default_port,
        cfg.xray_values["vless"],
        cfg.vless_link_query_keys,
        cfg.xray_values,
    )
    if profile is None:
        return None, (
            f"the url of {cfg.client_profile_entry_title} is not a usable "
            "vless link: the local proxy is not configured"
        )
    return profile, ""


def _is_remote_server(profile: routing_policy.VlessProfile, facts: _RunFacts) -> bool:
    """True when the profile points at this machine itself.

    A machine that runs the remote server must not connect to itself, so
    the address of the link is compared with the addresses of this
    machine's interfaces and with the addresses the echo services
    reported.
    """

    if profile.address in facts.local_addresses:
        return True
    return profile.address in (
        facts.public_addresses.ipv4 + facts.public_addresses.ipv6
    )


def _stage_local_proxy(
    cfg: ThreeXuiXraySetupConfig,
    timeout: float,
) -> TaskResult | None:
    """Stage 6: serve a local proxy for this machine through the panel.

    The panel already runs Xray on this machine, so the client of the
    remote server is an inbound of that panel: one listener that serves
    SOCKS5 and HTTP on the configured local address, without a password,
    without a traffic limit and without an expiry date. The inbound is
    created once and replaced when its definition differs, so a rerun with
    another port or another sniffing set converges. It is created on every
    machine, the remote server itself included: only the connection to
    that server is left out there, and the local proxy of such a machine
    is used by the pool the subscription fills. Returns None when the
    inbound already matches, and a TaskResult carrying the change or the
    reason it could not be made.
    """

    try:
        env = xui_client.panel_environment(cfg, timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        return TaskResult(
            success=True,
            warnings=(f"local proxy not configured: {exc}",),
        )
    try:
        changed, message = xray_client.ensure_local_proxy_inbound(
            cfg, env, timeout
        )
    except RuntimeError as exc:
        return TaskResult(
            success=True,
            warnings=(f"local proxy not configured: {exc}",),
        )
    if not changed:
        _log(f"local proxy {cfg.local_proxy_tag} is already configured")
        return None
    _log(
        f"local proxy {cfg.local_proxy_tag} on "
        f"{cfg.local_proxy_listen_address}:{cfg.local_proxy_port}: {message}"
    )
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"local proxy on "
            f"{cfg.local_proxy_listen_address}:{cfg.local_proxy_port} configured"
        ),
    )


def _proxy_request(
    cfg: ThreeXuiXraySetupConfig, proxy: str, url: str
) -> tuple[str, str, int]:
    """One request through the local proxy: body, HTTP code, curl exit code.

    The HTTP code comes from a write-out marker, so a refused or timed out
    transfer is reported as the configured no-answer code of the tool
    instead of being mistaken for a body. The
    request is not retried here: the caller decides how many attempts a
    check is worth, so a stalling tunnel is reported with the number of
    attempts it got instead of being hidden by a retry loop.
    """

    no_answer = cfg.tunnel_probe_no_answer_code
    try:
        result = run_command(
            substituted_command(
                cfg.tunnel_probe_command,
                {
                    "proxy_address": proxy,
                    "timeout_seconds": str(cfg.proxy_check_timeout_seconds),
                    "write_out": cfg.tunnel_probe_write_out,
                },
            )
            + [url],
            check=False,
            capture=True,
            timeout=cfg.proxy_check_command_timeout_seconds,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"the request could not be run: {exc}", no_answer, 1
    output = result.stdout
    body, _, code = output.rpartition("\n")
    return (
        trim_whitespace(body),
        trim_whitespace(code) or no_answer,
        result.returncode,
    )


def _own_addresses(facts: _RunFacts) -> tuple[str, ...]:
    """Every address this machine answers to, as the run facts know them."""

    return facts.local_addresses + facts.public_addresses.ipv4 + facts.public_addresses.ipv6


def _check_egress_address(
    cfg: ThreeXuiXraySetupConfig,
    policy: routing_policy.LocalProxyPolicy,
    profile: routing_policy.VlessProfile,
    facts: _RunFacts,
    proxy: str,
) -> tuple[str, ...]:
    """Check where a request through the local proxy leaves, per country.

    Outside Russia the policy sends an ordinary foreign name through the
    pool, whose members are the remote server and the nodes of the
    subscriptions, so any answer that is not an address of this machine is
    a working remote path and is logged with the exit it used. In Russia
    the same name is sent directly on purpose, so the answer must be an
    address of this machine: expecting the remote server there is what
    made an earlier version of this check report a working proxy as broken.
    An answer that is an address of this machine outside Russia stays the
    failure the check exists to catch, because it is the silent direct
    path. The request is attempted the configured number of times, because
    the path to a remote member can stall once and answer on the next
    attempt, which is a fact of the network rather than a reason to raise
    an alarm. It is called only for a machine that has a remote server of
    its own, so a missing remote path is never read as a defect here.
    """

    _log(f"checking where a request through {proxy} leaves")
    answer = ""
    code = 0
    http_code = cfg.tunnel_probe_no_answer_code
    attempts = cfg.proxy_check_attempts
    for attempt in range(1, attempts + 1):
        answer, http_code, code = _proxy_request(cfg, proxy, cfg.proxy_check_url)
        if code == 0 and answer:
            break
        if attempt < attempts:
            _log(
                f"attempt {attempt} of {attempts} answered nothing "
                f"(curl exit {code}), trying again"
            )
    if code != 0 or not answer:
        return (
            (
                f"the local proxy answered nothing for {cfg.proxy_check_url} "
                f"in {attempts} attempts (curl exit {code}, HTTP {http_code})"
            ),
        )
    if policy.in_russia:
        expected = (
            "an address of this machine "
            f"({', '.join(_own_addresses(facts)) or 'none known'})"
        )
        if answer in _own_addresses(facts):
            _log(
                f"the local proxy works: the request left by {answer}, "
                "directly as intended"
            )
            return ()
        if answer == profile.address:
            return (
                (
                    f"the local proxy answered {answer}, which is the remote "
                    f"server, while the policy routes {cfg.proxy_check_url} "
                    "directly: the policy may not be in place"
                ),
            )
        return (
            (
                f"the local proxy answered {answer}, while {expected} was "
                "expected: the request left somewhere unexpected"
            ),
        )
    if answer in _own_addresses(facts):
        return (
            (
                f"the local proxy answered {answer}, which is this machine: "
                "the connection did not leave by the remote path"
            ),
        )
    if answer == profile.address:
        _log(f"the local proxy works: the request left by the remote server {answer}")
    else:
        _log(
            f"the local proxy works: the request left by {answer} through "
            f"the pool {cfg.pool_balancer_tag}"
        )
    return ()


def _check_remote_path(
    cfg: ThreeXuiXraySetupConfig, proxy: str
) -> tuple[str, ...]:
    """Check that a destination the policy proxies really answers, in Russia.

    On a machine in Russia the plain check URL is routed directly, so it
    says nothing about the remote server; the check that does is a URL of a
    class the policy sends through the server, a resource blocked in Russia
    or a service that refuses to serve it. Such a service does not report
    the address of its client, so an HTTP answer is the evidence: no answer
    means the tunnel does not carry that class, which is exactly what the
    machine needs to hear. The request is attempted the configured number
    of times for the same reason as the egress check.
    """

    url = cfg.proxy_check_blocked_url
    _log(f"checking the remote path through {proxy} with {url}")
    http_code = cfg.tunnel_probe_no_answer_code
    code = 0
    attempts = cfg.proxy_check_attempts
    for attempt in range(1, attempts + 1):
        _, http_code, code = _proxy_request(cfg, proxy, url)
        if code == 0 and http_code not in (cfg.tunnel_probe_no_answer_code, ""):
            _log(f"the remote path works: {url} answered HTTP {http_code}")
            return ()
        if attempt < attempts:
            _log(
                f"attempt {attempt} of {attempts} answered nothing "
                f"(curl exit {code}), trying again"
            )
    reason = (
        f"curl exit {code}, HTTP {http_code}"
        if code != 0
        else f"HTTP {http_code}"
    )
    return (
        (
            f"the local proxy answered nothing for {url} in {attempts} "
            f"attempts ({reason}): "
            "a destination that must use the remote server does not reach it"
        ),
    )


def _check_proxy_path(
    cfg: ThreeXuiXraySetupConfig,
    policy: routing_policy.LocalProxyPolicy,
    profile: routing_policy.VlessProfile,
    facts: _RunFacts,
) -> tuple[str, ...]:
    """Prove the path of the local proxy: where it leaves and what it carries.

    The routing checks prove the decision of the core; these two prove the
    path. The first request asks an address-reporting service where the
    traffic left, and its expected answer follows from the policy: a remote
    exit outside Russia, this machine in Russia, because the policy sends
    an ordinary foreign name directly there. The second request, on a
    machine in Russia only, asks a destination that the policy sends
    through the pool whether it answers at all, so a remote path that
    carries nothing is visible instead of trusted. Both are called only
    where this machine has a remote server of its own. Every disagreement
    is returned as a warning naming what was asked, what was expected and
    what came back.
    """

    proxy = f"socks5h://{cfg.local_proxy_listen_address}:{cfg.local_proxy_port}"
    warnings = list(_check_egress_address(cfg, policy, profile, facts, proxy))
    if policy.in_russia:
        warnings.extend(_check_remote_path(cfg, proxy))
    return tuple(warnings)


def _stage_routing_policy(
    cfg: ThreeXuiXraySetupConfig,
    ctx: Context,
    timeout: float,
    facts: _RunFacts,
) -> TaskResult | None:
    """Stage 7: route the traffic of the local proxy.

    The policy decides, for every connection that enters the local proxy,
    which outbound takes it: advertising is dropped, the hidden services
    of tor and i2p go to the local proxies of those networks, the machine's
    own names and networks go directly, and the rest depends on the country
    of the machine (a machine outside Russia sends it to the remote
    server, a machine in Russia sends only what is blocked there and the
    services that refuse to serve Russia through the remote server).
    Every remote class leaves through the pool named by pool_balancer_tag:
    the pool covers the outbounds whose tags begin with pool_member_prefix,
    which are the nodes of the subscriptions the panel was given, and the
    remote outbound when this machine has one. The pool therefore exists on
    every machine and waits to be filled; on the machine that is the remote
    server itself the remote outbound is never created, so that machine
    never connects to itself, and the fallback of its pool is the direct
    outbound. The template is read, rewritten and written back only when
    something really differs, the categories are checked against the
    installed geodata first, and every class of destination is then
    verified against the running core, because the core can keep a rule set
    the stored template no longer matches. Writing the template reconciles
    the core, and the panel may do that by restarting it, so the
    verification waits for the core to answer before it asks, and a core
    that never answers is reported as such instead of as a wrong policy
    (see route_test_failures). The path through the local proxy is proven
    only where a remote path exists; a machine without one is told so in
    the log instead of being checked against a path it does not have.
    Returns None when everything is already in place, and a TaskResult
    with the change and the warnings otherwise.
    """

    profile, reason = _remote_profile(cfg, ctx)
    on_the_remote_server = profile is not None and _is_remote_server(profile, facts)
    remote_outbound: dict[str, object] | None = None
    if profile is None:
        _log(reason)
    elif on_the_remote_server:
        _log(
            "the profile points at this machine: the client half is configured "
            "without a connection to itself, and the remote classes wait for "
            "the pool to be filled"
        )
    else:
        remote_outbound = routing_policy.build_remote_outbound(
            cfg.remote_outbound_tag,
            profile,
            cfg.xray_field_keys,
            cfg.xray_values,
        )
    try:
        env = xui_client.panel_environment(cfg, timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        return TaskResult(
            success=True,
            warnings=(f"routing policy not applied: {exc}",),
        )
    template = xui_client.read_xray_template(cfg, env, timeout)
    if template is None:
        return TaskResult(
            success=True,
            warnings=(
                (
                    "routing policy not applied: the panel did not return its "
                    "Xray configuration"
                ),
            ),
        )

    selector: tuple[str, ...] = (cfg.pool_member_prefix,)
    pool_fallback_tag = cfg.direct_outbound_tag
    if remote_outbound is not None:
        selector = (cfg.pool_member_prefix, cfg.remote_outbound_tag)
        pool_fallback_tag = cfg.remote_outbound_tag
    warnings: list[str] = []
    policy = xray_client.machine_policy(cfg, ctx, env, timeout, warnings)
    wanted, differs = xray_client.apply_policy_to_template(
        policy,
        template,
        remote_outbound=remote_outbound,
        remote_balancer_tag=cfg.pool_balancer_tag,
    )
    settings, pool_differs = routing_policy.apply_fastest_pool(
        wanted.settings,
        cfg.xray_field_keys,
        balancer=routing_policy.build_balancer(
            cfg.xray_field_keys,
            tag=cfg.pool_balancer_tag,
            selector=selector,
            strategy=cfg.xray_values["least_ping"],
            fallback_tag=pool_fallback_tag,
        ),
        observatory=routing_policy.build_observatory(
            cfg.xray_field_keys,
            subject_selector=selector,
            probe_url=cfg.pool_probe_url,
            probe_interval=cfg.pool_probe_interval,
            enable_concurrency=cfg.pool_enable_concurrency,
        ),
    )
    if pool_differs:
        wanted = replace(wanted, settings=settings)
        differs = True
    applied = False
    if differs:
        ok, message = xui_client.write_xray_template(cfg, env, wanted, timeout)
        if not ok:
            return TaskResult(
                success=True,
                warnings=tuple(warnings)
                + (f"routing policy not applied: {message}",),
            )
        applied = True
        _log(f"routing policy applied: {message}")

    failures, decided = xray_client.route_test_failures(
        cfg,
        env,
        timeout,
        policy,
        remote_balancer_tag=cfg.pool_balancer_tag,
        balancer_selector=selector,
        pool_fallback_tag=pool_fallback_tag,
    )
    if failures and decided:
        # The running core can hold a rule set the stored template no longer
        # matches, a state the panel reaches on its own after an inbound is
        # renamed. Writing the same template again is what brings the core
        # back, so it is tried once before the disagreement is reported.
        ok, message = xui_client.write_xray_template(cfg, env, wanted, timeout)
        if ok:
            applied = True
            _log(f"routing policy written again: {message}")
            failures, decided = xray_client.route_test_failures(
                cfg,
                env,
                timeout,
                policy,
                remote_balancer_tag=cfg.pool_balancer_tag,
                balancer_selector=selector,
                pool_fallback_tag=pool_fallback_tag,
            )
    warnings.extend(failures)

    # The path through the local proxy is proven only when the core decided
    # every class and this machine has a remote path at all: a core that is
    # not answering carries no traffic, and that silence is already reported
    # above. The machine that is the remote server has no remote path of its
    # own, so nothing is checked there.
    if decided and remote_outbound is not None and profile is not None:
        warnings.extend(_check_proxy_path(cfg, policy, profile, facts))
    elif decided:
        _log(
            "the path through the local proxy is not checked: this machine "
            "has no remote path of its own"
        )

    if applied:
        return TaskResult(
            success=True,
            changed=True,
            message="routing policy applied",
            warnings=tuple(warnings),
        )
    if warnings:
        return TaskResult(success=True, warnings=tuple(warnings))
    _log("routing policy is already configured and verified")
    return None
