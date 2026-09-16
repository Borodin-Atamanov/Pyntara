"""Shared client-side setup of the 3x-ui panel.

The module holds the steps that make a machine a client through its own
3x-ui panel. The three_x_ui_xray_setup task uses them for stages 6 and 7
(the local proxy inbound and its routing policy), and the sotavpn_setup
task uses the same steps for the Sotavpn pool, whose load balancer
carries the remote classes of the same connection. Everything here talks
to the panel through pyntara.xui and builds the documents with the pure
builders of pyntara.routing_policy.

The steps are the environment of a run (pyntara.xui.panel_environment),
the geodata check of the configured category lists, the policy of the
local proxy, the inbound the policy needs, the template apply step and
the routing checks that ask the running core. A step that cannot be made
raises RuntimeError with the reason in plain words, so the calling task
decides between a warning and a retry.
"""

from __future__ import annotations

import time

from pyntara import routing_policy
from pyntara import xui as xui_client
from pyntara.config import ThreeXuiXraySetupConfig
from pyntara.context import Context
from pyntara.location import describe_answers, detect_country
from pyntara.logger import log_progress as _log
from pyntara.public_address import directly_connected_networks


def machine_policy(
    cfg: ThreeXuiXraySetupConfig,
    ctx: Context,
    env: dict[str, str],
    timeout: float,
    warnings: list[str],
) -> routing_policy.LocalProxyPolicy:
    """The routing policy of this machine, built from its own answers.

    The category lists are first checked against the geodata files of the
    panel, the networks of the machine are read from its interfaces and the
    country is detected from the configured services, with the same lines
    in the log every caller wants to see; the policy is then built from
    those answers. Tokens the panel refused are appended to warnings, so
    the calling task reports them with its own result, and the log lines
    name the country answer the decision came from.
    """

    lists, category_warnings = checked_category_lists(cfg, env, timeout)
    warnings.extend(category_warnings)
    own_networks = directly_connected_networks(ctx.config.engine, timeout)
    report = detect_country(
        ctx.config.engine,
        cfg.country_services,
        cfg.country_word,
        cfg.country_query_timeout_seconds,
        cfg.country_command_timeout_seconds,
    )
    for line in describe_answers(report):
        _log(f"country check {line}")
    if report.in_country:
        _log(
            f"country check: an answer named {cfg.country_word}, the machine "
            "is treated as inside it"
        )
    else:
        _log(
            f"country check: no answer named {cfg.country_word}, the machine "
            "is treated as outside it"
        )
    return build_local_proxy_policy(
        cfg,
        lists=lists,
        in_russia=report.in_country,
        own_networks=own_networks,
    )


