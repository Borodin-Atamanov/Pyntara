"""Unit tests for the System Metrics spool ingest command.

The command is exercised with the config loader and the ingest function
mocked; the config path plumbing and the error path are the unit under
test (developer guide).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import make_config

from pyntara.config import Config
from pyntara.metrics_ingest import main


def test_main_loads_config_and_ingests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # main reads the config path from the argument, loads the config and
    # ingests the spool once.
    config_path = tmp_path / "config.toml"
    config = make_config(task_data_root=tmp_path)
    seen_paths: list[Path] = []
    ingested: list[Config] = []

    def fake_load(path: Path) -> Config:
        seen_paths.append(Path(path))
        return config

    def fake_ingest(cfg: Config) -> None:
        ingested.append(cfg)

    monkeypatch.setattr("pyntara.metrics_ingest.load_config", fake_load)
    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", fake_ingest)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_ingest", str(config_path)]
    )
    main()
    assert seen_paths == [config_path]
    assert ingested == [config]


def test_main_missing_config_argument_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Without a config path argument there is no way to know the queue and
    # spool paths: the command fails loudly with exit code 1.
    monkeypatch.setattr("sys.argv", ["pyntara.metrics_ingest"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "config path" in capsys.readouterr().err
