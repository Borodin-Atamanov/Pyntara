"""Shared helpers for task modules.

run_command is the single command-execution wrapper used by tasks: no
shell, real-time output streaming, timeout and return-code checking
(docs/guides/project-rules.md section 4). The timeout is a required
parameter: the value comes from config.toml through Context, never from a
hardcoded default (architecture contract section 3).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

# apt must never ask questions; every package operation runs noninteractive.
# The single definition lives here so tasks cannot diverge.
APT_NONINTERACTIVE_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def package_is_installed(package: str, timeout: float) -> bool:
    """True when dpkg considers the package fully installed.

    The status query distinguishes "install ok installed" from leftovers
    like "deinstall ok config-files", so an uninstalled package is never
    treated as installed. The timeout comes from config.toml.
    """

    result = run_command(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def install_package_once(package: str, timeout: float) -> tuple[bool, str]:
    """Install one package; return (success, error_text).

    apt runs noninteractive through the shared environment so it never
    asks questions. Any nonzero exit or timeout is a failure with the
    exception text; the caller decides whether to retry.
    """

    try:
        run_command(
            ["apt-get", "install", "-y", package],
            extra_env=APT_NONINTERACTIVE_ENV,
            timeout=timeout,
        )
        return True, ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def read_os_release(path: Path) -> dict[str, str]:
    """Parse an os-release file into a dict of shell-style variables.

    Every line has the form KEY="value" or KEY=value; surrounding quotes
    are stripped, comments and blank lines skipped. The freedesktop spec
    makes the file optional, so a missing file yields an empty dict;
    other read errors raise OSError. Shared by tasks that must know the
    distribution family or the release codename.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def os_family_is_debian(os_release: dict[str, str]) -> bool:
    """True when the os-release ID or ID_LIKE names Debian or Ubuntu.

    Debian-based distributions declare ID=debian or ID=ubuntu, and
    derivatives declare ID_LIKE=debian. The check covers both fields, so
    a derivative of a derivative such as ID_LIKE="ubuntu debian" still
    resolves to Debian family.
    """

    fields = f"{os_release.get('ID', '')} {os_release.get('ID_LIKE', '')}"
    tokens = {token.casefold() for token in fields.split()}
    return bool(tokens & {"debian", "ubuntu"})


