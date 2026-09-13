"""Guard: a config value is not written in a module under src/.

Every value the run uses lives in config/ unless its type is one of the
Exceptions of docs/spec/config-content.md (kernel and device paths,
regular expressions, the wording of a message, a file body longer than
five lines and the numbers that encode a protocol). This guard reads the
sources of the package and refuses a value of a listed type written in
code, so the rule is checked by the suite instead of by attention.

The guard recognises four shapes that a value takes in code:

A module level constant of a value, the name in capitals, which is the
shape the audit of the migration calls a module level constant
(docs/TODO.md, Values outside config/ are not classified by a written
rule).
A command argv written as a list literal whose first element is a program
name, which is an unconfigured call of an external tool.
An absolute path literal outside the kernel and device prefixes.
A regular expression literal that two or more modules share, because a
value of an exception type is written once and imported, never copied.

Each shape has an allowlist of what is known to be left: the entries
documented as spec exceptions, and the entries still waiting for their
migration block. The allowlists are compared exactly, so a new value in
code fails the suite and a migrated value leaves a stale entry that must
be removed in the same commit. The guard therefore shrinks: each
migration block deletes its entries here.

The package src/pyntara/config/ is left out: it is the config layer
itself, and the values there are the declarations the guard protects.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "pyntara"
CONFIG_LAYER = PACKAGE_ROOT / "config"

VALUE_CONSTANT_DEFINITION = re.compile(r"^([A-Z][A-Z0-9_]*)\s+=\s+\S")
COMMAND_ARGV_LITERAL = re.compile(r'\[\s*"[a-z0-9][a-z0-9._+-]*"\s*,')
ABSOLUTE_PATH_LITERAL = re.compile(r'"(/[a-z][^"]*)"')
COMPILED_PATTERN = re.compile(r"re\.compile\(\s*r?([\"'])(.*?)\1", re.DOTALL)
KERNEL_PATH_PREFIXES = ("/proc", "/sys", "/dev")

# Module level constants of a value. The entries below are the exceptions
# of docs/spec/config-content.md and the few values that still wait for
# their migration block.
#
# Exceptions: a regular expression (the node pattern of the augeas calls,
# the profile identifier of the dnsproxy, the release and address patterns
# of the setup tasks), the wording of a message (the note an address lookup
# leaves when no service answers), the numbers of an encoding (the proquint
# alphabet, the identity size and the certificate type of an i2pd key) and
# the kernel paths of the swapfile and the zram modules. The location of
# the code itself, the config path and the repository root of the entry
# point, is the layout of the installation and stays with it.
#
# Pending: the environment of the noninteractive apt, the key names the
# three metrics modules read from the component config, and the version
# pattern the setup tasks copy. Each leaves with the block of its task.
VALUE_CONSTANTS_ALLOWED: dict[str, frozenset[str]] = {
    "src/pyntara/augeas.py": frozenset(
        {
            'AUGTOOL_VALUE_RE = re.compile(r\'^(?P<node>.+) = "(?P<value>.*)"$\')',
        }
    ),
    "src/pyntara/i2pd.py": frozenset(
        {
            "I2PD_CERTIFICATE_TYPE_KEY = 5",
            "I2PD_IDENTITY_SIZE = 387",
        }
    ),
    "src/pyntara/i2pd_address.py": frozenset(
        {
            'FALLBACK_NOTE = "address read from the saved file, the keys file is missing or broken"',
        }
    ),
    "src/pyntara/metrics.py": frozenset({"SERVICE_CONFIG_KEYS = ("}),
    "src/pyntara/metrics_collect.py": frozenset(
        {
            'COLLECTOR_SECTION_KEYS = ("commit_command", "command_path", "error_priority")',
            "COLLECTOR_TABLE_KEYS = (",
        }
    ),
    "src/pyntara/metrics_ingest.py": frozenset({"INGEST_CONFIG_KEYS = ("}),
    "src/pyntara/nextdns.py": frozenset({'PROFILE_ID_RE = re.compile(r"^[0-9a-f]{6}$")'}),
    "src/pyntara/port_forwarding.py": frozenset(
        {
            'ALLOCATED_RE = re.compile(r"Allocated port (\\d+) for remote forward")',
            'FAILED_RE = re.compile(r"remote port forwarding failed for listen port")',
            'SUCCESS_RE = re.compile(r"remote forward success for: listen (\\d+)")',
        }
    ),
    "src/pyntara/public_address_report.py": frozenset(
        {
            'NO_ANSWER_REASON = "no echo service reported an address of this family"',
        }
    ),
    "src/pyntara/pyntara.py": frozenset(
        {
            # The composition root names where the code itself lives: the
            # directory of the shipped configuration and the root of the
            # clone are the location of the running code, not a value of
            # the machine, so they stay here as exceptions.
            'CONFIG_PATH = Path("config")',
            "REPO_ROOT = Path(__file__).resolve().parents[1]",
        }
    ),
    "src/pyntara/tasks/chrome_setup.py": frozenset(
        {'FLAG_PLACEHOLDER_PATTERN = re.compile(r"\\{([a-z_]+)\\}")'}
    ),
    "src/pyntara/tasks/dnsproxy_setup.py": frozenset(
        {
            'PROFILE_ID_PATTERN = re.compile(r"[0-9a-f]{6}\\Z")',
            'VERSION_PATTERN = re.compile(r"v?(\\d+\\.\\d+\\.\\d+)")',
        }
    ),
    "src/pyntara/tasks/i2pd_service_setup.py": frozenset(
        {'VERSION_PATTERN = re.compile(r"(\\d+\\.\\d+\\.\\d+)")'}
    ),
    "src/pyntara/tasks/rustdesk_setup.py": frozenset(
        {
            'VERSION_PATTERN = re.compile(r"(\\d+\\.\\d+(?:\\.\\d+)?)")',
        }
    ),
    "src/pyntara/tasks/swapfile_service_install.py": frozenset(
        {'MEMINFO_PATH = Path("/proc/meminfo")'}
    ),
    "src/pyntara/tasks/three_x_ui_xray_setup.py": frozenset(
        {
            'IPV4_PATTERN = re.compile(r"\\d{1,3}(?:\\.\\d{1,3}){3}")',
            'VERSION_PATTERN = re.compile(r"(\\d+\\.\\d+\\.\\d+)")',
        }
    ),
    "src/pyntara/tasks/yggdrasil_service_setup.py": frozenset(
        {
            "CONNECTED_PATTERN = re.compile(",
            "PEER_URI_PATTERN = re.compile(",
            'VERSION_PATTERN = re.compile(r"(\\d+\\.\\d+\\.\\d+)")',
        }
    ),
    "src/pyntara/tasks/zram_service.py": frozenset(
        {
            'CPUINFO_PATH = Path("/proc/cpuinfo")',
            'MEMINFO_PATH = Path("/proc/meminfo")',
            'SYS_BLOCK_PATH = Path("/sys/block")',
            'ZRAM_CONTROL_DIR = Path("/sys/class/zram-control")',
            'ZRAM_HOT_ADD_PATH = ZRAM_CONTROL_DIR / "hot_add"',
            'ZRAM_HOT_REMOVE_PATH = ZRAM_CONTROL_DIR / "hot_remove"',
        }
    ),
    "src/pyntara/tor_address.py": frozenset({"FALLBACK_NOTE = ("}),
    "src/pyntara/utils.py": frozenset(
        {
            "CONSONANT_INDEX = {char: index for index, char in enumerate(CONSONANTS)}",
            'CONSONANTS = "bdfghjklmnprstvz"',
            "PROQUINT_LETTERS = frozenset(CONSONANTS + VOWELS)",
            'TRAILING_MARKER = "-"',
            "VOWEL_INDEX = {char: index for index, char in enumerate(VOWELS)}",
            'VOWELS = "aiou"',
        }
    ),
}

# Command argv literals: every command of an external tool the run invokes
# is a config value with its placeholders. These are the call sites that
# still spell their argv in code, module by module; the list is the work
# that is left and must shrink with every migration block. The package
# group of the shared helpers is the last module on the list.
COMMAND_ARGV_ALLOWED: dict[str, frozenset[str]] = {
    "src/pyntara/utils.py": frozenset(
        {
            '["ss", "-tlnp", f"sport = :{port}"],',
            '["systemctl", "is-active", name],',
            '["systemctl", "is-enabled", name],',
            '["systemctl", "show", "-p", "MainPID", "--value", service_name],',
            'run_command(["systemctl", "stop", service_unit_name], timeout=timeout)',
        }
    ),
}

# Absolute path literals outside /proc, /sys and /dev: the augeas node
# prefix of the two ssh tasks, which is the interface of an external tool
# and therefore a value.
PATH_LITERALS_ALLOWED: dict[str, frozenset[str]] = {}

# A regular expression shared by two or more modules: the pattern of a
# three part version, copied in the modules that read a release version.
# A pattern is an exception, so the copies are allowed to exist; the count
# here states how many modules write it, and a fifth copy fails the suite
# and asks for the shared vocabulary of the engine table instead.
DUPLICATED_PATTERNS_ALLOWED: dict[str, int] = {
    "(\\d+\\.\\d+\\.\\d+)": 3,
}


def _module_paths() -> list[Path]:
    """Every module of the package except the config layer."""

    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and CONFIG_LAYER not in path.parents
    )


def _relative(path: Path) -> str:
    """The path of a module as it is written in the allowlists."""

    return str(path.relative_to(PACKAGE_ROOT.parent.parent))


def _offenders(pattern: re.Pattern[str]) -> dict[str, frozenset[str]]:
    """The lines of every module that match one guard rule."""

    found: dict[str, set[str]] = {}
    for path in _module_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            if pattern.search(line):
                found.setdefault(_relative(path), set()).add(line.strip())
    return {module: frozenset(lines) for module, lines in found.items()}


def _path_offenders() -> dict[str, frozenset[str]]:
    """The lines of every module with an absolute path outside the kernel.

    A path under /proc, /sys or /dev is the path of the kernel and of the
    devices it exposes, which is an exception of the config spec, so those
    lines are not offenders.
    """

    found: dict[str, set[str]] = {}
    for path in _module_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ABSOLUTE_PATH_LITERAL.search(line)
            if match and not match.group(1).startswith(KERNEL_PATH_PREFIXES):
                found.setdefault(_relative(path), set()).add(line.strip())
    return {module: frozenset(lines) for module, lines in found.items()}


def _assert_matches_allowlist(
    shape: str, offenders: dict[str, frozenset[str]], allowed: dict[str, frozenset[str]]
) -> None:
    """Fail on a new value in code and on a stale allowlist entry.

    The comparison is symmetric on purpose: an entry that no longer
    matches means the value moved into the config, and the entry must
    leave the allowlist in the same commit, so the list shrinks with the
    migration instead of describing an older state.
    """

    added = {
        module: sorted(lines - allowed.get(module, frozenset()))
        for module, lines in offenders.items()
        if lines - allowed.get(module, frozenset())
    }
    stale = {
        module: sorted(lines - offenders.get(module, frozenset()))
        for module, lines in allowed.items()
        if lines - offenders.get(module, frozenset())
    }
    assert not added, (
        f"{shape} written in code: {added}. A value of a listed type lives "
        "in config/ unless its type is an exception "
        "(docs/spec/config-content.md, Exceptions)"
    )
    assert not stale, (
        f"{shape} allowlist entries that no longer match: {stale}. A "
        "migrated value must leave the allowlist in the same commit"
    )


def _patterns_by_module() -> dict[str, set[str]]:
    """The modules that carry each compiled regular expression literal."""

    found: dict[str, set[str]] = {}
    for path in _module_paths():
        text = path.read_text(encoding="utf-8")
        for match in COMPILED_PATTERN.finditer(text):
            found.setdefault(match.group(2), set()).add(_relative(path))
    return found


def test_no_module_defines_a_value_constant() -> None:
    # A module level constant holds a value only for the reader of the
    # module: the config is the one place where the reader sees the machine
    # the installer builds and changes it (architecture contract,
    # Configuration).
    _assert_matches_allowlist(
        "module level constant of a value",
        _offenders(VALUE_CONSTANT_DEFINITION),
        VALUE_CONSTANTS_ALLOWED,
    )


def test_no_module_spells_a_command_argv() -> None:
    # Every command the run invokes is a config value with its placeholders,
    # so a mirror host, a renamed unit or another tool needs no code change.
    _assert_matches_allowlist(
        "command argv",
        _offenders(COMMAND_ARGV_LITERAL),
        COMMAND_ARGV_ALLOWED,
    )


def test_no_module_carries_an_absolute_path_literal() -> None:
    # Every path of a file or a directory the run reads or writes is a
    # config value; only the paths of the kernel and of the devices it
    # exposes stay in code.
    _assert_matches_allowlist(
        "absolute path literal",
        _path_offenders(),
        PATH_LITERALS_ALLOWED,
    )


def test_no_regular_expression_is_copied_between_modules() -> None:
    # A value of an exception type stays in code and is written once: two
    # modules that need the same pattern import one definition instead of
    # copying it.
    duplicated = {
        pattern: len(modules)
        for pattern, modules in _patterns_by_module().items()
        if len(modules) > 1
    }
    assert duplicated == DUPLICATED_PATTERNS_ALLOWED, (
        "regular expressions shared by several modules: "
        f"{sorted(set(duplicated) | set(DUPLICATED_PATTERNS_ALLOWED))}"
    )


def test_the_guard_refuses_a_new_value_and_a_stale_entry() -> None:
    # The comparison is symmetric: a value that appears in the code fails,
    # and an entry whose value left the code fails as well, so the
    # allowlists can only shrink towards the empty state.
    with pytest.raises(AssertionError):
        _assert_matches_allowlist(
            "constant", {"module.py": frozenset({"NEW = 1"})}, {}
        )
    with pytest.raises(AssertionError):
        _assert_matches_allowlist(
            "constant", {}, {"module.py": frozenset({"GONE = 1"})}
        )
    _assert_matches_allowlist(
        "constant",
        {"module.py": frozenset({"KEPT = 1"})},
        {"module.py": frozenset({"KEPT = 1"})},
    )


def test_every_rule_finds_its_shape_in_a_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each rule recognises the shape it refuses: a constant of a value, a
    # command argv, an absolute path outside the kernel, and a regular
    # expression two modules share.
    package = tmp_path / "src" / "pyntara"
    first = package / "first.py"
    second = package / "second.py"
    first.parent.mkdir(parents=True)
    first.write_text(
        "THRESHOLD_SECONDS = 30\n"
        "PROFILE_ID_PATTERN = re.compile(r'[0-9a-f]{6}')\n"
        'run_command(["nmcli", "general", "reload"], timeout=60)\n'
        'STATE_FILE_PATH = Path("/var/lib/pyntara/state")\n'
        "MEMORY_FILE_PATH = Path('/proc/meminfo')\n",
        encoding="utf-8",
    )
    second.write_text(
        "COPY_PATTERN = re.compile(r'[0-9a-f]{6}')\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "test_config_value_guard.PACKAGE_ROOT", package
    )
    monkeypatch.setattr(
        "test_config_value_guard.CONFIG_LAYER", package / "config"
    )
    constants = _offenders(VALUE_CONSTANT_DEFINITION)
    assert constants["src/pyntara/first.py"] == frozenset(
        {
            "THRESHOLD_SECONDS = 30",
            "PROFILE_ID_PATTERN = re.compile(r'[0-9a-f]{6}')",
            "STATE_FILE_PATH = Path(\"/var/lib/pyntara/state\")",
            "MEMORY_FILE_PATH = Path('/proc/meminfo')",
        }
    )
    assert _offenders(COMMAND_ARGV_LITERAL)["src/pyntara/first.py"] == frozenset(
        {'run_command(["nmcli", "general", "reload"], timeout=60)'}
    )
    assert _path_offenders()["src/pyntara/first.py"] == frozenset(
        {'STATE_FILE_PATH = Path("/var/lib/pyntara/state")'}
    )
    assert {
        pattern: len(modules)
        for pattern, modules in _patterns_by_module().items()
        if len(modules) > 1
    } == {"[0-9a-f]{6}": 2}
