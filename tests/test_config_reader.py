"""The runtime config reader never fails.

The engine reads the config on the target machine, where nobody is watching
it: a missing file, broken TOML, an unknown section, an unknown key, an
absent value or a value of the wrong type must all leave the run going. No
rule of the config is checked here, because no rule runs in production: the
rules live in tests/config_checks.py and the coverage guard applies them to
the shipped config/ directory during development (architecture contract,
Configuration).
"""

from __future__ import annotations

from pathlib import Path

from pyntara.config import (
    Config,
    absent_config_keys,
    describe_absent_config_keys,
    load_config,
)


def _write(tmp_path: Path, content: str) -> Path:
    """Write content as config.toml in tmp_path and return its path."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_missing_file_returns_a_config_with_absent_values(
    tmp_path: Path,
) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert isinstance(config, Config)
    assert config.hostname.hostname_random_bytes is None
    assert config.hostname.hostname_file is None


def test_empty_directory_returns_a_config_with_absent_values(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = load_config(config_dir)
    assert config.hostname.hostname_random_bytes is None


def test_broken_toml_is_read_as_a_document_without_values(
    tmp_path: Path,
) -> None:
    config = load_config(_write(tmp_path, "[hostname\nhostname_random_bytes = 4\n"))
    assert config.hostname.hostname_random_bytes is None


def test_unknown_section_and_key_are_ignored(tmp_path: Path) -> None:
    # A key nobody reads and a section nobody knows are not errors: the run
    # keeps the values it understands and continues.
    config = load_config(
        _write(
            tmp_path,
            '[hostname]\nhostname_random_bytes = 4\nunknown_key = "x"\n'
            '[unknown_section]\nmessage = "hello"\n',
        )
    )
    assert config.hostname.hostname_random_bytes == 4


def test_absent_key_leaves_its_value_absent(tmp_path: Path) -> None:
    # The config is the only source of values: a key that is not there is a
    # value that is not there, never an invented one.
    config = load_config(_write(tmp_path, "[hostname]\nhostname_random_bytes = 4\n"))
    assert config.hostname.hostname_random_bytes == 4
    assert config.hostname.hostname_file is None


def test_absent_section_keeps_its_object_with_absent_values(
    tmp_path: Path,
) -> None:
    # The section object always exists, so reading through it cannot raise an
    # attribute error; only the values are absent.
    config = load_config(_write(tmp_path, "[hostname]\nhostname_random_bytes = 4\n"))
    assert config.hostname.hostname_file is None


def test_value_of_an_unexpected_type_is_handed_over_as_it_is(
    tmp_path: Path,
) -> None:
    # A wrong type is not repaired and not reported here: the task that
    # needed the value reports what it could not do, and the run continues.
    config = load_config(
        _write(tmp_path, '[hostname]\nhostname_random_bytes = "four"\n')
    )
    assert config.hostname.hostname_random_bytes == "four"


def test_values_keep_the_shape_their_field_declares(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            '[hostname]\nhostname_file = "/etc/hostname"\n'
            'set_hostname_command = ["hostnamectl", "set-hostname"]\n'
            '[add_extra_repos]\ncomponents = ["universe", "multiverse"]\n',
        )
    )
    assert config.hostname.hostname_file == "/etc/hostname"
    assert config.hostname.set_hostname_command == (
        "hostnamectl",
        "set-hostname",
    )
    assert config.add_extra_repos.components == ("universe", "multiverse")


def test_absent_config_keys_names_the_values_a_section_does_not_hold(
    tmp_path: Path,
) -> None:
    # The deployed services name the keys they cannot find, so the journal of
    # a machine shows config keys and not a Python error.
    config = load_config(_write(tmp_path, "[hostname]\nhostname_random_bytes = 4\n"))
    assert absent_config_keys(config.hostname, ("hostname_random_bytes",)) == ()
    assert absent_config_keys(
        config.hostname, ("hostname_random_bytes", "hostname_file")
    ) == ("hostname_file",)


def test_absent_config_keys_ignores_a_key_of_a_wrong_type(tmp_path: Path) -> None:
    # A value of a wrong type is a value the document holds: naming it as
    # absent would be a lie, and no rule is applied here.
    config = load_config(
        _write(tmp_path, '[hostname]\nhostname_random_bytes = "four"\n')
    )
    assert absent_config_keys(config.hostname, ("hostname_random_bytes",)) == ()


def test_absent_config_keys_ignores_an_empty_array(tmp_path: Path) -> None:
    # An array the document does not have cannot be told from an empty array,
    # so an empty one is not reported as absent.
    config = load_config(_write(tmp_path, "[add_extra_repos]\ncomponents = []\n"))
    assert config.add_extra_repos.components == ()
    assert absent_config_keys(config.add_extra_repos, ("components",)) == ()


def test_absent_config_keys_ignores_a_missing_section(tmp_path: Path) -> None:
    # A section that is not in the document keeps an object with absent
    # values, so a service reads a section the same way in every case.
    config = load_config(_write(tmp_path, "[engine]\nnotice_timeout = 7\n"))
    assert absent_config_keys(
        config.hostname, ("hostname_random_bytes",)
    ) == ("hostname_random_bytes",)


def test_describe_absent_config_keys_names_each_table_once(
    tmp_path: Path,
) -> None:
    # The clause names the table and then the keys, so a long list stays
    # readable, and a table without absent keys is left out.
    config = load_config(_write(tmp_path, "[engine]\nnotice_timeout = 7\n"))
    described = describe_absent_config_keys(
        (
            (
                "hostname",
                absent_config_keys(
                    config.hostname, ("hostname_random_bytes", "hostname_path")
                ),
            ),
            (
                "hostname.ssh",
                absent_config_keys(config.hostname, ()),
            ),
        )
    )
    assert described == "[hostname] has no hostname_random_bytes, hostname_path"


def test_describe_absent_config_keys_says_nothing_when_all_keys_are_there(
    tmp_path: Path,
) -> None:
    config = load_config(_write(tmp_path, "[hostname]\nhostname_random_bytes = 4\n"))
    assert (
        describe_absent_config_keys(
            (
                (
                    "hostname",
                    absent_config_keys(config.hostname, ("hostname_random_bytes",)),
                ),
            )
        )
        == ""
    )
