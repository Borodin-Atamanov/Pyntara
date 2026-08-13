"""Live tests for the yggdrasil peer list download and parsing.

These tests hit the real network and are excluded from the default suite
by the live marker; run them manually during development with
pytest -m live (docs/spec/yggdrasil-service.md). They download the real
public-peers tarball, parse it with the task helpers and verify that the
resulting peer URIs are valid, but they never start the yggdrasil
service, which would require root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import make_config

from pyntara.tasks import yggdrasil_service_setup

pytestmark = pytest.mark.live


def _cfg(tmp_path: Path):
    """Config with temporary storage for the live download."""

    return make_config(
        task_data_root=tmp_path,
        yggdrasil_peers_full_path=tmp_path / "peers-full.txt",
    ).yggdrasil_service_setup


def test_live_download_and_parse(tmp_path: Path) -> None:
    # The real tarball downloads, the markdown files parse into peer URIs
    # and the full list lands next to the config.
    cfg = _cfg(tmp_path)
    peers = yggdrasil_service_setup._download_peers(cfg, 120)
    assert len(peers) > 50, "the public peers list should be large"
    assert cfg.peers_full_path.is_file()
    saved = cfg.peers_full_path.read_text(encoding="utf-8").splitlines()
    assert saved == peers


def test_live_peer_uris_are_valid(tmp_path: Path) -> None:
    # Every downloaded peer URI has one of the known schemes and a port,
    # and a meaningful share of them resolves: dead nodes in the list are
    # expected, but the list as a whole must be live.
    cfg = _cfg(tmp_path)
    peers = yggdrasil_service_setup._download_peers(cfg, 120)
    schemes = {
        "tcp",
        "tls",
        "quic",
        "ws",
        "wss",
        "socks",
        "sockstls",
        "unix",
    }
    resolvable = 0
    checked = 0
    for uri in peers:
        scheme = uri.split(":", 1)[0]
        assert scheme in schemes, f"unexpected scheme in {uri}"
        if uri.startswith(("socks", "sockstls", "unix")):
            continue
        checked += 1
        if yggdrasil_service_setup._resolve_uri_addrs(uri):
            resolvable += 1
    assert resolvable > 10, f"only {resolvable} of {checked} peers resolve"


def test_live_probe_pipeline(tmp_path: Path) -> None:
    # The full pipeline over real data: download, shuffle, batches and
    # best-pick selection complete without errors and produce peers.
    cfg = _cfg(tmp_path)
    peers = yggdrasil_service_setup._download_peers(cfg, 120)
    yggdrasil_service_setup.random.shuffle(peers)
    batch = peers[: cfg.peer_batch_size]
    assert len(batch) > 0
    picked = yggdrasil_service_setup._pick_best_peers(batch, {}, cfg.peer_target_count)
    assert len(picked) == cfg.peer_target_count
