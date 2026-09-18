"""Command: the country this machine currently sits in, per its services.

The System Metrics collector runs this command as its country network
module, so the network report says where the machine is. The detection
is the shared function of pyntara.location, the same one the
three_x_ui_xray_setup task uses for its routing profile, so the
geographic question is asked once in code.

The document carries the answers as they were given: the values the
services named, the word the decision used and whether an answer named
it. The geographic position itself lives in the values, so the report
shows what the services saw instead of a bare yes or no, which is what
makes the record useful for a machine that moves.

The service list, the word and the timeouts are the values of the
three_x_ui_xray_setup section, never duplicated here. No answer at all
is an error with the reason on stderr, so a silent detection is visible;
a machine outside the word's country is a normal answer, not a failure.
Runs as `python -m pyntara.country_report` (docs/spec/system-metrics.md,
section Report collector).
"""

from __future__ import annotations

import json
import sys

from pyntara.location import CountryReport, detect_country
from pyntara.values import engine as engine_values
from pyntara.values import three_x_ui_xray_setup as panel_values


def country_document(report: CountryReport, word: str) -> dict[str, object]:
    """The report record of the detection, answers included.

    The field names come from the declared REPORT_RECORD_KEYS, which the
    address commands of the collector share, so the shape of a record lives
    in one place. An answer that arrived as a JSON document keeps its parsed
    structure, never a JSON string; a text answer keeps its raw text.
    """

    keys = engine_values.REPORT_RECORD_KEYS
    return {
        keys["word"]: word,
        keys["in_country"]: report.in_country,
        keys["values"]: list(report.values),
        keys["answers"]: [
            {
                keys["source"]: answer.source,
                keys["values"]: list(answer.values),
                keys["document"]: (
                    answer.document if answer.document is not None else answer.raw
                ),
            }
            for answer in report.answers
        ],
    }


def main(argv: list[str]) -> int:
    """Print the country record; 0 when answered, 2 on a usage error.

    No service answering is reported on stderr with exit code 1, so the
    collector shows a failed detection instead of an empty module.
    """

    if len(argv) != 1:
        print(f"usage: {argv[0]}", file=sys.stderr)
        return 2
    if not panel_values.COUNTRY_SERVICES:
        print(
            "error: no country service is declared in the "
            "three_x_ui_xray_setup values",
            file=sys.stderr,
        )
        return 1
    report = detect_country(
        panel_values.COUNTRY_SERVICES,
        panel_values.COUNTRY_WORD,
        panel_values.COUNTRY_QUERY_TIMEOUT_SECONDS,
        panel_values.COUNTRY_COMMAND_TIMEOUT_SECONDS,
    )
    if not report.answers:
        print(
            "error: no country service answered for this machine",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            country_document(report, panel_values.COUNTRY_WORD),
            ensure_ascii=False,
            indent=engine_values.REPORT_JSON_INDENT,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
