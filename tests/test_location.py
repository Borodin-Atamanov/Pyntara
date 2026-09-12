"""Unit tests for the country detection.

The services are mocked, so no test touches the network: the module only
standardizes the answers and merges them (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import pytest
from support import make_config

from pyntara import location as location_module
from pyntara.location import (
    DEFAULT_COUNTRY_WORD,
    ServiceAnswer,
    describe_answers,
    detect_country,
    find_country_word,
    merge_values,
    normalize_text,
    standardize_answer,
)

SERVICES = ("https://ip2c.org/self", "https://ifconfig.co/json")


class TestNormalizeText:
    """Tests for the word text a value is compared through."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("RU", "ru"),
            ("loc=RU", "loc ru"),
            ("colo=GRU", "colo gru"),
            ("Russian Federation", "russian federation"),
            ("1;RU;RUS;Russia", "1 ru rus russia"),
            ('  "country": "RU",', "country ru"),
            ("", ""),
        ],
    )
    def test_turns_every_separator_into_a_space(self, value: str, expected: str) -> None:
        assert normalize_text(value) == expected


class TestFindCountryWord:
    """Tests for the occurance search of the country word."""

    @pytest.mark.parametrize(
        "values",
        [
            ("Russia",),
            ("russia",),
            ("RUSSIA",),
            ("Russian Federation",),
            ("russian",),
            ("1;RU;RUS;Russia",),
            ("loc=Russia",),
            ('{"country": "Russia"}',),
        ],
    )
    def test_values_that_name_the_country_match(self, values: tuple[str, ...]) -> None:
        assert find_country_word(values, DEFAULT_COUNTRY_WORD) == "russia"

    @pytest.mark.parametrize(
        "values",
        [
            ("AR",),
            ("RU",),
            ("GRU",),
            ("Peru",),
            ("Belarus",),
            ("colo=GRU",),
            ("1;PE;PER;Peru",),
            ("RateLimited",),
            ("<html><body>error</body></html>",),
            (),
        ],
    )
    def test_values_without_the_word_do_not_match(
        self, values: tuple[str, ...]
    ) -> None:
        # The word is the whole country name, so the values that broke a
        # short-code search (the Cloudflare colo code GRU, the country
        # names Peru and Belarus, the bare code RU) carry none of its
        # letters in a row.
        assert find_country_word(values, DEFAULT_COUNTRY_WORD) is None

    def test_the_word_inside_a_longer_word_matches(self) -> None:
        # A plain occurrence search catches a word that merely contains
        # the letters, which is the accepted price of the simple rule.
        assert find_country_word(("Prussia",), DEFAULT_COUNTRY_WORD) == "russia"

    def test_an_empty_word_never_matches(self) -> None:
        assert find_country_word(("Russia",), "") is None

    def test_the_word_is_configurable(self) -> None:
        assert find_country_word(("Germany",), "germany") == "germany"


