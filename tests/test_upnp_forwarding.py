"""Unit tests for the router port forwarding service.

The UPnP client is faked by a small router table that answers the same
calls the real client makes, including the silent replacement of a rule, so
the decisions of the service are asserted against the behaviour of the
router instead of against an assumption. The system queries are mocked; the
tests only touch temporary fixtures (docs/guides/developer-guide.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from support import FakeProc, make_config

import pyntara.upnp_forwarding as forwarding
from pyntara.config import Config
from pyntara.port_forwarding import desired_port

CONFIG_PATH = "/etc/pyntara/config.toml"
ROUTER_ADDRESS = "191.83.167.128"
INTERNAL_ADDRESS = "192.168.1.52"


class _FakeRouter:
    """The mapping table of a router, with the calls of the upnpc client.

    The table answers the status call, the list call and the add call. An
    add for an external port that is already taken replaces the rule, which
    is what the real router does silently; that behaviour is the reason the
    service never adds a rule at a port it does not own.
    """

    def __init__(
        self,
        rules: tuple[tuple[int, str, int, str], ...] = (),
        address: str | None = ROUTER_ADDRESS,
    ) -> None:
        # (external port, internal address, internal port, description)
        self.rules = list(rules)
        self.address = address
        self.calls: list[list[str]] = []

    def run(self, command: list[str], **kwargs: Any) -> FakeProc:
        self.calls.append(list(command))
        if command[1] == "-s":
            if self.address is None:
                return FakeProc(0, "No IGD UPnP Device found on the network\n")
            return FakeProc(0, f"ExternalIPAddress = {self.address}\n")
        if command[1] == "-l":
            return FakeProc(
                0,
                "".join(
                    f" {index} TCP   {port}->{address}:{internal_port}"
                    f"  '{description}'  ''\n"
                    for index, (
                        port,
                        address,
                        internal_port,
                        description,
                    ) in enumerate(self.rules)
                ),
            )
        if command[1] == "-e":
            target = command.index("-a")
            description = command[command.index("-e") + 1]
            port = int(command[target + 3])
            self.rules = [rule for rule in self.rules if rule[0] != port]
            self.rules.append(
                (
                    port,
                    command[target + 1],
                    int(command[target + 2]),
                    description,
                )
            )
            return FakeProc(0, "ok\n")
        return FakeProc(0, "")

    @property
    def added(self) -> list[list[str]]:
        """The add calls the router received."""

        return [call for call in self.calls if "-e" in call]


def _service(
    monkeypatch: pytest.MonkeyPatch,
    router: _FakeRouter,
    config: Config,
    triggers: list[Config],
) -> None:
    """Wire the service to a faked router, machine and collector."""

    monkeypatch.setattr(forwarding.upnp, "run_command", router.run)
    monkeypatch.setattr(
        forwarding, "default_route_address", lambda _e, _t: INTERNAL_ADDRESS
    )
    monkeypatch.setattr(forwarding.socket, "gethostname", lambda: "testhost")
    monkeypatch.setattr(
        forwarding, "package_is_installed", lambda *_a, **_k: True
    )
    monkeypatch.setattr(forwarding, "load_config", lambda _path: config)
    monkeypatch.setattr(forwarding, "trigger_collection", triggers.append)


def _run_main() -> int:
    """Run the service the way the deployed unit runs it."""

    return forwarding.main(["upnp_forwarding", CONFIG_PATH])


class TestCandidatePorts:
    """Tests for the external ports the service tries, in order."""

    def test_the_first_candidate_is_the_port_of_the_tunnel_task(self) -> None:
        # One machine carries one predictable number: the router publishes
        # the same port the reverse tunnel asks a server for, so both
        # schemes name the machine with the same value.
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        assert ports[0] == desired_port(config, "testhost")

    def test_every_further_candidate_carries_the_attempt_number(self) -> None:
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        assert ports[1] == desired_port(config, "testhost2")
        assert ports[2] == desired_port(config, "testhost3")

    def test_no_candidate_is_tried_twice(self) -> None:
        # The number of candidates is a config value; a hash that repeats an
        # earlier port is dropped instead of wasting an attempt on it.
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        assert 0 < len(ports) <= config.upnp_forwarding_setup.mapping_attempts
        assert len(set(ports)) == len(ports)


class TestMain:
    """Tests for the whole run of the deployed service."""

    def test_a_free_port_is_forwarded_and_wakes_the_collector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        router = _FakeRouter()
        triggers: list[Config] = []
        _service(monkeypatch, router, config, triggers)

        assert _run_main() == 0
        assert router.rules == [
            (ports[0], INTERNAL_ADDRESS, 30222, "pyntara ssh")
        ]
        assert triggers == [config]

    def test_the_rule_of_another_program_moves_to_the_next_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This router replaces a rule silently, so the port another program
        # holds is left alone and the next candidate is tried; the rule of
        # the other program is still there afterwards.
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        foreign = (ports[0], "192.168.1.48", 443, "pyntara xray")
        router = _FakeRouter(rules=(foreign,))
        triggers: list[Config] = []
        _service(monkeypatch, router, config, triggers)

        assert _run_main() == 0
        assert foreign in router.rules
        assert (ports[1], INTERNAL_ADDRESS, 30222, "pyntara ssh") in router.rules
        assert triggers == [config]

    def test_a_rule_that_is_already_right_wakes_nobody(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The network did not change, so the report carries the same facts
        # as the last one and there is nothing worth sending.
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        router = _FakeRouter(
            rules=((ports[0], INTERNAL_ADDRESS, 30222, "pyntara ssh"),)
        )
        triggers: list[Config] = []
        _service(monkeypatch, router, config, triggers)

        assert _run_main() == 0
        assert router.added == []
        assert triggers == []

    def test_a_rule_that_moved_is_refreshed_and_wakes_the_collector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The machine took another address, so its own rule points at the
        # old one: the router takes the same rule again with the new target.
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        stale = (ports[0], "192.168.1.9", 30222, "pyntara ssh")
        router = _FakeRouter(rules=(stale,))
        triggers: list[Config] = []
        _service(monkeypatch, router, config, triggers)

        assert _run_main() == 0
        assert router.rules == [
            (ports[0], INTERNAL_ADDRESS, 30222, "pyntara ssh")
        ]
        assert triggers == [config]

    def test_no_router_is_a_normal_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A network without UPnP, or a router with UPnP switched off, is a
        # normal state: nothing is attempted and the service reports success.
        config = make_config()
        router = _FakeRouter(address=None)
        triggers: list[Config] = []
        _service(monkeypatch, router, config, triggers)

        assert _run_main() == 0
        assert router.added == []
        assert triggers == []

    def test_every_candidate_taken_is_reported_and_not_forced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = make_config()
        ports = forwarding.candidate_ports(config, "testhost")
        rules = tuple(
            (port, "192.168.1.48", 443, "pyntara xray") for port in ports
        )
        router = _FakeRouter(rules=rules)
        triggers: list[Config] = []
        _service(monkeypatch, router, config, triggers)

        assert _run_main() == 0
        assert router.added == []
        assert triggers == []
        assert router.rules == list(rules)

    def test_a_missing_command_argument_is_refused(self) -> None:
        assert forwarding.main(["upnp_forwarding"]) == 2
        assert forwarding.main(["upnp_forwarding", CONFIG_PATH, "extra"]) == 2

    def test_the_config_path_is_the_only_argument(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The deployed unit passes the single system config path, so the
        # service reads the same document the task deployed.
        seen: list[Path] = []
        config = make_config()

        def fake_load(path: Path) -> Config:
            seen.append(path)
            return config

        monkeypatch.setattr(forwarding, "load_config", fake_load)
        monkeypatch.setattr(
            forwarding, "package_is_installed", lambda *_a, **_k: True
        )
        monkeypatch.setattr(forwarding, "ensure_forwarding", lambda *_a: None)
        path = tmp_path / "config.toml"
        assert forwarding.main(["upnp_forwarding", str(path)]) == 0
        assert seen == [path]
