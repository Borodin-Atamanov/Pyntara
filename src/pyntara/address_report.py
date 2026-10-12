"""Print the JSON access record of an address command.

tor_address, i2pd_address and yggdrasil_address each resolved their own channel
and printed the record with the same main wrapper: a usage check, the record
from their own access record function, and one JSON document or an error on
stderr. The wrapper lives here once and the three commands pass their own
function, so the exit codes and the output form can never drift apart.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from pyntara.values import engine as engine_values


def run(
    argv: list[str],
    access_record: Callable[[], tuple[dict[str, object] | None, str]],
) -> int:
    """Print the record; 0 when found, 2 on a usage error, 1 otherwise."""

    if len(argv) != 1:
        print(f"usage: {argv[0]}", file=sys.stderr)
        return 2
    record, error = access_record()
    if record is None:
        print(error, file=sys.stderr)
        return 1
    print(
        json.dumps(
            record,
            ensure_ascii=False,
            indent=engine_values.REPORT_JSON_INDENT,
        )
    )
    return 0
