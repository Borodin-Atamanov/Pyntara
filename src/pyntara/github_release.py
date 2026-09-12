"""Read the latest release of a GitHub repository.

Five tasks install a program from GitHub releases: dnsproxy_setup,
i2pd_service_setup, rustdesk_setup, three_x_ui_xray_setup and
yggdrasil_service_setup. Each one used to carry its own copy of the query,
so the endpoint, the curl flags and the error messages could drift apart;
the query lives here once and the tasks import it (architecture contract,
Configuration). The task keeps its own asset selection, because which
asset fits a machine is knowledge of that task.
"""

from __future__ import annotations

import json

from pyntara.config import EngineConfig
from pyntara.utils import release_query_command, run_command


def fetch_latest_release(repository: str, engine: EngineConfig) -> dict[str, object]:
    """Return the payload of the latest release of a GitHub repository.

    repository is the owner/name pair, for example AdguardTeam/dnsproxy.
    The URL is the engine-wide github_latest_release_url with {repo}
    replaced and the command is the engine-wide release query, so every
    task queries a release the same way. Raises RuntimeError when the
    request fails or the payload is not usable JSON, so the caller reports
    the reason instead of a raw exception.
    """

    url = engine.github_latest_release_url.format(repo=repository)
    result = run_command(
        release_query_command(engine, url),
        check=False,
        capture=True,
        timeout=engine.command_timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot fetch {url}: exit {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cannot parse the release JSON from {url}: {exc}") from None
    if not isinstance(payload, dict):
        # RuntimeError and not TypeError: every caller catches RuntimeError
        # and reports the reason, so an unusable payload reaches the user as
        # the message of the task instead of escaping to the runner.
        raise RuntimeError(  # noqa: TRY004
            f"the release payload from {url} is not an object"
        )
    return payload


def release_tag(payload: dict[str, object]) -> str:
    """Return the tag of a release payload; raises RuntimeError when absent."""

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("release payload has no tag_name")
    return tag


def asset_name_urls(payload: dict[str, object]) -> list[tuple[str, str]]:
    """Return the (name, download url) pairs of a release payload.

    Malformed asset entries are skipped and the order of the API is kept.
    Raises RuntimeError when the payload carries no assets array.
    """

    assets = payload.get("assets")
    if not isinstance(assets, list):
        # RuntimeError for the same reason as an unusable payload above.
        raise RuntimeError(  # noqa: TRY004
            "release payload has no assets array"
        )
    result: list[tuple[str, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result.append((name, url))
    return result
