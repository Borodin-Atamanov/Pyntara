"""Tests for the shared GitHub release query.

Every task that installs a program from GitHub releases reads the latest
release through pyntara.github_release, so the endpoint, the curl settings
and the error messages cannot drift apart between the tasks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from support import FakeProc, make_config

from pyntara import github_release
from pyntara.utils import curl_flags

REPOSITORY = "owner/name"


def _engine(tmp_path: Path) -> Any:
    """The engine config of the tests, with a query URL of its own."""

    return make_config(
        task_data_root=tmp_path,
        github_latest_release_url=(
            "https://api.example.invalid/repos/{repo}/releases/latest"
        ),
    ).engine


def test_fetch_latest_release_queries_the_configured_url_with_the_curl_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The URL comes from the config with the repository substituted, and the
    # retry and timeout flags are the engine-wide curl settings, so every
    # task queries a release the same way.
    engine = _engine(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        del kwargs
        calls.append(list(command))
        return FakeProc(0, '{"tag_name": "v1.2.3"}')

    monkeypatch.setattr(github_release, "run_command", fake_run)
    payload = github_release.fetch_latest_release(REPOSITORY, engine)
    assert payload == {"tag_name": "v1.2.3"}
    assert calls == [
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            *curl_flags(
                engine.curl_timeout_seconds,
                engine.curl_retries,
                engine.curl_connect_timeout_seconds,
                engine.curl_retry_max_time_seconds,
                engine.curl_retry_delay_seconds,
            ),
            "https://api.example.invalid/repos/owner/name/releases/latest",
        ]
    ]


def test_fetch_latest_release_reports_a_failed_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed request is a RuntimeError naming the URL, so the caller
    # reports the reason instead of a raw subprocess error.
    engine = _engine(tmp_path)
    monkeypatch.setattr(
        github_release, "run_command", lambda *a, **k: FakeProc(22, "", "404")
    )
    with pytest.raises(RuntimeError, match="cannot fetch .*releases/latest: exit 22"):
        github_release.fetch_latest_release(REPOSITORY, engine)


def test_fetch_latest_release_reports_broken_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    monkeypatch.setattr(
        github_release, "run_command", lambda *a, **k: FakeProc(0, "not json")
    )
    with pytest.raises(RuntimeError, match="cannot parse the release JSON from"):
        github_release.fetch_latest_release(REPOSITORY, engine)


def test_fetch_latest_release_reports_a_payload_that_is_not_an_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A payload that is not an object is a RuntimeError like every other
    # unusable answer: a TypeError would leave the error report of the task
    # behind, because the tasks catch RuntimeError.
    engine = _engine(tmp_path)
    monkeypatch.setattr(
        github_release, "run_command", lambda *a, **k: FakeProc(0, "[1, 2]")
    )
    with pytest.raises(RuntimeError, match="is not an object"):
        github_release.fetch_latest_release(REPOSITORY, engine)


def test_release_tag_reads_the_tag() -> None:
    assert github_release.release_tag({"tag_name": "v1.2.3"}) == "v1.2.3"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tag_name": ""},
        {"tag_name": 42},
        {"tag_name": None},
    ],
)
def test_release_tag_rejects_a_payload_without_a_usable_tag(
    payload: dict[str, object],
) -> None:
    with pytest.raises(RuntimeError, match="release payload has no tag_name"):
        github_release.release_tag(payload)


def test_asset_name_urls_reads_the_pairs_in_the_order_of_the_api() -> None:
    assert github_release.asset_name_urls(
        {
            "assets": [
                {"name": "second.deb", "browser_download_url": "https://b"},
                {"name": "first.deb", "browser_download_url": "https://a"},
            ]
        }
    ) == [("second.deb", "https://b"), ("first.deb", "https://a")]


def test_asset_name_urls_skips_malformed_entries() -> None:
    # An asset entry that is not a table, or that carries no usable name or
    # url, is skipped instead of stopping the task that reads the release.
    assert github_release.asset_name_urls(
        {
            "assets": [
                "not a table",
                {"name": "no-url.deb"},
                {"browser_download_url": "https://no-name"},
                {"name": "ok.deb", "browser_download_url": "https://ok"},
            ]
        }
    ) == [("ok.deb", "https://ok")]


def test_asset_name_urls_rejects_a_payload_without_assets() -> None:
    with pytest.raises(RuntimeError, match="release payload has no assets array"):
        github_release.asset_name_urls({"tag_name": "v1"})
