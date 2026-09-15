"""Unit tests for the shared deterministic port chain.

The chain names a machine in both forwarding schemes, so its properties
are tested here once: the port is a stable function of the name, the
candidates walk the configured range in a fixed order, no candidate is
offered twice, and the walk ends when the range is exhausted, because the
next candidate could only repeat a port already offered. The services
that consume the chain are tested in their own files
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

from dataclasses import replace
from itertools import islice

from support import make_config

from pyntara.config import Config
from pyntara.forwarding_ports import candidate_ports, desired_port


def _tiny_range_config(low: int = 1000, high: int = 1002) -> Config:
    """A config whose port range holds exactly three ports."""

    config = make_config()
    return replace(
        config,
        port_forwarding_setup=replace(
            config.port_forwarding_setup,
            desired_port_min=low,
            desired_port_max=high,
        ),
    )


class TestDesiredPort:
    def test_deterministic_and_in_range(self) -> None:
        config = make_config()
        first = desired_port(config, "dozor-gunid")
        second = desired_port(config, "dozor-gunid")
        assert first == second
        assert config.port_forwarding_setup.desired_port_min <= first
        assert first <= config.port_forwarding_setup.desired_port_max

    def test_differs_across_hostnames(self) -> None:
        config = make_config()
        ports = {
            desired_port(config, hostname) for hostname in ("aaa-babab", "bbb-babab")
        }
        assert len(ports) == 2


class TestCandidatePorts:
    """Tests for the ordered candidates of one machine."""

    def test_the_first_candidate_is_the_port_of_the_hostname(self) -> None:
        # One machine carries one predictable number: the first candidate
        # is the port of the hostname itself, which is the number the
        # router rule of the other service publishes.
        config = make_config()
        assert next(candidate_ports(config, "dozor-gunid")) == desired_port(
            config, "dozor-gunid"
        )

    def test_the_order_is_stable_between_walks(self) -> None:
        config = make_config()
        assert list(islice(candidate_ports(config, "testhost"), 5)) == list(
            islice(candidate_ports(config, "testhost"), 5)
        )

    def test_every_candidate_is_inside_the_range(self) -> None:
        config = make_config()
        section = config.port_forwarding_setup
        for port in islice(candidate_ports(config, "testhost"), 50):
            assert section.desired_port_min <= port <= section.desired_port_max

    def test_no_candidate_is_offered_twice(self) -> None:
        config = make_config()
        ports = list(islice(candidate_ports(config, "testhost"), 50))
        assert len(set(ports)) == len(ports)

    def test_a_further_candidate_carries_the_attempt_number(self) -> None:
        config = make_config()
        second = list(islice(candidate_ports(config, "testhost"), 2))[1]
        assert second == desired_port(config, "testhost2")

    def test_the_walk_ends_when_the_range_is_exhausted(self) -> None:
        # The range holds a finite number of ports, so a walk offers every
        # port of the range once and then stops: the next candidate could
        # only repeat one of them. The bound comes from the range itself,
        # never from a configured attempt count.
        config = _tiny_range_config()
        assert sorted(candidate_ports(config, "testhost")) == [1000, 1001, 1002]