def checked_category_lists(
    cfg: ThreeXuiXraySetupConfig, env: dict[str, str], timeout: float
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """The configured category lists with only the categories that resolve.

    The routing tokens name categories of the geodata files the panel
    installed, and those files are community maintained: a token the panel
    cannot resolve would make the whole Xray configuration invalid and
    take the panel down with it, so it is dropped from the policy and
    reported. Literal tokens (a domain name, an address range) are
    accepted by the panel as they are, so they always survive. Returns the
    lists by config name and one warning per dropped token.
    """

    domain_lists: dict[str, tuple[str, ...]] = {
        "ad_block_domain_categories": cfg.ad_block_domain_categories,
        "direct_domains": cfg.direct_domains,
        "russia_direct_domain_categories": cfg.russia_direct_domain_categories,
        "russia_blocked_domain_categories": cfg.russia_blocked_domain_categories,
        "geo_restricted_domain_categories": cfg.geo_restricted_domain_categories,
    }
    ip_lists: dict[str, tuple[str, ...]] = {
        "direct_ip_categories": cfg.direct_ip_categories,
        "russia_direct_ip_categories": cfg.russia_direct_ip_categories,
        "russia_blocked_ip_categories": cfg.russia_blocked_ip_categories,
    }
    rejected = {
        **xui_client.validate_geodata_tokens(
            cfg,
            env,
            cfg.panel_geodata_domain_kind,
            [token for tokens in domain_lists.values() for token in tokens],
            timeout,
        ),
        **xui_client.validate_geodata_tokens(
            cfg,
            env,
            cfg.panel_geodata_ip_kind,
            [token for tokens in ip_lists.values() for token in tokens],
            timeout,
        ),
    }
    lists = {
        name: tuple(token for token in tokens if token not in rejected)
        for name, tokens in {**domain_lists, **ip_lists}.items()
    }
    warnings = tuple(
        f"routing category {token} dropped: {reason}"
        for token, reason in sorted(rejected.items())
    )
    return lists, warnings


def build_local_proxy_policy(
    cfg: ThreeXuiXraySetupConfig,
    *,
    lists: dict[str, tuple[str, ...]],
    in_russia: bool,
    own_networks: tuple[str, ...],
) -> routing_policy.LocalProxyPolicy:
    """The policy of the local proxy from the config and the run facts.

    lists are the checked category lists of checked_category_lists,
    in_russia is the country the services reported and own_networks are
    the subnets the kernel reports as directly connected; everything else
    comes from the [three_x_ui_xray_setup] table.
    """

    return routing_policy.LocalProxyPolicy(
        inbound_tag=cfg.local_proxy_tag,
        remote_outbound_tag=cfg.remote_outbound_tag,
        tor_outbound_tag=cfg.tor_outbound_tag,
        i2p_outbound_tag=cfg.i2p_outbound_tag,
        direct_outbound_tag=cfg.direct_outbound_tag,
        blocked_outbound_tag=cfg.blocked_outbound_tag,
        tor_proxy_address=cfg.tor_proxy_address,
        i2p_proxy_address=cfg.i2p_proxy_address,
        ad_block_domain_categories=lists["ad_block_domain_categories"],
        direct_domains=lists["direct_domains"],
        direct_ip_categories=lists["direct_ip_categories"],
        direct_ip_networks=cfg.direct_ip_networks,
        own_networks=own_networks,
        in_russia=in_russia,
        russia_blocked_domain_categories=lists["russia_blocked_domain_categories"],
        russia_blocked_ip_categories=lists["russia_blocked_ip_categories"],
        russia_direct_domain_categories=lists["russia_direct_domain_categories"],
        russia_direct_ip_categories=lists["russia_direct_ip_categories"],
        geo_restricted_domain_categories=lists["geo_restricted_domain_categories"],
        russia_domain_strategy=cfg.russia_domain_strategy,
        outside_russia_domain_strategy=cfg.outside_russia_domain_strategy,
        panel_inbound_protocol=cfg.panel_inbound_protocol,
        panel_blocked_rule_protocols=cfg.panel_blocked_rule_protocols,
        panel_private_block_category=cfg.panel_private_block_category,
        field_keys=cfg.xray_field_keys,
        values=cfg.xray_values,
    )


def _inbound_matches(
    cfg: ThreeXuiXraySetupConfig,
    existing: dict[str, object],
    payload: dict[str, object],
) -> bool:
    """True when the stored inbound already carries the wanted definition.

    Only the keys of the payload are compared, and the traffic counters
    are left out: the panel counts traffic into the two counter fields of
    the configured field map, so comparing them would report a change on
    every run. The nested blocks are compared key by key for the same
    reason, so a panel that adds a value of its own does not make the
    inbound different.
    """

    counters = {
        cfg.xray_field_keys["up"],
        cfg.xray_field_keys["down"],
    }
    for key, wanted in payload.items():
        if key in counters:
            continue
        stored = existing.get(key)
        if isinstance(wanted, dict):
            if not isinstance(stored, dict):
                return False
            for name, value in wanted.items():
                if stored.get(name) != value:
                    return False
            continue
        if stored != wanted:
            return False
    return True


def ensure_local_proxy_inbound(
    cfg: ThreeXuiXraySetupConfig, env: dict[str, str], timeout: float
) -> tuple[bool, str]:
    """Serve the local proxy inbound through the panel; (changed, message).

    The panel already runs Xray on the machine, so the client is an
    inbound of that panel: one listener that serves SOCKS5 and HTTP on
    the configured local address, without a password, without a traffic
    limit and without an expiry date. The inbound is created once and
    replaced when its definition differs, so a rerun with another port or
    another sniffing set converges. changed is False when the stored
    inbound already matches. A write that fails raises RuntimeError with
    the panel message, so the caller reports the step it could not make.
    """

    payload = routing_policy.build_local_proxy_inbound(
        tag=cfg.local_proxy_tag,
        protocol=cfg.panel_inbound_protocol,
        remark=cfg.local_proxy_tag,
        listen_address=cfg.local_proxy_listen_address,
        port=cfg.local_proxy_port,
        udp_enabled=cfg.local_proxy_udp,
        enabled=cfg.local_proxy_enabled,
        traffic_limit_bytes=cfg.local_proxy_traffic_limit_bytes,
        expiry_time=cfg.local_proxy_expiry_time,
        sniffing_enabled=cfg.local_proxy_sniffing_enabled,
        sniffing_metadata_only=cfg.local_proxy_sniffing_metadata_only,
        sniffing_route_only=cfg.local_proxy_sniffing_route_only,
        sniffing_protocols=cfg.local_proxy_sniffing_protocols,
        fields=cfg.xray_field_keys,
        values=cfg.xray_values,
    )
    existing = xui_client.find_inbound_by_tag(
        cfg, env, cfg.local_proxy_tag, timeout
    )
    if existing is not None and _inbound_matches(cfg, existing, payload):
        return False, ""
    ok, message = xui_client.upsert_inbound(cfg, env, payload, timeout)
    if not ok:
        raise RuntimeError(message)
    return True, message


def apply_policy_to_template(
    policy: routing_policy.LocalProxyPolicy,
    template: xui_client.XrayTemplate,
    *,
    remote_outbound: dict[str, object] | None,
    remote_balancer_tag: str = "",
) -> tuple[xui_client.XrayTemplate, bool]:
    """Bring a stored template to the policy state; (wanted, differs).

    The result carries the outbound test URL of the stored template, so a
    write never loses a setting of the panel. differs says whether the
    document really changed; the caller writes only then.
    """

    updated, differs = routing_policy.apply_routing_policy(
        template.settings,
        policy,
        remote_outbound=remote_outbound,
        remove_panel_restrictions=True,
        remote_balancer_tag=remote_balancer_tag,
    )
    wanted = xui_client.XrayTemplate(
        settings=updated,
        outbound_test_url=template.outbound_test_url,
    )
    return wanted, differs


def _own_network_address(own_networks: tuple[str, ...]) -> str | None:
    """The network address of the first IPv4 subnet of the machine, or None.

    The routing check asks the core about an address of the machine's own
    networks; an IPv4 subnet carries no zone and no ambiguity, so it is
    the simpler of the two families to ask about.
    """

    for network in own_networks:
        address = network.split("/", 1)[0]
        if address and ":" not in address:
            return address
    return None


def _route_expectations(
    cfg: ThreeXuiXraySetupConfig,
    policy: routing_policy.LocalProxyPolicy,
    *,
    remote_balancer_tag: str = "",
) -> tuple[tuple[str, str, str], ...]:
    """The destinations to ask the core about and the outbound each must take.

    Each entry is a destination, its kind ("domain" or "address") and the
    tag of the outbound the policy sends it to. The expectations follow
    from the policy itself, so a disagreement means the running core did
    not take the policy: the two hidden services are local, the direct
    domain and the machine's own subnet go directly, the advertising
    domain is dropped, and the country decides the rest. When
    remote_balancer_tag is set, the remote classes leave through that
    balancer and the expected answer is the member the balancer picked (or
    the balancer tag itself), which the caller accepts through the
    balancer selector.
    """

    remote_target = remote_balancer_tag or policy.remote_outbound_tag
    checks: list[tuple[str, str, str]] = [
        (cfg.route_check_onion_domain, "domain", policy.tor_outbound_tag),
        (cfg.route_check_i2p_domain, "domain", policy.i2p_outbound_tag),
        (cfg.route_check_ad_domain, "domain", policy.blocked_outbound_tag),
        (cfg.route_check_direct_domain, "domain", policy.direct_outbound_tag),
    ]
    own_address = _own_network_address(policy.own_networks)
    if own_address is not None:
        checks.append((own_address, "address", policy.direct_outbound_tag))
    if policy.in_russia:
        checks.append(
            (cfg.route_check_foreign_domain, "domain", policy.direct_outbound_tag)
        )
        checks.append(
            (
                cfg.route_check_russia_blocked_domain,
                "domain",
                remote_target,
            )
        )
    else:
        checks.append(
            (cfg.route_check_foreign_domain, "domain", remote_target)
        )
    return tuple(checks)


def _ask_core(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
    *,
    inbound_tag: str,
    kind: str,
    destination: str,
) -> tuple[bool | None, str]:
    """One routing question to the running core: (decision, answer).

    kind selects the field the panel accepts for the destination, a
    domain or an address, so one call shape serves every destination
    class of the policy and the readiness wait. The decision is True with
    the chosen outbound tag, False when the core answered without a
    matching rule, and None when no decision was obtained, because the
    panel or the core did not answer.
    """

    question = (
        {"domain": destination} if kind == "domain" else {"address": destination}
    )
    return xui_client.route_test(
        cfg,
        env,
        inbound_tag=inbound_tag,
        network=cfg.route_test_network,
        protocol=cfg.route_test_protocol,
        port=cfg.route_test_port,
        timeout=timeout,
        **question,
    )


def _answer_matches(
    expected: str,
    answer: str,
    remote_balancer_tag: str,
    balancer_selector: tuple[str, ...],
    pool_fallback_tag: str,
) -> bool:
    """Whether one core answer satisfies an expectation.

    A remote class whose traffic leaves through the pool is answered with
    the member the balancer picked, so the balancer tag itself, any tag its
    selector covers and the fallback of the pool satisfy the expectation:
    the core answers the fallback when no member is available, which is the
    normal state of a pool whose subscription has not arrived yet. Every
    other class is answered with the outbound the policy names for it.
    """

    if answer == expected:
        return True
    if remote_balancer_tag and expected == remote_balancer_tag:
        if routing_policy.tag_matches_selector(answer, balancer_selector):
            return True
        return bool(pool_fallback_tag) and answer == pool_fallback_tag
    return False


def _verify_routes(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
    policy: routing_policy.LocalProxyPolicy,
    *,
    remote_balancer_tag: str = "",
    balancer_selector: tuple[str, ...] = (),
    pool_fallback_tag: str = "",
) -> tuple[tuple[str, ...], str | None]:
    """Ask the running core about every destination class, and report.

    The routing engine of the running core answers, so this is the only
    honest check that the policy reached the traffic: the stored template
    has already been seen to disagree with the core. A check that matches
    is logged with the outbound it took; every disagreement is returned as
    a warning naming the destination, the expected outbound and the answer.
    A remote class that leaves through a pool accepts every member the
    balancer selector covers and the fallback of the pool. Returns
    (failures, undecided): undecided is the reason the panel gave when a
    question got no decision at all, and the failures of that round are
    dropped, because a round that answered nothing proves nothing.
    """

    failures: list[str] = []
    for destination, kind, expected in _route_expectations(
        cfg, policy, remote_balancer_tag=remote_balancer_tag
    ):
        matched, answer = _ask_core(
            cfg,
            env,
            timeout,
            inbound_tag=policy.inbound_tag,
            kind=kind,
            destination=destination,
        )
        if matched is None:
            return (), answer
        if matched and _answer_matches(
            expected,
            answer,
            remote_balancer_tag,
            balancer_selector,
            pool_fallback_tag,
        ):
            _log(f"routing check {destination}: {answer}")
            continue
        observed = answer if matched else f"no decision ({answer})"
        alternatives = []
        if remote_balancer_tag and expected == remote_balancer_tag:
            alternatives.append("a member of its pool")
            if pool_fallback_tag:
                alternatives.append(f"its fallback {pool_fallback_tag}")
        expected_text = (
            f"{expected} or " + " or ".join(alternatives)
            if alternatives
            else expected
        )
        failures.append(
            f"routing check {destination}: expected {expected_text}, got "
            f"{observed}"
        )
    return tuple(failures), None


def route_test_failures(
    cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
    policy: routing_policy.LocalProxyPolicy,
    *,
    remote_balancer_tag: str = "",
    balancer_selector: tuple[str, ...] = (),
    pool_fallback_tag: str = "",
) -> tuple[tuple[str, ...], bool]:
    """Ask the core about every class, waiting for its first answer.

    The round is its own readiness probe: a written template reconciles the
    running core, and the panel answers the write before a restart it
    triggered has finished, so the first questions can land while the core
    is still loading its geodata files and binding its listeners (nine
    seconds on an eight-core machine). A question that gets no decision
    therefore ends the round and starts it again after a pause, until the
    core answers or the budget runs out; the budget and the pause are
    config values, because a slow machine needs a longer budget and not a
    false warning. When the budget runs out, the warning carries the state
    the panel reports about its core and the last line the core printed,
    so an operator reads the reason instead of a number. Returns
    (warnings, decided): decided is False when the core never answered, and
    the warning then names that instead of a disagreement, because a core
    that is still starting or has died is not a policy the core refused.
    """

    budget = cfg.core_ready_wait_seconds
    delay = cfg.readiness_check_delay_seconds
    started = time.monotonic()
    failures, undecided = _verify_routes(
        cfg,
        env,
        timeout,
        policy,
        remote_balancer_tag=remote_balancer_tag,
        balancer_selector=balancer_selector,
        pool_fallback_tag=pool_fallback_tag,
    )
    if undecided is not None:
        _log(
            f"the panel core did not answer yet ({undecided}), waiting up to "
            f"{budget} s"
        )
    while undecided is not None:
        if time.monotonic() - started >= budget:
            diagnostics = xui_client.core_diagnostics(cfg, env, timeout)
            return (
                (
                    f"the panel core did not answer within {budget} s "
                    f"({undecided}; {diagnostics}), so the routing policy and "
                    "the proxy path were not verified"
                ),
            ), False
        time.sleep(delay)
        failures, undecided = _verify_routes(
            cfg,
            env,
            timeout,
            policy,
            remote_balancer_tag=remote_balancer_tag,
            balancer_selector=balancer_selector,
            pool_fallback_tag=pool_fallback_tag,
        )
        if undecided is None:
            _log(
                f"the panel core answered after "
                f"{time.monotonic() - started:.1f}s"
            )
    return failures, True
