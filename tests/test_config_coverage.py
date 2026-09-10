"""Coverage guards for the config sources.

The repository config/ directory is the single source of truth for the
values the engine and the deployed service use, and the test suite keeps
two copies of it: the shared document in config_helpers.py that the
wrong-type tests mutate, and VALID_TOML in test_config.py that the
end-to-end cases load. Nothing else compares the three: a section that lost
its parser, a key that no parser reads, or a copy that fell behind the real
config would stay invisible until the target machine refused to run or
silently ignored a setting (architecture contract, Configuration).

These tests read the real config directory and compare all three forms.
"""

from __future__ import annotations

import tomllib
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest
from config_helpers import base_config
from support import make_config
from test_config import VALID_TOML

from pyntara.config import Config, load_config
from pyntara.config.loader import render_config_source

REPOSITORY_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

# The test documents, by name, so a failing guard names the copy that fell
# behind instead of only saying that something did.
TEST_DOCUMENTS: dict[str, str] = {
    "config_helpers.base_config": base_config(),
    "test_config.VALID_TOML": VALID_TOML,
}

# Keys of the repository config that the test document leaves out because
# the parser treats them as optional, each documented as such next to the
# field: a missing array means the value is empty and the task removes what
# it would otherwise write. A new key with the same property must be
# recorded here, so an accidental omission cannot pass unnoticed.
OPTIONAL_SECTION_KEYS: dict[str, frozenset[str]] = {
    "kde_settings": frozenset({"kconfig", "places_hidden"}),
    "ssh_client_setup": frozenset({"directives"}),
    "vault_structure": frozenset({"groups"}),
}

# Fields a parser derives from other keys instead of reading a key of their
# own. A new derived field must be recorded here as well: a field that no
# key can ever set is worth a deliberate decision, not an accident.
DERIVED_SECTION_FIELDS: dict[str, frozenset[str]] = {
    "three_x_ui_xray_setup": frozenset(
        {
            "cert_fullchain",
            "cert_privkey",
            "self_signed_cert_fullchain",
            "self_signed_cert_privkey",
        }
    ),
}


