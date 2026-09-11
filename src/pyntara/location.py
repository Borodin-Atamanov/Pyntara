"""Detection of the country a machine currently sits in.

The module asks a list of services what country they see, standardizes
every answer whatever its format and merges the answers without losing
what they carried, so a task can pick the routing profile that fits where
the machine is right now (docs/spec/3x-ui.md). The machine may move, so
the check runs on every task run.

Standardization turns one answer into two things: the raw text exactly as
the service sent it, and the scalar values it carried, with their names
when the format has names. All four formats met in practice are handled:
a bare value ("AR"), a named record ("loc=AR"), a separated record
("1;AR;ARG;Argentina") and a JSON document, flat or nested, taken as a
whole or line by line.

Merging keeps every value of every service in arrival order with the
repeats removed, so one service answers the question even when the others
are down or answer something unexpected, and nothing is dropped on the
way.

The decision is a plain occurrence search: every value is lowercased,
every separator becomes a space and the configured word is looked for
inside the value. The word is "russia" by default, so "Russia",
"Russian Federation" and "loc=Russia" all name the country, while
"GRU" (a Cloudflare colo code), "Peru" and "Belarus" name nothing,
because none of them carries the letters of the word.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pyntara.utils import fetch_urls_by_source

# The word that names the country. The search is case-insensitive, so the
# single word covers "russia", "Russia" and "RUSSIA"; the value lives in
# the config, so the operator can point the same check at another country
# without touching the code.
DEFAULT_COUNTRY_WORD = "russia"


@dataclass(frozen=True)
class ServiceAnswer:
    """One service answer, standardized without losing anything.

    raw is the answer exactly as it arrived. values holds every scalar the
    answer carried, in the order of the document, and fields holds the
    same scalars with their names when the format provided names; a bare
    value or a separated record without names fills values only.
    """

    source: str
    raw: str
    values: tuple[str, ...]
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CountryReport:
    """Everything the services said and what the merge decided.

    matched_word is the configured word when an answer carried it, or None
    when no answer named the country: the check never guesses, it reports
    what the services actually said.
    """

    answers: tuple[ServiceAnswer, ...]
    values: tuple[str, ...]
    matched_word: str | None

    @property
    def in_country(self) -> bool:
        """True when at least one answer named the country."""

        return self.matched_word is not None


def normalize_text(value: str) -> str:
    """Lowercase text with every non-alphanumeric character turned into a space.

    Single spaces stay between the words, so a multi-word answer can be
    searched word by word. "GRU" becomes "gru" and carries no word
    "russia", while "loc=Russia" becomes "loc russia" and does.
    """

    spaced = "".join(char if char.isalnum() else " " for char in value.casefold())
    return " ".join(spaced.split())


def find_country_word(values: tuple[str, ...], word: str) -> str | None:
    """The configured word when any value contains it, or None.

    The value is normalized the same way as the word, so the search
    ignores case and separators and a plain occurrence decides: "russia"
    is found in "Russia", in "loc=Russia" and in "Russian Federation",
    and it is not found in "GRU", "Peru" or "Belarus". A word that
    merely contains the letters elsewhere ("Prussia") would be caught
    too, which is the accepted price of a plain occurrence search.
    """

    needle = normalize_text(word)
    if not needle:
        return None
    for value in values:
        if needle in normalize_text(value):
            return word
    return None


def _try_json(text: str) -> dict[str, object] | list[object] | None:
    """A JSON document or array, or None for anything else.

    A bare scalar is not a document: a service answering "1" must stay a
    plain value instead of becoming a JSON field without a name.
    """

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _flatten_json(value: object, prefix: str = "") -> list[tuple[str, str]]:
    """Every scalar of a JSON document as (path, text) in document order.

    Nested objects extend the path with a dot and arrays with an index, so
    the origin of a value stays visible in the merged result; an empty
    prefix (the document root is an array) leaves the path without a
    leading separator.
    """

    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            pairs.extend(_flatten_json(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pairs.extend(_flatten_json(item, f"{prefix}[{index}]"))
    elif value is not None:
        pairs.append((prefix, str(value)))
    return pairs


def _split_named_record(line: str) -> tuple[tuple[str, str], ...]:
    """The named value of a "key=value" or "key: value" line, or nothing.

    The name is the part before the first separator, the value is
    everything after it with the trailing comma and quotes of a JSON-like
    line removed, so a line copied out of a JSON document still yields a
    clean value.
    """

    for separator in ("=", ":"):
        if separator in line:
            name, _, raw_value = line.partition(separator)
            name = name.strip().strip('"').strip()
            value = raw_value.strip().strip(",").strip().strip('"').strip()
            if name and value:
                return ((name, value),)
    return ()


def standardize_answer(source: str, raw: str) -> ServiceAnswer:
    """Standardize one answer of one service, whatever its format.

    A whole JSON document is flattened at once, so a pretty-printed JSON
    answer is read as the document it is. Otherwise every line is read on
    its own: a line that is a JSON document is flattened, a named record
    yields a field, and a separated record yields one value per part. A
    line that fits none of the shapes stays a plain value, so an
    unexpected answer is kept instead of being thrown away.
    """

    text = raw.strip()
    if not text:
        return ServiceAnswer(source=source, raw=raw, values=(), fields=())
    document = _try_json(text)
    if document is not None:
        fields = tuple(_flatten_json(document))
        return ServiceAnswer(
            source=source,
            raw=raw,
            values=tuple(value for _, value in fields),
            fields=fields,
        )
    fields_out: list[tuple[str, str]] = []
    values: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line_document = _try_json(line)
        if line_document is not None:
            for name, value in _flatten_json(line_document):
                fields_out.append((name, value))
                values.append(value)
            continue
        named = _split_named_record(line)
        if named:
            for name, value in named:
                fields_out.append((name, value))
                values.append(value)
            continue
        parts = [part.strip() for part in line.split(";")] if ";" in line else [line]
        values.extend(part for part in parts if part)
    return ServiceAnswer(
        source=source,
        raw=raw,
        values=tuple(values),
        fields=tuple(fields_out),
    )


def merge_values(answers: tuple[ServiceAnswer, ...]) -> tuple[str, ...]:
    """Every value of every answer, in arrival order with repeats removed.

    A value several services agree on counts once; a value only one
    service reported is kept as it is, so merging loses nothing.
    """

    merged: list[str] = []
    for answer in answers:
        for value in answer.values:
            if value and value not in merged:
                merged.append(value)
    return tuple(merged)


def detect_country(
    services: tuple[str, ...],
    word: str,
    query_timeout_seconds: float,
    command_timeout_seconds: float,
) -> CountryReport:
    """Ask every service, standardize and merge the answers, decide the country.

    An empty service list, an unreachable service list or answers that do
    not carry the word all end in matched_word=None, which the caller
    reads as "not that country".
    """

    answers = tuple(
        standardize_answer(source, raw)
        for source, raw in fetch_urls_by_source(
            services, query_timeout_seconds, command_timeout_seconds
        )
    )
    values = merge_values(answers)
    return CountryReport(
        answers=answers,
        values=values,
        matched_word=find_country_word(values, word),
    )


def describe_answers(report: CountryReport) -> tuple[str, ...]:
    """One line per service with the values it contributed, for the log.

    A service that answered nothing is reported as silent, so the log
    shows which service carried the decision and which one let the check
    down.
    """

    lines: list[str] = []
    for answer in report.answers:
        if answer.values:
            lines.append(f"{answer.source}: {', '.join(answer.values)}")
        else:
            lines.append(f"{answer.source}: no answer")
    return tuple(lines)
