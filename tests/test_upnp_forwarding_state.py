"""Unit tests for the address command of the forwarded router port.

The UPnP client is mocked via monkeypatch; the tests only touch temporary
fixtures (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from support import FakeProc, make_config

from pyntara import upnp_forwarding_state
from pyntara.config import Config
from pyntara.ssh_access import ssh_command

ROUTER_ADDRESS = "191.83.167.128"
CONFIG_PATH = "/etc/pyntara/config.toml"
THIS_MACHINE = "testhost"

# The table of the router this machine shares with a neighbour of this
# project: only the first rule carries the mark of this machine, and the
# mark names the machine, so the neighbour's rule is never read as ours.
LISTING = (
    " 0 TCP   39222->192.168.1.52:30222  'pyntara ssh testhost'  ''\n"
    " 1 TCP    443->192.168.1.48:443    'pyntara xray otherhost'  ''\n"
)


@pytest.fixture(autouse=True)
def _this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every case as the machine the router table names."""

    monkeypatch.setattr(
        upnp_forwarding_state.socket, "gethostname", lambda: THIS_MACHINE
    )


def _router(
    monkeypatch: pytest.MonkeyPatch,
    listing: str,
    address: str | None = ROUTER_ADDRESS,
) -> None:
    """Fake the two client calls the command makes."""

    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        if command[1] == "-s":
            if address is None:
                return FakeProc(0, "No IGD UPnP Device found on the network\n")
            return FakeProc(0, f"ExternalIPAddress = {address}\n")
        return FakeProc(0, listing)

    monkeypatch.setattr(upnp_forwarding_state.upnp, "run_command", fake_run)


def _config(monkeypatch: pytest.MonkeyPatch) -> Config:
    config = make_config()
    monkeypatch.setattr(
        upnp_forwarding_state, "load_config", lambda _path: config
    )
    return config


class TestAddressScope:
    """Tests for the scope a router address carries."""

    def test_a_global_address_reaches_the_internet(self) -> None:
        assert (
            upnp_forwarding_state.address_scope(
                ROUTER_ADDRESS,
                "global",
                "nat",
            )
            == "global"
        )

    def test_a_provider_or_private_address_takes_the_narrow_scope(self) -> None:
        # The shared address space of a provider NAT and the private address
        # of another router both stop well before the internet.
        for address in ("100.64.0.7", "192.168.1.1", "10.0.0.1"):
            assert (
                upnp_forwarding_state.address_scope(address, "global", "nat")
                == "nat"
            )

    def test_a_value_that_is_not_an_address_takes_the_narrow_scope(self) -> None:
        assert (
            upnp_forwarding_state.address_scope("not-an-address", "global", "nat")
            == "nat"
        )


class TestMappingRecords:
    """Tests for the records one router table produces."""

    def test_reports_the_rule_of_this_project_with_its_command(self) -> None:
        config = make_config()
        keys = config.engine.report_record_keys
        assert upnp_forwarding_state.mapping_records(
            config, ROUTER_ADDRESS, LISTING
        ) == [
            {
                keys["channel"]: "upnp",
                keys["address"]: ROUTER_ADDRESS,
                keys["port"]: 39222,
                keys["local_port"]: 30222,
                keys["scope"]: "global",
                keys["ssh"]: ssh_command(config.engine, ROUTER_ADDRESS, 39222),
            }
        ]

    def test_a_rule_of_another_machine_stays_out_of_the_report(self) -> None:
        config = make_config()
        assert upnp_forwarding_state.mapping_records(
            config,
            ROUTER_ADDRESS,
            " 1 TCP    443->192.168.1.48:443"
            "    'pyntara xray otherhost'  ''\n",
        ) == []

    def test_a_rule_that_lost_the_machine_name_is_not_ours(self) -> None:
        # A rule written before the mark carried the machine name belongs to
        # no machine in particular: the report leaves it out, and the
        # forwarding service writes its own rule again under the new mark.
        config = make_config()
        assert upnp_forwarding_state.mapping_records(
            config,
            ROUTER_ADDRESS,
            " 0 TCP   39222->192.168.1.52:30222  'pyntara ssh'  ''\n",
        ) == []


class TestMain:
    """Tests for the command the collector runs."""

    def test_prints_the_records_of_the_router(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = _config(monkeypatch)
        _router(monkeypatch, LISTING)
        assert (
            upnp_forwarding_state.main(["upnp_forwarding_state", CONFIG_PATH])
            == 0
        )
        keys = config.engine.report_record_keys
        printed = capsys.readouterr().out
        assert f'"{keys["port"]}": 39222' in printed
        assert f'"{keys["channel"]}": "upnp"' in printed
        # The collector keeps a document printed on stdout as records, so
        # nothing but the document may travel there.
        document = json.loads(printed)
        assert document[0][keys["scope"]] == "global"

    def test_prints_nothing_without_a_router(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _config(monkeypatch)
        _router(monkeypatch, LISTING, address=None)
        assert (
            upnp_forwarding_state.main(["upnp_forwarding_state", CONFIG_PATH])
            == 0
        )
        assert capsys.readouterr().out == ""

    def test_prints_nothing_when_the_router_carries_no_rule_of_this_project(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _config(monkeypatch)
        _router(monkeypatch, "")
        assert (
            upnp_forwarding_state.main(["upnp_forwarding_state", CONFIG_PATH])
            == 0
        )
        assert capsys.readouterr().out == ""

    def test_a_wrong_argument_count_is_refused(self) -> None:
        assert upnp_forwarding_state.main(["upnp_forwarding_state"]) == 2
