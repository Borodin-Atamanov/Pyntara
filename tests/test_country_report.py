"""Unit tests for the country command (pyntara.country_report).

The command runs on the target system as the country network module of
the System Metrics collector: it asks the configured services what
country they see and prints the decision with the answers. The shared
detection is replaced by a fake, so the tests assert the document shape
and the exit codes without a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import base_config, write_config

from pyntara import country_report
from pyntara.location import CountryReport, ServiceAnswer


def _config(tmp_path: Path) -> Path:
    return write_config(tmp_path, base_config())


def _report(*answers: ServiceAnswer, word: str | None = None) -> CountryReport:
    values: list[str] = []
    for answer in answers:
        for value in answer.values:
            if value and value not in values:
                values.append(value)
    return CountryReport(answers=tuple(answers), values=tuple(values), matched_word=word)


def _fake_detection(report: CountryReport):
    def detect(*args: object, **kwargs: object) -> CountryReport:
        return report

    return detect


def test_document_carries_the_decision_and_the_answers() -> None:
    # The position itself lives in the values the services named, so the
    # document keeps them instead of a bare yes or no.
    report = _report(
        ServiceAnswer(
            source="https://ip2c.org/self",
            raw="1;AR;ARG;Argentina",
            values=("1", "AR", "ARG", "Argentina"),
            fields=(),
        )
    )
    document = country_report.country_document(report, "russia")
    assert document == {
        "word": "russia",
        "in_country": False,
        "values": ["1", "AR", "ARG", "Argentina"],
        "answers": [
            {
                "source": "https://ip2c.org/self",
                "values": ["1", "AR", "ARG", "Argentina"],
                "raw": "1;AR;ARG;Argentina",
            }
        ],
    }


def test_document_marks_a_named_country() -> None:
    report = _report(
        ServiceAnswer(
            source="https://ifconfig.co/json",
            raw="loc=Russia",
            values=("loc=Russia",),
            fields=(),
        ),
        word="russia",
    )
    assert country_report.country_document(report, "russia")["in_country"] is True


def test_main_prints_the_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        country_report,
        "detect_country",
        _fake_detection(
            _report(
                ServiceAnswer(
                    source="https://ipwho.is/",
                    raw="Argentina",
                    values=("Argentina",),
                    fields=(),
                )
            )
        ),
    )
    assert country_report.main(["country_report", str(config_path)]) == 0
    captured = capsys.readouterr()
    assert "Argentina" in captured.out
    assert "russia" in captured.out
    assert captured.err == ""


def test_main_reports_a_silent_detection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        country_report, "detect_country", _fake_detection(_report())
    )
    assert country_report.main(["country_report", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no country service answered" in captured.err


def test_empty_service_list_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # An empty service list is a configuration gap, not a silent answer:
    # the reason says that nothing was configured.
    content = base_config().replace(
        'country_services = ["https://ip2c.org/self", '
        '"https://ifconfig.co/json", "https://ipwho.is/"]',
        "country_services = []",
    )
    config_path = write_config(tmp_path, content)
    assert country_report.main(["country_report", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no country service is configured" in captured.err


def test_missing_config_key_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    content = base_config().replace('country_word = "russia"\n', "")
    config_path = write_config(tmp_path, content)
    assert country_report.main(["country_report", str(config_path)]) == 1
    assert "country_word" in capsys.readouterr().err


def test_usage_requires_the_config_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert country_report.main(["country_report"]) == 2
    assert "usage" in capsys.readouterr().err
