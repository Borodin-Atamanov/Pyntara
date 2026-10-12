"""Unit tests for the shared deb download and install helpers.

The download and the apt install that i2pd_service_setup, rustdesk_setup and
yggdrasil_service_setup share live in pyntara.deb_package. These tests check the
shared contract directly: the refresh rule (an install refreshes the apt index
only when the caller gives an update timeout and does not skip updates, so the
yggdrasil package that depends only on systemd never refreshes) and the retry
loop.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyntara import deb_package


def test_install_deb_refreshes_the_index_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An install that is given an update timeout and no skip refreshes the apt
    # index once before the install, then installs the downloaded file.
    events: list[str] = []

    def record_refresh(timeout: float) -> None:
        events.append("refresh")

    def record_install(path: str, timeout: float) -> tuple[bool, str]:
        events.append(f"install {path}")
        return True, ""

    monkeypatch.setattr(deb_package, "refresh_apt_index", record_refresh)
    monkeypatch.setattr(deb_package, "install_package_once", record_install)

    ok, error = deb_package.install_deb(
        tmp_path,
        "program.deb",
        install_timeout=10.0,
        retries=0,
        update_timeout=5.0,
    )

    assert ok is True
    assert error == ""
    assert events == ["refresh", f"install {tmp_path / 'program.deb'}"]


def test_install_deb_skips_the_refresh_when_skipping_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A test or offline run skips the apt index refresh; the install still runs.
    events: list[str] = []
    monkeypatch.setattr(
        deb_package, "refresh_apt_index", lambda timeout: events.append("refresh")
    )
    monkeypatch.setattr(
        deb_package, "install_package_once", lambda path, timeout: (True, "")
    )

    ok, _ = deb_package.install_deb(
        tmp_path,
        "program.deb",
        install_timeout=10.0,
        retries=0,
        update_timeout=5.0,
        skip_update=True,
    )

    assert ok is True
    assert events == []


def test_install_deb_never_refreshes_without_an_update_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # yggdrasil_service_setup passes no update timeout: its package depends only
    # on systemd, so the shared helper must refresh nothing for it.
    events: list[str] = []
    monkeypatch.setattr(
        deb_package, "refresh_apt_index", lambda timeout: events.append("refresh")
    )
    monkeypatch.setattr(
        deb_package, "install_package_once", lambda path, timeout: (True, "")
    )

    ok, _ = deb_package.install_deb(
        tmp_path, "program.deb", install_timeout=10.0, retries=0
    )

    assert ok is True
    assert events == []


def test_install_deb_retries_until_the_install_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The first attempt fails and the second succeeds, so the loop stops there
    # instead of spending every retry.
    attempts: list[str] = []

    def install_failing_once(path: str, timeout: float) -> tuple[bool, str]:
        attempts.append(path)
        if len(attempts) >= 2:
            return True, ""
        return False, "transient failure"

    monkeypatch.setattr(deb_package, "refresh_apt_index", lambda timeout: None)
    monkeypatch.setattr(deb_package, "install_package_once", install_failing_once)

    ok, error = deb_package.install_deb(
        tmp_path,
        "program.deb",
        install_timeout=10.0,
        retries=3,
        update_timeout=5.0,
    )

    assert ok is True
    assert error == ""
    assert len(attempts) == 2


def test_install_deb_reports_a_failed_index_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A refresh that fails stops before the install and reports the reason, so
    # the caller can warn instead of installing against a stale index.
    def failing_refresh(timeout: float) -> None:
        raise subprocess.CalledProcessError(1, ["apt-get", "update"])

    called_install = False

    def install(path: str, timeout: float) -> tuple[bool, str]:
        nonlocal called_install
        called_install = True
        return True, ""

    monkeypatch.setattr(deb_package, "refresh_apt_index", failing_refresh)
    monkeypatch.setattr(deb_package, "install_package_once", install)

    ok, error = deb_package.install_deb(
        tmp_path,
        "program.deb",
        install_timeout=10.0,
        retries=0,
        update_timeout=5.0,
    )

    assert ok is False
    assert "apt index refresh" in error
    assert called_install is False