def dpkg_architecture(timeout: float) -> str:
    """The dpkg architecture of the target machine, e.g. amd64.

    dpkg --print-architecture is the single source of the Debian
    architecture name used by package asset names. Raises
    CalledProcessError or TimeoutExpired when the query fails.
    """

    result = run_command(
        ["dpkg", "--print-architecture"],
        check=True,
        capture=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def trim_whitespace(text: str) -> str:
    """Remove the leading and trailing whitespace of a text.

    Whitespace is spaces, tabs, newlines and carriage returns; everything
    between the edges is preserved, so multi-line output keeps its
    internal structure. The collector trims every module output with this
    helper before it enters the report: console commands, config files and
    user data all end their lines with a newline that must not reach the
    telemetry (docs/spec/system-metrics.md, section Report collector).
    """

    return text.strip()


def run_command(
    command: Iterable[str],
    *,
    timeout: float,
    extra_env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and control its outcome.

    Output streams to the terminal in real time by default; pass
    capture=True for quiet status queries. With check=True a nonzero return
    code raises CalledProcessError; with check=False the caller inspects
    returncode itself. A command that exceeds the timeout raises
    TimeoutExpired. The optional input feeds the process stdin, so a
    caller can pass data that is too large for a command argument.
    """

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    if capture:
        return subprocess.run(
            list(command),
            env=env,
            timeout=timeout,
            check=check,
            capture_output=True,
            text=True,
            input=input,
        )
    return subprocess.run(
        list(command),
        env=env,
        timeout=timeout,
        check=check,
        text=True,
        input=input,
    )


def service_is_enabled(name: str, timeout: float) -> bool:
    """True when the systemd service is enabled for boot.

    systemctl is-enabled reports the boot state; "enabled" is the
    ordinary persistent state and "enabled-runtime" the state of a unit
    enabled only for the current boot (for example by a systemd
    generator), both mean the service starts at boot, every other output
    (disabled, masked, not-found) is False.
    """

    result = run_command(
        ["systemctl", "is-enabled", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() in (
        "enabled",
        "enabled-runtime",
    )


def service_is_active(name: str, timeout: float) -> bool:
    """True when the systemd service is currently running.

    systemctl is-active reports the runtime state; "active" is the only
    state that means the service is running, every other output (inactive,
    failed, activating) is False.
    """

    result = run_command(
        ["systemctl", "is-active", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "active"


def ensure_root_owner(path: Path) -> None:
    """Set owner root:root when the process runs as root.

    The installer runs under sudo, so the ownership is applied on real
    machines; non-root test runs skip the chown, because it would fail
    without privileges.
    """

    if os.geteuid() == 0:
        os.chown(path, 0, 0)


def backoff_delay(
    failures: int, base_seconds: int, multiplier: int, max_seconds: int
) -> int:
    """The pause after failures consecutive failed cycles, in seconds.

    The first failed cycle waits base_seconds, every further failure
    multiplies the pause by the integer multiplier until max_seconds; all
    values are whole seconds, so no rounding is needed. A call without
    failures returns the base, so the helper is safe at any counter
    value. The shared geometric backoff of the System Metrics retry loops
    (docs/spec/system-metrics.md, sections Schedule and retry and Report
    collector).
    """

    if failures < 1:
        return base_seconds
    # int ** int resolves to Any in mypy strict, so the growth is widened
    # explicitly; the exponent is never negative here, the widening keeps
    # the integer arithmetic unchanged.
    growth = int(multiplier ** (failures - 1))
    return min(base_seconds * growth, max_seconds)


# Proquint encoding of arbitrary bytes into pronounceable words
# (draft-rayner-proquint). The alphabet is a fixed protocol contract of
# the format, not configuration: it lives only here, inside the code.
CONSONANTS = "bdfghjklmnprstvz"
VOWELS = "aiou"
CONSONANT_INDEX = {char: index for index, char in enumerate(CONSONANTS)}
VOWEL_INDEX = {char: index for index, char in enumerate(VOWELS)}
PROQUINT_LETTERS = frozenset(CONSONANTS + VOWELS)
TRAILING_MARKER = "-"


def _proquint_encode_word(word: int) -> str:
    """Encode one 16-bit word as a five-letter proquint syllable.

    The bit layout of draft-rayner-proquint: consonant on bits 15-12,
    vowel on 11-10, consonant on 9-6, vowel on 5-4, consonant on 3-0,
    all big-endian.
    """

    return (
        CONSONANTS[(word >> 12) & 0xF]
        + VOWELS[(word >> 10) & 0x3]
        + CONSONANTS[(word >> 6) & 0xF]
        + VOWELS[(word >> 4) & 0x3]
        + CONSONANTS[word & 0xF]
    )


def proquint_encode(data: bytes, separator: str = "-") -> str:
    """Encode arbitrary bytes into a pronounceable proquint string.

    Each 16-bit big-endian word becomes one five-letter syllable; the
    syllables are joined with separator. Odd-length data is padded with a
    trailing zero byte and marked with a single trailing dash that is not
    part of the separator. Empty data encodes to an empty string. The
    separator must be a string without any proquint alphabet letters, or
    ValueError is raised: such a separator would shift word boundaries
    and make the result undecodable.

    Example: b'\x7f\x00\x00\x01' encodes to "lusab-babad", the
    canonical draft-rayner-proquint example for 127.0.0.1.
    """

    if not isinstance(data, bytes):
        raise TypeError("proquint data must be bytes")
    if not isinstance(separator, str):
        raise TypeError("proquint separator must be a string")
    if not separator.isascii() or (set(separator) & PROQUINT_LETTERS):
        raise ValueError("proquint separator must not contain alphabet letters")
    if not data:
        return ""
    padded = data + b"\x00" if len(data) % 2 else data
    trailing = len(data) % 2 == 1
    words = [
        int.from_bytes(padded[index : index + 2], "big")
        for index in range(0, len(padded), 2)
    ]
    encoded = separator.join(_proquint_encode_word(word) for word in words)
    return encoded + TRAILING_MARKER if trailing else encoded


def proquint_decode(s: str) -> bytes | None:
    """Decode a proquint string back into bytes, or None on any error.

    The string is case-insensitive. Surrounding whitespace is trimmed
    first so a copied line ending in a newline still decodes. A single
    trailing dash marks odd-length data and is consumed together with the
    padding zero byte; without it, the padding byte is kept, so the
    format cannot distinguish even data ending in a zero from padded odd
    data without the marker. Non-alphabet characters are ignored as
    separators. A string that filters down to nothing decodes to None
    unless it was empty after trimming (then it is b''). The result of
    decoding is None when the filtered length is not a multiple of five
    or a syllable position holds the wrong letter kind.

    Example: "lusab-babad" decodes to b'\x7f\x00\x00\x01'.
    """

    if not isinstance(s, str):
        raise TypeError("proquint input must be a string")
    trimmed = s.strip()
    if not trimmed:
        return b""
    has_trailing = trimmed.endswith(TRAILING_MARKER)
    body = trimmed[:-1] if has_trailing else trimmed
    filtered = "".join(char for char in body.lower() if char in PROQUINT_LETTERS)
    if not filtered or len(filtered) % 5:
        return None
    words: list[int] = []
    for start in range(0, len(filtered), 5):
        chunk = filtered[start : start + 5]
        if (
            chunk[0] not in CONSONANT_INDEX
            or chunk[1] not in VOWEL_INDEX
            or chunk[2] not in CONSONANT_INDEX
            or chunk[3] not in VOWEL_INDEX
            or chunk[4] not in CONSONANT_INDEX
        ):
            return None
        words.append(
            (CONSONANT_INDEX[chunk[0]] << 12)
            | (VOWEL_INDEX[chunk[1]] << 10)
            | (CONSONANT_INDEX[chunk[2]] << 6)
            | (VOWEL_INDEX[chunk[3]] << 4)
            | CONSONANT_INDEX[chunk[4]]
        )
    raw = b"".join(word.to_bytes(2, "big") for word in words)
    if has_trailing:
        if not raw or raw[-1] != 0:
            return None
        return raw[:-1]
    return raw
