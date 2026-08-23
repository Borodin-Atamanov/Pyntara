"""Unit tests for the NextDNS profile selection.

The profile selection is a pure function with no external dependencies;
the tests pin the deterministic choice and the profile ID shape.
"""

from __future__ import annotations

from pyntara.nextdns import profile_id_is_valid, select_profile_id


def test_profile_id_is_valid_accepts_six_hex() -> None:
    assert profile_id_is_valid("6c7f39") is True
    assert profile_id_is_valid("000000") is True
    assert profile_id_is_valid("ABCDEF") is False
    assert profile_id_is_valid("12345") is False
    assert profile_id_is_valid("1234567") is False
    assert profile_id_is_valid("zzzzzz") is False


def test_select_profile_id_is_deterministic() -> None:
    pool = ("111111", "222222", "333333", "aaaaaa", "bbbbbb")
    first = select_profile_id("myhost", pool)
    second = select_profile_id("myhost", pool)
    assert first == second
    assert first in pool


def test_select_profile_id_spreads_over_pool() -> None:
    # Different hostnames must not collapse onto a single profile: the
    # sha256 mod pool selection is spread by construction, and a few dozen
    # hostnames should touch at least two profiles of a small pool.
    pool = ("111111", "222222", "333333", "aaaaaa", "bbbbbb")
    chosen = {select_profile_id(f"host-{index}", pool) for index in range(50)}
    assert len(chosen) > 1


def test_select_profile_id_empty_pool_returns_none() -> None:
    assert select_profile_id("myhost", ()) is None


def test_select_profile_id_broken_id_returns_none() -> None:
    # A pool entry that is not a valid profile ID must never be selected.
    assert select_profile_id("myhost", ("zzzzzz",)) is None
    assert select_profile_id("myhost", ("12345",)) is None
