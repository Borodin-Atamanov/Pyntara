"""Config tests for [vault_structure] and [local_vault_setup]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # local_vault_setup source_vault_production is a number, not a string
        base_config().replace(
            'source_vault_production = "secrets/production.vault"',
            "source_vault_production = 1",
        ),
        # local_vault_setup source_vault_default is an empty string
        base_config().replace(
            'source_vault_default = "secrets/default.vault"', 'source_vault_default = ""'
        ),
        # local_vault_setup local_vault_path is a number, not a string
        base_config().replace(
            'local_vault_path = "/var/lib/pyntara/secrets/pyntara.vault"',
            "local_vault_path = 1",
        ),
        # local_vault_setup pass_file_path is an empty string
        base_config().replace('pass_file_path = "/etc/pyntara/pass"', 'pass_file_path = ""'),
        # local_vault_setup vault_password_entry_title is a number, not a string
        base_config().replace(
            'vault_password_entry_title = "pyntara_local_vault_password"',
            "vault_password_entry_title = 1",
        ),
        # vault_structure entries is a string, not an array
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            '[vault_structure]\nentries = "salt"',
        ),
        # vault_structure entries is an empty array
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            "[vault_structure]\nentries = []",
        ),
        # vault_structure entries are missing entirely: the section is gone
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]\n"
            'title = "password_salt"\nnotes = "Primary salt."\n'
            "[[vault_structure.entries]]\n"
            'title = "pyntara_local_vault_password"\n'
            'notes = "Local vault password."\n',
            "",
        ),
        # vault_structure entry title is a number, not a string
        base_config().replace('title = "password_salt"', "title = 1"),
        # vault_structure entry title is an empty string
        base_config().replace('title = "password_salt"', 'title = ""'),
        # vault_structure entry notes is missing
        base_config().replace('notes = "Primary salt."', ""),
        # vault_structure entry notes is an empty string
        base_config().replace('notes = "Primary salt."', 'notes = ""'),
        # vault_structure entry titles are duplicated
        base_config().replace(
            'title = "pyntara_local_vault_password"', 'title = "password_salt"'
        ),
        # vault_structure entry names an unknown field; url lives in the vault
        base_config().replace(
            'notes = "Primary salt."',
            'notes = "Primary salt."\nurl = "https://example.com/exec"',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_entry_title_must_exist_in_vault_structure(tmp_path: Path) -> None:
    # The local vault password entry must be part of the vault structure: a
    # typo in the title is caught at config load, not on the target machine.
    assert_config_error(
        tmp_path,
        base_config().replace(
            'vault_password_entry_title = "pyntara_local_vault_password"',
            'vault_password_entry_title = "no_such_entry"',
        ),
        match="must name an entry",
    )


def test_load_config_google_script_entry_title_must_exist_in_vault_structure(
    tmp_path: Path,
) -> None:
    # The Google script entry title must be part of the vault structure:
    # a typo is caught at config load, not on the target machine.
    assert_config_error(
        tmp_path,
        base_config().replace(
            'google_script_key_entry_title = "google_script_key"',
            'google_script_key_entry_title = "no_such_entry"',
        ),
        match="must name an entry",
    )


def test_load_config_vault_entry_reachable_in_loaded_config(tmp_path: Path) -> None:
    # The vault structure parses into typed entries; the base config has
    # six entries including the cross-checked titles.
    config = load_config(write_config(tmp_path, base_config()))
    assert [entry.title for entry in config.vault_structure.entries] == [
        "password_salt",
        "pyntara_local_vault_password",
        "google_script_key",
        "three_x_ui_credentials",
        "ssh_passphase_for_port_forwarding",
        "rustdesk_password",
    ]
    # The groups array is optional: the base config has none.
    assert config.vault_structure.groups == ()


def test_load_config_generated_password_parses(tmp_path: Path) -> None:
    # The optional generated_password field of an entry is carried into the
    # typed entry; the base config sets proquint-7 for the passphrase entry.
    config = load_config(write_config(tmp_path, base_config()))
    entry = next(
        e
        for e in config.vault_structure.entries
        if e.title == "ssh_passphase_for_port_forwarding"
    )
    assert entry.generated_password == "proquint-7"


@pytest.mark.parametrize(
    "content",
    [
        # generated_password is a number, not a string
        base_config().replace(
            'generated_password = "proquint-7"', "generated_password = 7"
        ),
        # generated_password has an invalid word count
        base_config().replace(
            'generated_password = "proquint-7"', 'generated_password = "proquint-0"'
        ),
        # generated_password has an invalid spec
        base_config().replace(
            'generated_password = "proquint-7"', 'generated_password = "dice-7"'
        ),
    ],
)
def test_load_config_generated_password_wrong_types_raise(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content)


@pytest.mark.parametrize(
    "content",
    [
        # vault_structure groups is a string, not an array
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            "[vault_structure]\ngroups = \"NextDNS\"\n[[vault_structure.entries]]",
        ),
        # vault_structure group title is a number, not a string
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            "[vault_structure]\n[[vault_structure.groups]]\ntitle = 1\nnotes = \"x\"\n[[vault_structure.entries]]",
        ),
        # vault_structure group notes is missing
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            "[vault_structure]\n[[vault_structure.groups]]\ntitle = \"NextDNS\"\n[[vault_structure.entries]]",
        ),
        # vault_structure group title collides with an entry title
        base_config().replace(
            "[vault_structure]\n[[vault_structure.entries]]",
            "[vault_structure]\n[[vault_structure.groups]]\ntitle = \"password_salt\"\nnotes = \"x\"\n[[vault_structure.entries]]",
        ),
    ],
)
def test_load_config_vault_groups_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_vault_groups_parse(tmp_path: Path) -> None:
    # A configured group is parsed into the typed groups tuple.
    content = base_config().replace(
        "[vault_structure]\n[[vault_structure.entries]]",
        "[vault_structure]\n[[vault_structure.groups]]\ntitle = \"NextDNS\"\nnotes = \"Profile accounts.\"\n[[vault_structure.entries]]",
    )
    config = load_config(write_config(tmp_path, content))
    assert len(config.vault_structure.groups) == 1
    assert config.vault_structure.groups[0].title == "NextDNS"
    assert config.vault_structure.groups[0].notes == "Profile accounts."


def test_load_config_vault_group_seed_entries_parse(tmp_path: Path) -> None:
    # A configured group with seed_entries parses them into the typed
    # group, so the regeneration tooling can mirror the structure.
    content = base_config().replace(
        "[vault_structure]\n[[vault_structure.entries]]",
        "[vault_structure]\n[[vault_structure.groups]]\ntitle = \"port_forwarding_servers\"\n"
        'notes = "Server addresses."\n'
        '[[vault_structure.groups.seed_entries]]\n'
        'title = "Server 001"\nurl = "200:a804:881c:d5d8:6d4e:afab:e158:371"\n'
        'notes = "Test address."\n'
        "[[vault_structure.entries]]",
    )
    config = load_config(write_config(tmp_path, content))
    group = config.vault_structure.groups[0]
    assert group.title == "port_forwarding_servers"
    assert group.notes == "Server addresses."
    assert len(group.seed_entries) == 1
    seed = group.seed_entries[0]
    assert seed.title == "Server 001"
    assert seed.url == "200:a804:881c:d5d8:6d4e:afab:e158:371"
    assert seed.notes == "Test address."


@pytest.mark.parametrize(
    "seed_block",
    [
        # seed_entries is a string, not an array
        'seed_entries = "Server 001"\n',
        # seed entry is a string, not a table
        'seed_entries = ["Server 001"]\n',
        # seed entry title is missing
        (
            "[[vault_structure.groups.seed_entries]]\n"
            'url = "200:a804:881c:d5d8:6d4e:afab:e158:371"\n'
        ),
        # seed entry title is an empty string
        '[[vault_structure.groups.seed_entries]]\ntitle = ""\n',
        # seed entry url is a number, not a string
        '[[vault_structure.groups.seed_entries]]\ntitle = "Server 001"\nurl = 7\n',
        # seed entry names an unknown field
        '[[vault_structure.groups.seed_entries]]\ntitle = "Server 001"\npassword = "x"\n',
        # duplicate seed entry titles
        (
            '[[vault_structure.groups.seed_entries]]\ntitle = "Server 001"\n'
            '[[vault_structure.groups.seed_entries]]\ntitle = "Server 001"\n'
        ),
    ],
)
def test_load_config_vault_group_seed_entries_wrong_types_raise(
    tmp_path: Path, seed_block: str
) -> None:
    content = base_config().replace(
        "[vault_structure]\n[[vault_structure.entries]]",
        '[vault_structure]\n[[vault_structure.groups]]\ntitle = "port_forwarding_servers"\n'
        'notes = "Server addresses."\n' + seed_block + "[[vault_structure.entries]]",
    )
    assert_config_error(tmp_path, content)
