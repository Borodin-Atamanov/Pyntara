"""Ingest System Metrics spool files into the queue.

The deployed systemd service system_metrics-ingest.service, started by
the path unit system_metrics-ingest.path whenever a file appears in the
spool, runs this module through the venv python. The module loads the
single system config from the command line argument and moves every
spool file into the queue main_outbox
(docs/spec/system-metrics.md, section Queue architecture).
"""

from __future__ import annotations

import sys
from pathlib import Path

from pyntara.config import load_config
from pyntara.metrics_commit import ingest_spool


def main() -> None:
    """Load the system config and ingest the spool once.

    The config path is the first command line argument; the ingest
    service unit renders the configured system_config_path into the
    ExecStart line. A missing argument is an explicit error: without a
    config the ingest cannot know the queue and spool paths.
    """

    if len(sys.argv) < 2:
        print("error: missing config path argument", file=sys.stderr)
        raise SystemExit(1)
    cfg = load_config(Path(sys.argv[1]))
    ingest_spool(cfg)


if __name__ == "__main__":
    main()
