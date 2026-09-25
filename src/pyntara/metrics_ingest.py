"""Ingest System Metrics spool files into the queue.

The deployed systemd service system_metrics-ingest.service, started by
the path unit system_metrics-ingest.path whenever a file appears in the
spool, runs this module through the venv python. Every path of the queue
and of the spool is a declared value of pyntara.values.system_metrics_setup,
so the module takes no argument and moves every spool file into the queue
main_outbox (docs/spec/system-metrics.md, section Queue architecture).
"""

from __future__ import annotations

import sys

from pyntara.logger import configure_journal
from pyntara.metrics_commit import ingest_spool
from pyntara.values import system_metrics_setup as values


def main() -> None:
    """Ingest the spool once.

    The ingest reads every path it needs from the declared values of
    pyntara.values.system_metrics_setup, so it takes no argument at all
    and the unit starts it the same way whatever the machine carries. A
    run that fails or leaves an entry unpublished exits nonzero with one
    readable line, so the systemd restart policy retries the whole spool
    instead of waiting for the next file to appear beside a stuck one.
    """

    configure_journal(values.SERVICE_JOURNAL_IDENTIFIER)
    try:
        left_behind = ingest_spool()
    except Exception as exc:
        # A failed run reports one line and exits nonzero, never a
        # traceback, so systemd retries the whole spool.
        print(
            f"error: the ingest failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    if left_behind:
        print(
            f"error: the ingest left {left_behind} spool entries unpublished",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
