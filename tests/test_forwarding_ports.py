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

from itertools import islice

import pytest

from pyntara.forwarding_ports import candidate_ports, desired_port
from pyntara.values import port_forwarding_setup as values


@pytest.fixture
def tiny_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the declared range at exactly three ports."""

    monkeypatch.setattr(values, "DESIRED_PORT_MIN", 1000)
    monkeypatch.setattr(values, "DESIRED_PORT_MAX", 1002)


class TestDesiredPort:
    def test_deterministic_and_in_range(self) -> None:
        first = desired_port("dozor-gunid")
        second = desired_port("dozor-gunid")
        assert first == second
        assert values.DESIRED_PORT_MIN <= first
        assert first <= values.DESIRED_PORT_MAX

    def test_differs_across_hostnames(self) -> None:
        ports = {desired_port(hostname) for hostname in ("aaa-babab", "bbb-babab")}
        assert len(ports) == 2


class TestCandidatePorts:
    """Tests for the ordered candidates of one machine."""

    def test_the_first_candidate_is_the_port_of_the_hostname(self) -> None:
        # One machine carries one predictable number: the first candidate
        # is the port of the hostname itself, which is the number the
        # router rule of the other service publishes.
        assert next(candidate_ports("dozor-gunid")) == desired_port("dozor-gunid")

    def test_the_order_is_stable_between_walks(self) -> None:
        assert list(islice(candidate_ports("testhost"), 5)) == list(
            islice(candidate_ports("testhost"), 5)
        )

    def test_every_candidate_is_inside_the_range(self, tiny_range: None) -> None:
        for port in islice(candidate_ports("testhost"), 50):
            assert values.DESIRED_PORT_MIN <= port <= values.DESIRED_PORT_MAX

    def test_no_candidate_is_offered_twice(self, tiny_range: None) -> None:
        ports = list(islice(candidate_ports("testhost"), 50))
        assert len(set(ports)) == len(ports)

    def test_a_further_candidate_carries_the_attempt_number(
        self, tiny_range: None
    ) -> None:
        second = list(islice(candidate_ports("testhost"), 2))[1]
        assert second == desired_port("testhost2")

    def test_the_walk_ends_when_the_range_is_exhausted(self, tiny_range: None) -> None:
        # The range holds a finite number of ports, so a walk offers every
        # port of the range once and then stops: the next candidate could
        # only repeat one of them. The bound comes from the range itself,
        # never from a configured attempt count.
        assert sorted(candidate_ports("testhost")) == [1000, 1001, 1002]