class TestStandardizeAnswer:
    """Tests for turning one answer of any format into values."""

    def test_a_bare_value_stays_a_value(self) -> None:
        answer = standardize_answer("s", "AR")
        assert answer.values == ("AR",)
        assert answer.fields == ()
        assert answer.raw == "AR"

    def test_a_named_record_yields_a_field(self) -> None:
        answer = standardize_answer("s", "loc=AR")
        assert answer.values == ("AR",)
        assert answer.fields == (("loc", "AR"),)

    def test_a_colon_record_yields_a_field(self) -> None:
        answer = standardize_answer("s", '"country": "Argentina",')
        assert answer.values == ("Argentina",)
        assert answer.fields == (("country", "Argentina"),)

    def test_a_separated_record_yields_one_value_per_part(self) -> None:
        answer = standardize_answer("s", "1;AR;ARG;Argentina")
        assert answer.values == ("1", "AR", "ARG", "Argentina")
        assert answer.fields == ()

    def test_a_flat_json_document_is_flattened(self) -> None:
        answer = standardize_answer("s", '{"ip":"1.2.3.4","country":"AR"}')
        assert answer.values == ("1.2.3.4", "AR")
        assert answer.fields == (("ip", "1.2.3.4"), ("country", "AR"))

    def test_a_pretty_printed_document_is_read_as_a_whole(self) -> None:
        answer = standardize_answer(
            "s", '{\n  "country": "Russia",\n  "country_code": "RU"\n}'
        )
        assert answer.values == ("Russia", "RU")
        assert answer.fields == (("country", "Russia"), ("country_code", "RU"))

    def test_a_nested_document_keeps_the_path_of_every_value(self) -> None:
        answer = standardize_answer(
            "s", '{"connection": {"asn": 27747}, "borders": ["BR", "UY"]}'
        )
        assert answer.fields == (
            ("connection.asn", "27747"),
            ("borders[0]", "BR"),
            ("borders[1]", "UY"),
        )

    def test_a_trace_document_yields_one_field_per_line(self) -> None:
        answer = standardize_answer("s", "ip=1.2.3.4\ncolo=EZE\nloc=AR")
        assert answer.values == ("1.2.3.4", "EZE", "AR")
        assert answer.fields == (("ip", "1.2.3.4"), ("colo", "EZE"), ("loc", "AR"))

    def test_an_unexpected_answer_is_kept_as_a_value(self) -> None:
        answer = standardize_answer("s", "<html><body>error</body></html>")
        assert answer.values == ("<html><body>error</body></html>",)

    def test_an_empty_answer_carries_nothing(self) -> None:
        answer = standardize_answer("s", "   ")
        assert answer == ServiceAnswer(source="s", raw="   ", values=(), fields=())


class TestMergeValues:
    """Tests for merging the answers of several services without loss."""

    def test_keeps_the_arrival_order_and_removes_repeats(self) -> None:
        answers = (
            ServiceAnswer("a", "AR", ("AR", "AR"), ()),
            ServiceAnswer("b", "RU", ("RUS",), ()),
            ServiceAnswer("c", "", ("AR",), ()),
        )
        assert merge_values(answers) == ("AR", "RUS")


class TestDetectCountry:
    """Tests for the whole detection over the shared query helper."""

    def test_decides_from_one_service_when_the_others_are_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[tuple[str, ...], float, float]] = []

        def fake_fetch(
            engine: object, urls: tuple[str, ...], query: float, command: float
        ) -> tuple:
            calls.append((urls, query, command))
            return (
                ("https://ip2c.org/self", "1;RU;RUS;Russia"),
                ("https://ifconfig.co/json", ""),
            )

        monkeypatch.setattr(location_module, "fetch_urls_by_source", fake_fetch)
        report = detect_country(
            make_config().engine, SERVICES, DEFAULT_COUNTRY_WORD, 60, 1800.0
        )
        assert report.in_country is True
        assert report.matched_word == "russia"
        assert report.values == ("1", "RU", "RUS", "Russia")
        assert calls == [(SERVICES, 60, 1800.0)]

    def test_reports_no_country_when_no_answer_names_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            location_module,
            "fetch_urls_by_source",
            lambda *a, **k: (("https://ip2c.org/self", "1;AR;ARG;Argentina"),),
        )
        report = detect_country(
            make_config().engine, SERVICES, DEFAULT_COUNTRY_WORD, 60, 1800.0
        )
        assert report.in_country is False
        assert report.matched_word is None

    def test_reports_no_country_when_nothing_answered(self) -> None:
        # An empty service list must not start a query and must not turn
        # into a country.
        report = detect_country(
            make_config().engine, (), DEFAULT_COUNTRY_WORD, 60, 1800.0
        )
        assert report.in_country is False
        assert report.answers == ()
        assert report.values == ()


class TestDescribeAnswers:
    """Tests for the log lines of one report."""

    def test_names_every_service_and_its_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            location_module,
            "fetch_urls_by_source",
            lambda *a, **k: (
                ("https://ip2c.org/self", "1;RU;RUS;Russia"),
                ("https://ifconfig.co/json", ""),
            ),
        )
        report = detect_country(
            make_config().engine, SERVICES, DEFAULT_COUNTRY_WORD, 60, 1800.0
        )
        assert describe_answers(report) == (
            "https://ip2c.org/self: 1, RU, RUS, Russia",
            "https://ifconfig.co/json: no answer",
        )

