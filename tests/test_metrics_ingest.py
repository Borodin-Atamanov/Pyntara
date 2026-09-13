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
from pyntara.config.engine import EngineConfig
from pyntara.metrics_ingest import main


def test_main_journals_under_the_configured_service_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The ingest announces itself in the journal under the identifier of its
    # own section, never under the engine name.
    config_path = tmp_path / "config.toml"
    config = make_config(task_data_root=tmp_path)
    configured: list[EngineConfig] = []
    monkeypatch.setattr("pyntara.metrics_ingest.load_config", lambda path: config)
    monkeypatch.setattr("pyntara.metrics_ingest.configure_journal", configured.append)
    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", lambda cfg: None)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_ingest", str(config_path)]
    )
    main()
    service_identifier = config.system_metrics_setup.service_journal_identifier
    assert configured[-1].journal_identifier == service_identifier


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


def test_main_reports_a_config_without_the_queue_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # An incomplete config cannot name the spool and the queue: the ingest
    # names the absent keys and never lets a traceback reach the path unit.
    config_path = tmp_path / "config.toml"
    config_path.write_text('[engine]\ntask_data_root = "/tmp"\n', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["pyntara.metrics_ingest", str(config_path)])
    main()
    captured = capsys.readouterr()
    assert captured.err == (
        "error: the ingest cannot run: [system_metrics_setup] has no spool_dir, "
        "spool_temp_prefix, system_metrics_dir, system_metrics_dir_mode, "
        "main_outbox_dir, temp_dir, max_queue_file_size_bytes, queue_file_mode, "
        "queue_file_suffix_length, queue_link_attempts\n"
    )
    assert "Traceback" not in captured.err


def test_main_reports_a_failed_ingest_in_one_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A failure while the ingest works is also reported in one line.
    config = make_config(task_data_root=tmp_path)

    def fail(cfg: Config) -> list[Path]:
        del cfg
        raise OSError("the spool is not readable")

    monkeypatch.setattr("pyntara.metrics_ingest.load_config", lambda path: config)
    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", fail)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_ingest", str(tmp_path / "config.toml")]
    )
    main()
    captured = capsys.readouterr()
    assert "error: the ingest failed: the spool is not readable" in captured.err
    assert "Traceback" not in captured.err
