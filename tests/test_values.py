"""Guards of the task values.

The values package is the single place a value is declared, so these guards
keep the package honest: every declared value passes the rule of its own
annotation, the short list of extra rules holds, the names a module tells its
readers to read are exactly the names it declares, and every declared value is
read by some module of the package.

The rule of a value is derived from its annotation, so a new value needs no
line here. A rule that an annotation cannot express, and where a wrong value
would break the machine silently, is written once in EXTRA_VALUE_RULES; a
guard proves every name in that list is a declared value, so a typo cannot
hide there.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import get_type_hints

import pytest
from value_checks import (
    ValueRuleError,
    check_absolute_path,
    check_file_mode,
    check_nonempty_text,
    check_nonempty_text_tuple,
    check_not_negative_int,
    check_real_package_names,
    check_shipped_value,
    check_vault_entry_title,
)

import pyntara
from pyntara.values import cli_tools_heavy_setup as cli_tools_heavy_values
from pyntara.values import cli_tools_lite_setup as cli_tools_lite_values
from pyntara.values import engine as engine_values
from pyntara.values import i2pd_service_setup as i2pd_values
from pyntara.values import port_forwarding_setup as port_forwarding_values
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values import tor_setup as tor_values
from pyntara.values import upnp_forwarding_setup as upnp_forwarding_values

# Every values module of the package, by its name inside pyntara.values.
VALUES_MODULE_NAMES: tuple[str, ...] = (
    "add_extra_repos",
    "chrome_setup",
    "cli_tools_heavy_setup",
    "cli_tools_lite_setup",
    "common",
    "dnsproxy_setup",
    "engine",
    "ffmpeg_setup",
    "hostname",
    "i2pd_service_setup",
    "imagemagick_setup",
    "kde_keyboard_setup",
    "kde_settings",
    "local_vault_setup",
    "nextdns_setup_system_wide",
    "playwright_setup",
    "port_forwarding_setup",
    "rustdesk_setup",
    "scrcpy_setup",
    "sotavpn_setup",
    "ssh_client_setup",
    "ssh_daemon_setup",
    "system_metrics_setup",
    "upnp_forwarding_setup",
    "swapfile_service_install",
    "tasks",
    "telegram_setup",
    "three_x_ui_xray_setup",
    "tor_setup",
    "vault_structure",
    "vocalinux_setup",
    "yggdrasil_service_setup",
    "zram_service",
    "zswap_service",
)

# The name of the list a values module declares next to its values, which
# names the values its task reads.
READ_VALUE_NAMES_ATTRIBUTE = "READ_VALUE_NAMES"

# The rules an annotation cannot express, one line per value: its module, its
# name and the rule. A file mode is a whole number, so the generic pass sees
# only an int, while a mode of zero would leave the file unusable without a
# word; that is the kind of value that belongs here. A package list is a tuple
# of texts for the generic pass, while a virtual package name in it would make
# the task reinstall that package on every run without ever reaching its goal.
EXTRA_VALUE_RULES: tuple[tuple[str, str, Callable[[object, str], object]], ...] = (
    ("cli_tools_heavy_setup", "PACKAGES", check_real_package_names),
    ("common", "EXECUTABLE_FILE_MODE", check_file_mode),
    ("common", "LAUNCHER_FILE_MODE", check_file_mode),
    ("ffmpeg_setup", "WAYRECORD_FILE_MODE", check_file_mode),
    ("i2pd_service_setup", "ADDRESS_FILE_MODE", check_file_mode),
    ("local_vault_setup", "LOCAL_VAULT_FILE_MODE", check_file_mode),
    ("local_vault_setup", "PASS_DIR_MODE", check_file_mode),
    ("local_vault_setup", "PASS_FILE_MODE", check_file_mode),
    ("local_vault_setup", "PASS_FILE_WRITABLE_MODE", check_file_mode),
    ("local_vault_setup", "SECRETS_DIR_MODE", check_file_mode),
    ("local_vault_setup", "VAULT_PASSWORD_ENTRY_TITLE", check_vault_entry_title),
    ("common", "PROFILE_ID_FILE_MODE", check_file_mode),
    ("rustdesk_setup", "ID_FILE_MODE", check_file_mode),
    ("rustdesk_setup", "VAULT_ENTRY_TITLE", check_vault_entry_title),
    ("scrcpy_setup", "FALLBACK_PACKAGES", check_nonempty_text_tuple),
    ("upnp_forwarding_setup", "MAPPING_ATTEMPTS", check_not_negative_int),
    ("port_forwarding_setup", "ASKPASS_HELPER_FILE_MODE", check_file_mode),
    ("port_forwarding_setup", "STATE_FILE_MODE", check_file_mode),
    (
        "port_forwarding_setup",
        "PASSPHRASE_ENTRY_TITLE",
        check_vault_entry_title,
    ),
    ("sotavpn_setup", "KEY_ENTRY_TITLE", check_vault_entry_title),
    ("ssh_client_setup", "DROPIN_FILE_MODE", check_file_mode),
    ("ssh_daemon_setup", "AUTHORIZED_KEYS_FILE_MODE", check_file_mode),
    ("ssh_daemon_setup", "DROPIN_FILE_MODE", check_file_mode),
    ("ssh_daemon_setup", "PRIVATE_KEY_FILE_MODE", check_file_mode),
    ("ssh_daemon_setup", "PUBLIC_KEY_FILE_MODE", check_file_mode),
    ("ssh_daemon_setup", "SSH_DIR_MODE", check_file_mode),
    ("swapfile_service_install", "SWAPFILE_MODE", check_file_mode),
    ("system_metrics_setup", "COMMAND_FILE_MODE", check_file_mode),
    ("system_metrics_setup", "QUEUE_FILE_MODE", check_file_mode),
    ("system_metrics_setup", "SPOOL_DIR_MODE", check_file_mode),
    ("system_metrics_setup", "SYSTEM_METRICS_DIR_MODE", check_file_mode),
    ("system_metrics_setup", "VAULT_BACKUP_FILE_MODE", check_file_mode),
    ("telegram_setup", "ICON_FILE_MODE", check_file_mode),
    ("three_x_ui_xray_setup", "CERT_FULLCHAIN_FILE_MODE", check_file_mode),
    ("three_x_ui_xray_setup", "CERT_PRIVKEY_FILE_MODE", check_file_mode),
    ("tor_setup", "ADDRESS_FILE_MODE", check_file_mode),
    ("tor_setup", "DROPIN_FILE_MODE", check_file_mode),
    ("tor_setup", "HIDDEN_SERVICE_DIR_MODE", check_file_mode),
    ("yggdrasil_service_setup", "ADDRESS_FILE_MODE", check_file_mode),
    ("yggdrasil_service_setup", "CONFIG_FILE_MODE", check_file_mode),
    (
        "yggdrasil_service_setup",
        "NM_UNMANAGED_CONF_FILE_MODE",
        check_file_mode,
    ),
    ("yggdrasil_service_setup", "PRIVATE_KEY_FILE_MODE", check_file_mode),
    ("zram_service", "HOT_ADD_READABLE_MODE_BIT", check_file_mode),
)

# Values the rule of their annotation refuses while the shipped value is
# legitimate: the one place a rule is overridden for a single value, and every
# entry is a decision with a reason written beside it (point 56 of the plan).
# A guard proves each entry names a declared value that the generic pass really
# refuses, so an exemption that has become unnecessary fails the suite.
EXEMPT_VALUES: tuple[tuple[str, str], ...] = (
    # The separator between the proquint words of the RustDesk password is one
    # space, so the text carries no letter on purpose.
    ("rustdesk_setup", "PASSWORD_SEPARATOR"),
    # The line separator of the rendered yggdrasil config is one newline: the
    # value is the whole text, so stripping it leaves nothing while the
    # declaration is exactly the character the renderer writes.
    ("yggdrasil_service_setup", "LINE_SEPARATOR"),
    # static_peers is empty on purpose: a node starts with no hardcoded peer
    # and takes its peers from the downloaded public list, and the machine
    # settings of a target system may fill the tuple in.
    ("yggdrasil_service_setup", "STATIC_PEERS"),
)


def _source_root() -> Path:
    """The directory of the package sources the guards read."""

    package_file = pyntara.__file__
    assert package_file is not None
    return Path(package_file).resolve().parent


# Directories outside the package that read values too: the maintenance scripts
# of the repository, which import a values module and are not part of the wheel.
EXTRA_SOURCE_DIRECTORIES: tuple[str, ...] = ("secrets",)


def _scanned_source_paths() -> list[Path]:
    """Every source file whose reads of values count, package and scripts."""

    paths = list(_source_root().rglob("*.py"))
    repository_root = _source_root().parents[1]
    for directory_name in EXTRA_SOURCE_DIRECTORIES:
        directory = repository_root / directory_name
        if directory.is_dir():
            paths.extend(directory.rglob("*.py"))
    return sorted(path for path in paths if "__pycache__" not in path.parts)


def _values_module(module_name: str) -> object:
    """The values module of the package, imported by name."""

    return __import__(f"pyntara.values.{module_name}", fromlist=["*"])


def _declared_value_names(module_name: str) -> set[str]:
    """The names a values module declares by assignment, its list excluded.

    The names are read from the module source rather than from the module
    object, so a name the module imports, such as Path, is not mistaken for a
    value.
    """

    source = (_source_root() / "values" / f"{module_name}.py").read_text(
        encoding="utf-8"
    )
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    names.discard(READ_VALUE_NAMES_ATTRIBUTE)
    return names


def _read_names_by_attribute() -> dict[str, set[str]]:
    """Names read as attributes of a values module, per module.

    Every scanned source file is parsed: a module that imports a values module
    under an alias is followed, so a read through an alias counts and a read
    that never happens leaves the name unread. The scan covers the package and
    the maintenance scripts under secrets/, because a value read only there is
    not dead.
    """

    reads: dict[str, set[str]] = {name: set() for name in VALUES_MODULE_NAMES}
    for path in _scanned_source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pyntara.values":
                for imported in node.names:
                    if imported.name in VALUES_MODULE_NAMES:
                        aliases[imported.asname or imported.name] = imported.name
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            ):
                reads[aliases[node.value.id]].add(node.attr)
    return reads


def test_every_shipped_value_passes_the_rule_of_its_annotation() -> None:
    # The values that ship are the values the machine runs, and no task test
    # exercises them: a task test points the values at its fixture tree. This
    # pass is what looks at the shipped values, and it reads the annotation of
    # each of them instead of a list somebody must remember to extend.
    for module_name in VALUES_MODULE_NAMES:
        module = _values_module(module_name)
        for name, annotation in sorted(get_type_hints(module).items()):
            if name == READ_VALUE_NAMES_ATTRIBUTE:
                continue
            if (module_name, name) in EXEMPT_VALUES:
                continue
            check_shipped_value(
                getattr(module, name), annotation, f"{module_name}.{name}"
            )


def test_every_exempt_value_is_declared_and_otherwise_refused() -> None:
    # An exemption that names nothing would look like protection, and one whose
    # value the generic pass now accepts is a leftover: both fail here.
    for module_name, name in EXEMPT_VALUES:
        assert name in _declared_value_names(module_name), (
            f"exemption naming no declared value: {module_name}.{name}"
        )
        module = _values_module(module_name)
        annotation = get_type_hints(module)[name]
        with pytest.raises(ValueRuleError):
            check_shipped_value(
                getattr(module, name), annotation, f"{module_name}.{name}"
            )


def test_every_extra_rule_passes_on_its_shipped_value() -> None:
    for module_name, name, rule in EXTRA_VALUE_RULES:
        module = _values_module(module_name)
        assert rule(getattr(module, name), f"{module_name}.{name}") == getattr(
            module, name
        )


def test_every_extra_rule_names_a_declared_value() -> None:
    # A rule for a name that no module declares would never run and would look
    # like protection, so the suite refuses it.
    unknown: list[str] = []
    for module_name, name, _ in EXTRA_VALUE_RULES:
        if name not in _declared_value_names(module_name):
            unknown.append(f"{module_name}.{name}")
    assert not unknown, f"extra rules naming no declared value: {unknown}"


@pytest.mark.parametrize("value", ["", "   ", None, 42])
def test_the_generic_rule_refuses_a_text_with_nothing_in_it(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, str, "section.NAME")


@pytest.mark.parametrize("value", [-1, 1.5, "3", True, None])
def test_the_generic_rule_refuses_a_negative_whole_number(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, int, "section.NAME")


@pytest.mark.parametrize(
    "value", ["relative/path", "etc/hostname", "", None, 42, Path("etc/hostname")]
)
def test_the_generic_rule_refuses_a_path_that_is_not_absolute(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, Path, "section.NAME")


@pytest.mark.parametrize(
    "value", [(), [], "hostnamectl", ("hostnamectl", ""), ("hostnamectl", 1), None]
)
def test_the_generic_rule_refuses_a_tuple_that_is_no_command(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, tuple[str, ...], "section.NAME")


def test_the_generic_rule_leaves_an_annotation_it_does_not_read_alone() -> None:
    # A float, a bool or a record type is not judged by the generic pass: the
    # second layer takes such a value when a wrong one would be silent.
    check_shipped_value(-5.0, float, "section.NAME")
    check_shipped_value(False, bool, "section.NAME")


@pytest.mark.parametrize("value", [0, -1, 0o10000, 493.0, "0755", True, None])
def test_the_file_mode_rule_refuses_a_value_that_is_no_mode(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_file_mode(value, "section.NAME")


@pytest.mark.parametrize("value", [42, "", "   ", None, ("a",)])
def test_the_nonempty_text_rule_refuses_a_text_with_nothing_in_it(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_nonempty_text(value, "section.NAME")


@pytest.mark.parametrize("value", ["relative/path", "etc/hostname", "", None, 42])
def test_the_absolute_path_rule_refuses_a_path_that_is_not_absolute(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_absolute_path(value, "section.NAME")


@pytest.mark.parametrize("value", [-1, 4.0, "4", True, None])
def test_the_whole_number_rule_refuses_a_count_below_zero(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_not_negative_int(value, "section.NAME")


def test_the_whole_number_rule_allows_zero() -> None:
    # Zero is a value like any other: it means no attempt, no wait or no
    # permission to spare, and the tool that reads it reports itself.
    assert check_not_negative_int(0, "section.NAME") == 0


@pytest.mark.parametrize(
    "value",
    [(), [], "hostnamectl", ("hostnamectl", ""), ("hostnamectl", 1), None],
)
def test_the_text_tuple_rule_refuses_a_value_that_is_no_command(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_nonempty_text_tuple(value, "section.NAME")


def test_the_real_package_rule_refuses_a_virtual_package_name() -> None:
    # A virtual name in the list is invisible to dpkg-query, so the package
    # would be installed on every run and never reach the installed state.
    with pytest.raises(ValueRuleError):
        check_real_package_names(
            ("mc", "exiftool"), "cli_tools_heavy_setup.PACKAGES"
        )
    with pytest.raises(ValueRuleError):
        check_real_package_names(("mc",), "cli_tools_heavy_setup.PACKAGES")
    assert check_real_package_names(
        ("mc", "libimage-exiftool-perl"), "cli_tools_heavy_setup.PACKAGES"
    ) == ("mc", "libimage-exiftool-perl")


def test_the_vault_entry_title_rule_refuses_a_title_the_structure_lacks() -> None:
    # The runtime vault carries the password under this title: a title no entry
    # of the structure holds would leave the vault password unreadable while
    # the task looked finished, which is the silent failure the rule refuses.
    with pytest.raises(ValueRuleError):
        check_vault_entry_title("no_such_entry", "local_vault_setup.TITLE")
    assert (
        check_vault_entry_title(
            "pyntara_local_vault_password", "local_vault_setup.TITLE"
        )
        == "pyntara_local_vault_password"
    )


def test_the_read_list_names_exactly_the_declared_values() -> None:
    # The list a task reads is what the task reports when a value is missing,
    # so a value left out of the list would be read without ever being
    # reported, and a name in the list that nothing declares would turn into a
    # permanent warning.
    for module_name in VALUES_MODULE_NAMES:
        module = _values_module(module_name)
        listed = set(getattr(module, READ_VALUE_NAMES_ATTRIBUTE))
        assert listed == _declared_value_names(module_name), module_name


def test_every_declared_value_is_read_somewhere() -> None:
    # A value nobody reads is dead: it names a machine setting the run never
    # uses, and it would stay invisible until someone trusted it.
    reads = _read_names_by_attribute()
    unread: list[str] = []
    for module_name in VALUES_MODULE_NAMES:
        for name in sorted(_declared_value_names(module_name) - reads[module_name]):
            unread.append(f"{module_name}.{name}")
    assert not unread, f"values no module reads: {unread}"


def test_the_declared_byte_factors_agree() -> None:
    # Two factors that describe the same machine constant must not drift: a
    # mebibyte that is not 1024 kibibytes would size every swapfile and every
    # zram device from a number no kernel uses.
    assert engine_values.BYTES_PER_MIB == engine_values.BYTES_PER_KIB**2


def test_the_two_cli_package_lists_hold_the_toolset_once() -> None:
    # One console toolset is split into two sections, so a package in both
    # lists would be installed twice per run and a package that vanished from
    # both would silently stop being installed. The two counts are the measured
    # partition of the toolset, so a change is a deliberate act: a package
    # moved between the sections or left the set.
    lite = cli_tools_lite_values.PACKAGES
    heavy = cli_tools_heavy_values.PACKAGES
    assert not set(lite) & set(heavy)
    assert (len(lite), len(heavy)) == (55, 23)


def test_the_parallel_marker_is_part_of_the_parallel_write_out() -> None:
    # The marker is what the collector splits a merged answer text with, and
    # the text curl prints is what the marker must appear in: a marker that is
    # no longer in the text would break the attribution of every answer
    # silently, on the target machine only.
    assert (
        engine_values.CURL_PARALLEL_SOURCE_MARKER
        in engine_values.CURL_PARALLEL_WRITE_OUT
    )


def test_the_askpass_env_carries_the_helper_placeholder() -> None:
    # The unlock hands the helper path to ssh through this map, so the
    # placeholder is what makes the helper findable; a map without it
    # would start ssh-add against a path that does not exist.
    askpass_env = port_forwarding_values.ASKPASS_ENV
    assert askpass_env
    assert any("{helper_path}" in value for value in askpass_env.values())


def test_the_askpass_helper_prints_the_declared_passphrase_variable() -> None:
    # The helper is a script of its own: it must name the variable the
    # unlock sets, otherwise ssh-add receives an empty passphrase and the
    # key never loads on the target machine.
    name = port_forwarding_values.PASSPHRASE_ENV_KEY
    content = port_forwarding_values.ASKPASS_HELPER_CONTENT
    assert name in content


def test_the_two_scope_names_of_the_router_report_differ() -> None:
    # The report says how far a forwarded address reaches, and it does so
    # by naming the scope: two scope values that read the same would hide
    # the difference between a global address and one behind a provider
    # NAT, which is the sentence a reader of the report acts on.
    assert (
        upnp_forwarding_values.GLOBAL_SCOPE_NAME
        != upnp_forwarding_values.NAT_SCOPE_NAME
    )


def test_the_mapping_description_carries_the_hostname_placeholder() -> None:
    # The mark of a rule names the machine that owns it, so the placeholder
    # is what keeps two machines of one project on one router apart; a
    # template without it would let one machine read the rule of the other
    # as its own and never write its own rule.
    assert "{hostname}" in upnp_forwarding_values.UPNP_MAPPING_DESCRIPTION


def test_every_flag_family_has_a_report_word() -> None:
    # The report names the family of every record with the declared words, and
    # it does so without a fallback: a family of the flag mapping that no word
    # names raises on the target machine while the report is printed.
    assert set(engine_values.REPORT_FAMILY_WORDS) >= set(
        engine_values.ADDRESS_FAMILY_BY_FLAG.values()
    )


def test_the_i2pd_log_level_is_one_the_router_accepts() -> None:
    # i2pd refuses to start on a level outside its own vocabulary, and the
    # rendered configuration carries the value as it stands: a wrong spelling
    # would leave the machine with a router that never comes up while the task
    # reported a written configuration.
    assert i2pd_values.LOG_LEVEL in ("debug", "info", "warn", "error", "none")


def test_the_i2pd_asset_name_templates_carry_the_release_tag() -> None:
    # The name of the downloaded package is built from the template, and a
    # template without the release tag would ask the release API for the same
    # file name on every release, so the task would install the package of
    # another version or nothing at all.
    for template in (
        i2pd_values.CODENAME_ASSET_NAME_TEMPLATE,
        i2pd_values.GENERIC_ASSET_NAME_TEMPLATE,
    ):
        assert "{release_tag}" in template
        assert template.endswith(".deb")


def test_the_tor_log_level_is_one_the_daemon_accepts() -> None:
    # Tor refuses to start on a level outside its own vocabulary, and the
    # drop-in carries the value as it stands: a wrong spelling would leave the
    # machine with a daemon that never comes up while the task reported a
    # written configuration.
    assert tor_values.LOG_LEVEL in ("debug", "info", "notice", "warn", "err")


def test_the_tor_drop_in_lives_beside_the_main_configuration() -> None:
    # The include line of the main configuration is built from the drop-in
    # path, so the directive can never point at another file; and the file
    # must sit directly in the tor configuration directory, because the
    # AppArmor profile of the package allows reading /etc/tor/* but not its
    # subdirectories.
    assert tor_values.TORRC_DROPIN_PATH.parent == tor_values.TORRC_PATH.parent
    include_line = f"{tor_values.INCLUDE_DIRECTIVE} {tor_values.TORRC_DROPIN_PATH}"
    assert str(tor_values.TORRC_DROPIN_PATH) in include_line


def test_the_ssh_directives_carry_the_port_directive() -> None:
    # The port of the daemon is what every forward of this machine targets,
    # and the reader finds it by PORT_DIRECTIVE: a directive list without
    # that keyword would make every tunnel of the machine fail loudly, and
    # a non-numeric value would be forwarded as a port nothing listens on.
    names = {directive.name.casefold() for directive in ssh_daemon_values.DIRECTIVES}
    assert ssh_daemon_values.PORT_DIRECTIVE.casefold() in names
    port_values = [
        directive.value
        for directive in ssh_daemon_values.DIRECTIVES
        if directive.name.casefold() == ssh_daemon_values.PORT_DIRECTIVE.casefold()
    ]
    assert len(port_values) == 1
    assert port_values[0].isdigit()


def test_the_ssh_users_are_unique_and_never_empty() -> None:
    # The task walks USERS to deploy the keys; a repeated name would write
    # the same directory twice, and an empty name would make the run look
    # for a home directory that no user owns.
    assert ssh_daemon_values.USERS
    assert len(set(ssh_daemon_values.USERS)) == len(ssh_daemon_values.USERS)
    assert all(user.strip() for user in ssh_daemon_values.USERS)
