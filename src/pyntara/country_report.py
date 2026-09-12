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

The service list, the word and the timeouts come from the
[three_x_ui_xray_setup] section of the single system config, never
duplicated here. No answer at all is an error with the reason on stderr,
so a silent detection is visible; a machine outside the word's country
is a normal answer, not a failure. Runs as `python -m
pyntara.country_report CONFIG_PATH` (docs/spec/system-metrics.md,
section Report collector).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyntara.config import absent_config_keys, load_config
from pyntara.location import CountryReport, detect_country


def country_document(report: CountryReport, word: str) -> dict[str, object]:
    """The report record of the detection, answers included."""

    return {
        "word": word,
        "in_country": report.in_country,
        "values": list(report.values),
        "answers": [
            {
                "source": answer.source,
                "values": list(answer.values),
                "raw": answer.raw,
            }
            for answer in report.answers
        ],
    }


def main(argv: list[str]) -> int:
    """Print the country record; 0 when answered, 2 on a usage error.

    No service answering is reported on stderr with exit code 1, so the
    collector shows a failed detection instead of an empty module.
    """

    if len(argv) != 2:
        print(f"usage: {argv[0]} CONFIG_PATH", file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    setup = cfg.three_x_ui_xray_setup
    missing = absent_config_keys(
        setup,
        (
            "country_services",
            "country_word",
            "country_query_timeout_seconds",
            "country_command_timeout_seconds",
        ),
    )
    if missing:
        print(
            "error: the three_x_ui_xray_setup section of the config has no "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    if not setup.country_services:
        print(
            "error: no country service is configured in the "
            "three_x_ui_xray_setup section of the config",
            file=sys.stderr,
        )
        return 1
    report = detect_country(
        cfg.engine,
        setup.country_services,
        setup.country_word,
        setup.country_query_timeout_seconds,
        setup.country_command_timeout_seconds,
    )
    if not report.answers:
        print(
            "error: no country service answered for this machine",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            country_document(report, setup.country_word),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