def _top_level_tables(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the TOML tables of a parsed document.

    Arrays of tables such as [[tasks]] are left out: they are validated by
    their own parser and have no key set of a section.
    """

    tables: dict[str, dict[str, Any]] = {}
    for name, value in document.items():
        if isinstance(value, dict):
            tables[name] = value
    return tables


def _repository_config_document() -> dict[str, Any]:
    """The repository config parsed as plain TOML, without the typed Config."""

    return tomllib.loads(render_config_source(REPOSITORY_CONFIG_DIR))


def _section_field_names(section: object) -> set[str]:
    """Field names of a section dataclass, empty for a non-dataclass value."""

    if not is_dataclass(section):
        return set()
    return {field.name for field in fields(section)}


def _document_by_name(document_name: str) -> dict[str, Any]:
    """The parsed TOML of a test document."""

    return tomllib.loads(TEST_DOCUMENTS[document_name])


def test_repository_config_directory_loads() -> None:
    # The whole config of the installed system, loaded the way the engine
    # and the deployed service load it. A broken value never reaches a
    # target machine unnoticed again.
    config = load_config(REPOSITORY_CONFIG_DIR)
    assert isinstance(config, Config)
    assert config.engine.notice_timeout > 0


def test_every_repository_section_has_a_config_field() -> None:
    # A section with no Config field is a section no parser reads: the
    # loader rejects it too, so this test names the offender before the run.
    document = _repository_config_document()
    sections = set(Config.__dataclass_fields__)
    unwired = sorted(set(document) - sections)
    assert not unwired, f"config sections with no parser: {unwired}"


def test_every_config_field_has_a_repository_section() -> None:
    # The reverse direction: a field the repository config never fills is
    # either dead or a section someone forgot to ship.
    document = _repository_config_document()
    absent = sorted(set(Config.__dataclass_fields__) - set(document))
    assert not absent, f"Config fields with no config section: {absent}"


def test_every_repository_section_key_is_read_by_its_parser() -> None:
    # A key the parser never reads is silently ignored, which is the exact
    # failure the strictness in the loader and this test exist to prevent.
    config = load_config(REPOSITORY_CONFIG_DIR)
    unread: list[str] = []
    for section_name, table in _top_level_tables(
        _repository_config_document()
    ).items():
        section = getattr(config, section_name)
        for key in sorted(set(table) - _section_field_names(section)):
            unread.append(f"[{section_name}] {key}")
    assert not unread, f"config keys no parser reads: {unread}"


def test_section_fields_are_keys_or_recorded_derived_fields() -> None:
    # A field with no key and no derivation is a value nobody can configure.
    config = load_config(REPOSITORY_CONFIG_DIR)
    underivable: list[str] = []
    for section_name, table in _top_level_tables(
        _repository_config_document()
    ).items():
        section = getattr(config, section_name)
        derived = DERIVED_SECTION_FIELDS.get(section_name, frozenset())
        missing = sorted(_section_field_names(section) - set(table) - derived)
        for name in missing:
            underivable.append(f"[{section_name}] {name}")
    assert not underivable, f"section fields with no config key: {underivable}"


def test_every_repository_config_file_is_not_empty() -> None:
    # The loader joins the directory: an empty file contributes nothing and
    # hides the fact that a section was meant to live there.
    for path in sorted(REPOSITORY_CONFIG_DIR.glob("*.toml")):
        assert tomllib.loads(path.read_text(encoding="utf-8")), (
            f"config file is empty: {path.name}"
        )


@pytest.mark.parametrize("document_name", sorted(TEST_DOCUMENTS))
def test_test_document_sections_match_the_repository_config(
    document_name: str,
) -> None:
    repository = _top_level_tables(_repository_config_document())
    document = _top_level_tables(_document_by_name(document_name))
    assert sorted(document) == sorted(repository)


@pytest.mark.parametrize("document_name", sorted(TEST_DOCUMENTS))
def test_test_document_keys_come_from_the_repository_config(
    document_name: str,
) -> None:
    # A test document must not invent a key: a test-only key would make the
    # suite pass on a config the real loader would reject.
    repository = _top_level_tables(_repository_config_document())
    document = _top_level_tables(_document_by_name(document_name))
    invented: list[str] = []
    for section_name, table in document.items():
        for key in sorted(set(table) - set(repository.get(section_name, {}))):
            invented.append(f"[{section_name}] {key}")
    assert not invented, (
        f"{document_name} keys with no config key: {invented}"
    )


@pytest.mark.parametrize("document_name", sorted(TEST_DOCUMENTS))
def test_test_document_leaves_out_only_recorded_optional_keys(
    document_name: str,
) -> None:
    # Every other key of the real config must be mirrored, so a test cannot
    # silently run on an older shape of the config.
    repository = _top_level_tables(_repository_config_document())
    document = _top_level_tables(_document_by_name(document_name))
    absent: list[str] = []
    for section_name, table in repository.items():
        optional = OPTIONAL_SECTION_KEYS.get(section_name, frozenset())
        missing = set(table) - set(document.get(section_name, {})) - optional
        for key in sorted(missing):
            absent.append(f"[{section_name}] {key}")
    assert not absent, (
        f"config keys missing from {document_name}: {absent}"
    )


def test_test_factory_config_keeps_the_vault_entry_cross_checks() -> None:
    # The factory overrides whole sections, so it can produce a Config the
    # real loader would reject. Every vault entry title a section names must
    # exist in the entries the factory hands over.
    factory = make_config()
    titles = {entry.title for entry in factory.vault_structure.entries}
    referenced = {
        "local_vault_setup.vault_password_entry_title": (
            factory.local_vault_setup.vault_password_entry_title
        ),
        "system_metrics_setup.google_script_key_entry_title": (
            factory.system_metrics_setup.google_script_key_entry_title
        ),
        "port_forwarding_setup.passphrase_entry_title": (
            factory.port_forwarding_setup.passphrase_entry_title
        ),
        "rustdesk_setup.vault_entry_title": (
            factory.rustdesk_setup.vault_entry_title
        ),
        "three_x_ui_xray_setup.vault_entry_title": (
            factory.three_x_ui_xray_setup.vault_entry_title
        ),
        "three_x_ui_xray_setup.connection_vault_entry_title": (
            factory.three_x_ui_xray_setup.connection_vault_entry_title
        ),
    }
    missing = sorted(
        name for name, title in referenced.items() if title not in titles
    )
    assert not missing, f"vault entry titles missing from the factory: {missing}"
