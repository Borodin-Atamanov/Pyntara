"""Unit tests for the country command (pyntara.country_report).

The command runs on the target system as the country network module of
the System Metrics collector: it asks the configured services what
country they see and prints the decision with the answers. The shared
detection is replaced by a fake, so the tests assert the document shape
and the exit codes without a network.
"""

from __future__ import annotations

import pytest

from pyntara import country_report
from pyntara.location import CountryReport, ServiceAnswer
from pyntara.values import engine as engine_values
from pyntara.values import three_x_ui_xray_setup as panel_values

# The field names of a record, as the declared values carry them: the tests
# never spell one themselves.
RECORD_KEYS = engine_values.REPORT_RECORD_KEYS


def _report(*answers: ServiceAnswer, word: str | None = None) -> CountryReport:
    values: list[str] = []
    for answer in answers:
        for value in answer.values:
            if value and value not in values:
                values.append(value)
    return CountryReport(
        answers=tuple(answers), values=tuple(values), matched_word=word
    )


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
                "document": "1;AR;ARG;Argentina",
            }
        ],
    }


def test_a_json_answer_is_kept_structured_not_a_string() -> None:
    # A service that answers with a JSON document contributes its parsed
    # structure to the report, so the report carries no JSON string.
    report = _report(
        ServiceAnswer(
            source="https://ifconfig.co/json",
            raw='{"country": "AR", "country_name": "Argentina"}',
            values=("AR", "Argentina"),
            fields=(("country", "AR"), ("country_name", "Argentina")),
            document={"country": "AR", "country_name": "Argentina"},
        )
    )
    document = country_report.country_document(report, "russia")
    assert document["answers"] == [
        {
            "source": "https://ifconfig.co/json",
            "values": ["AR", "Argentina"],
            "document": {"country": "AR", "country_name": "Argentina"},
        }
    ]


def test_the_record_keys_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The field names of the record are declared values: another map is the
    # document the command prints, so the shape lives in one place.
    report = _report(
        ServiceAnswer(
            source="https://ip2c.org/self",
            raw="1;AR;ARG;Argentina",
            values=("AR",),
            fields=(),
        )
    )
    keys = dict(RECORD_KEYS)
    keys["word"] = "target"
    keys["in_country"] = "hit"
    keys["values"] = "seen"
    keys["answers"] = "replies"
    keys["source"] = "service"
    keys["document"] = "body"
    monkeypatch.setattr(engine_values, "REPORT_RECORD_KEYS", keys)
    assert country_report.country_document(report, "russia") == {
        "target": "russia",
        "hit": False,
        "seen": ["AR"],
        "replies": [
            {
                "service": "https://ip2c.org/self",
                "seen": ["AR"],
                "body": "1;AR;ARG;Argentina",
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
) -> None:
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
    assert country_report.main(["country_report"]) == 0
    captured = capsys.readouterr()
    assert "Argentina" in captured.out
    assert "russia" in captured.out
    assert captured.err == ""


def test_main_reports_a_silent_detection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(country_report, "detect_country", _fake_detection(_report()))
    assert country_report.main(["country_report"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no country service answered" in captured.err


def test_a_value_that_is_not_declared_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing is asked of a silent network when no country service is
    # declared: the reason says that the value carries no service.
    monkeypatch.setattr(panel_values, "COUNTRY_SERVICES", ())
    assert country_report.main(["country_report"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no country service is declared" in captured.err


def test_usage_rejects_a_config_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert country_report.main(["country_report", "/etc/pyntara.toml"]) == 2
    assert "usage" in capsys.readouterr().err
