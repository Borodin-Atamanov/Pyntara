"""Shared helpers for task modules.

run_command is the single command-execution wrapper used by tasks: no
shell, real-time output streaming, timeout and return-code checking
(project rules, General engineering requirements). The timeout is a required
parameter: the value comes from config.toml through Context, never from a
hardcoded default (architecture contract, Configuration).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path

from pyntara import logger

# apt must never ask questions; every package operation runs noninteractive.
# The single definition lives here so tasks cannot diverge.
APT_NONINTERACTIVE_ENV = {"DEBIAN_FRONTEND": "noninteractive"}

# Root of the clone this code runs from: the package lives in src/pyntara/, so
# the root is two directories above this file. It is computed once here and
# read once by the entry point, which passes it into the Context; a task never
# imports it, because the clone the task must read is the one the run started
# from and is the job of the composition root to name.
REPO_ROOT = Path(__file__).resolve().parents[2]


def task_data_dir(repo_root: Path, section: str) -> Path:
    """The task data directory of one task, from the clone root.

    Every template a task renders lives under task_data/<section>/ in the
    clone the run started from, so the layout is known here and nowhere
    else.
    """

    return repo_root / "task_data" / section


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


def install_packages(
    packages: list[str],
    *,
    install_timeout: float,
    update_timeout: float,
    retries: int,
    skip_update: bool,
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Install each package individually; return (installed, failures, warnings).

    failures is a list of (name, reason); warnings carries non-fatal
    problems such as a failed apt index refresh. The apt index is refreshed
    once before the first install, so packages resolve from a fresh index;
    skip_update=True disables the refresh for test or offline runs. Each
    package gets one initial attempt plus `retries` retries; a package that
    still fails is recorded and never blocks the others.
    """

    installed: list[str] = []
    failures: list[tuple[str, str]] = []
    warnings: list[str] = []
    if not skip_update:
        try:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_NONINTERACTIVE_ENV,
                timeout=update_timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"apt index refresh: {exc}")
    for package in packages:
        ok = False
        error = ""
        for _ in range(retries + 1):
            ok, error = install_package_once(package, install_timeout)
            if ok:
                break
        if ok:
            installed.append(package)
        else:
            failures.append((package, error))
    return installed, failures, warnings


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


def curl_flags(
    timeout_seconds: float,
    retries: int,
    connect_timeout_seconds: float,
    retry_max_time_seconds: int,
    retry_delay_seconds: int,
) -> list[str]:
    """Retry and timeout flags shared by every download or release query curl.

    --max-time bounds a single attempt and --connect-timeout bounds only
    the connection phase, so an unreachable host fails fast instead of
    eating the whole per-attempt budget. --retry repeats an attempt,
    --retry-all-errors makes every failure retryable (a transfer dropped
    after the connection is up, exit 56, is not a retry condition for
    --retry on its own), --retry-delay spaces the attempts apart,
    --retry-max-time bounds the total retry window and
    --retry-connrefused adds refused connections to the retryable set.
    The flags are the single definition for all task curl calls, so the
    values come from the [engine] config and never diverge (architecture
    contract, Configuration).
    """

    return [
        "--max-time",
        str(timeout_seconds),
        "--connect-timeout",
        str(connect_timeout_seconds),
        "--retry",
        str(retries),
        "--retry-all-errors",
        "--retry-delay",
        str(retry_delay_seconds),
        "--retry-max-time",
        str(retry_max_time_seconds),
        "--retry-connrefused",
    ]


# One-line summary curl prints after a completed download: the actual byte
# count, total time and average speed. The leading newline separates it
# from the progress meter, which ends without one. Download curls add
# --write-out with this format; release query curls stay silent, because
# their stdout is parsed as JSON.
CURL_DOWNLOAD_WRITE_OUT = (
    "\nDownloaded %{size_download} bytes in %{time_total}s "
    "at %{speed_download} bytes/s\n"
)


def run_command(
    command: Iterable[str],
    *,
    timeout: float,
    extra_env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    input: str | None = None,
    log_command: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and control its outcome.

    Output streams to the terminal in real time by default; pass
    capture=True for quiet status queries. With check=True a nonzero return
    code raises CalledProcessError; with check=False the caller inspects
    returncode itself. A command that exceeds the timeout raises
    TimeoutExpired. The optional input feeds the process stdin, so a
    caller can pass data that is too large for a command argument.

    log_command=True reports the command through the logger: the line
    `  run : <command>` before the process and `  /run: <exit_code>
    <seconds>s <command>` after it, mirrored to the journal, so every
    command in the install log carries its duration and exit code (project
    rules, Task progress output). A call that must not print its command,
    because the command line carries a secret, passes log_command=False:
    logging secret values is forbidden.
    """

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    command_list = list(command)
    command_text = " ".join(command_list)
    if log_command:
        logger.log_run_start(command_text)
    start = time.perf_counter()
    try:
        if capture:
            result = subprocess.run(
                command_list,
                env=env,
                timeout=timeout,
                check=check,
                capture_output=True,
                text=True,
                input=input,
            )
        else:
            result = subprocess.run(
                command_list,
                env=env,
                timeout=timeout,
                check=check,
                text=True,
                input=input,
            )
    except subprocess.CalledProcessError as exc:
        if log_command:
            logger.log_run_end(
                command_text, exc.returncode, time.perf_counter() - start
            )
        raise
    except subprocess.TimeoutExpired:
        if log_command:
            logger.log_run_end(command_text, None, time.perf_counter() - start)
        raise
    if log_command:
        logger.log_run_end(
            command_text, result.returncode, time.perf_counter() - start
        )
    return result


# The marker curl writes after every parallel transfer, so a merged
# answer text can be split back into one block per service; the effective
# URL follows the marker on the same line.
SOURCE_MARKER = "@@pyntara-source@@"


def _parallel_curl_command(
    urls: tuple[str, ...], timeout_seconds: float
) -> list[str]:
    """The one curl call that queries every URL at the same time.

    --parallel runs the transfers together and --parallel-max keeps them
    all in flight; --write-out adds a marker line with the effective URL
    after each answer, so every answer can be attributed to the service
    that gave it even though the answers arrive interleaved. Each
    transfer is bounded by timeout_seconds, so the call takes at most that
    long even when a service never answers.
    """

    return [
        "curl",
        "--parallel",
        "--parallel-max",
        str(len(urls)),
        "--silent",
        "--max-time",
        str(timeout_seconds),
        "--write-out",
        f"\n{SOURCE_MARKER} %{{url_effective}}\n",
        *urls,
    ]


def split_url_answers(text: str) -> tuple[tuple[str, str], ...]:
    """Split marked curl output into (service URL, answer) pairs.

    curl writes the marker of a transfer after that transfer finished and
    after its answer, so the lines collected before a marker are the
    answer of the service that marker names. The order follows the
    completion of the transfers, not the order of the URL list, and a
    transfer that answered nothing still carries its marker, so an empty
    answer is told apart from a service that was never asked.
    """

    answers: list[tuple[str, str]] = []
    pending: list[str] = []
    for line in text.splitlines():
        if line.startswith(SOURCE_MARKER):
            url = line[len(SOURCE_MARKER) :].strip()
            answers.append((url, "\n".join(pending).strip()))
            pending = []
            continue
        pending.append(line)
    return tuple(answers)


def fetch_urls_in_parallel(
    urls: tuple[str, ...],
    query_timeout_seconds: float,
    command_timeout_seconds: float,
) -> str:
    """Query every URL in one parallel curl call and return the answers.

    All URLs run in the same process at the same time, so one slow service
    delays the result by at most query_timeout_seconds instead of being
    waited for in turn, and the answers that did arrive are kept.
    command_timeout_seconds bounds the whole process; a process that
    exceeds it is killed, so the call always returns.

    A nonzero curl exit code means at least one transfer failed (a service
    that is down or that answered too late); the answers of the other
    transfers are still in the output, so the exit code decides nothing
    here and the caller sees exactly the answers the machine could get.
    Returns an empty string for an empty URL list, when curl is missing or
    when the process cannot start.

    The helper is shared by every service that must ask several addresses
    at once (the public address detection and the country detection), so
    the query shape and its timeout policy live in one place. The answers
    carry the source markers of split_url_answers, so a caller that needs
    to know which service said what splits them with that helper.
    """

    if not urls:
        return ""
    try:
        process = subprocess.Popen(
            _parallel_curl_command(urls, query_timeout_seconds),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return ""
    try:
        output, _ = process.communicate(timeout=command_timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    return output


def fetch_urls_by_source(
    urls: tuple[str, ...],
    query_timeout_seconds: float,
    command_timeout_seconds: float,
) -> tuple[tuple[str, str], ...]:
    """Query every URL in parallel; return (service URL, answer) pairs.

    The same call as fetch_urls_in_parallel, split per service, so a
    caller that must standardize and merge the answers of different
    services knows which service produced which answer.
    """

    return split_url_answers(
        fetch_urls_in_parallel(urls, query_timeout_seconds, command_timeout_seconds)
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


def port_listener_pid(port: int, timeout: float) -> int | None:
    """The PID of the process listening on the TCP port, or None.

    The query runs `ss -tlnp "sport = :PORT"` and parses the first
    pid=N token in the process column. None when the port is free, when
    ss is unavailable, or when the query fails: an unknown listener is
    reported as absent so the caller can proceed safely.
    """

    result = run_command(
        ["ss", "-tlnp", f"sport = :{port}"],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = re.search(r"pid=(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def service_main_pid(service_name: str, timeout: float) -> int | None:
    """The systemd MainPID of the service, or None when not running.

    systemctl show -p MainPID --value prints 0 when the unit has no
    running main process; that is normalized to None, so a stopped
    service never matches a live listener.
    """

    result = run_command(
        ["systemctl", "show", "-p", "MainPID", "--value", service_name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    try:
        pid = int(result.stdout.strip())
    except ValueError:
        return None
    return pid or None


def process_comm(pid: int) -> str | None:
    """The process name from /proc/<pid>/comm, or None when unreadable.

    The name identifies the process when the systemd MainPID does not
    match (a manually started binary or a stale unit state). A missing
    or unreadable entry means the process is gone.
    """

    try:
        value = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8")
    except OSError:
        return None
    return value.strip() or None


def ensure_port_free(
    port: int,
    service_unit_name: str,
    timeout: float,
    *,
    service_process_name: str | None = None,
    kill_grace_seconds: int = 5,
) -> str | None:
    """Free the TCP port for a new listener; returns the action taken.

    Detects the process listening on the port. When the listener is the
    given systemd service (MainPID match) or carries the configured
    process name, the service is stopped with systemctl. Any other
    listener is an unknown process and is terminated with SIGTERM, then
    SIGKILL after kill_grace_seconds if it still holds the port. Returns
    None when the port is already free, a short message otherwise.
    Raises RuntimeError when the port is still occupied after the action.
    """

    pid = port_listener_pid(port, timeout)
    if pid is None:
        return None
    is_ours = pid == service_main_pid(service_unit_name, timeout)
    if not is_ours and service_process_name:
        is_ours = process_comm(pid) == service_process_name
    if is_ours:
        run_command(["systemctl", "stop", service_unit_name], timeout=timeout)
        if port_listener_pid(port, timeout) is None:
            return f"stopped {service_unit_name} listening on port {port}"
        # systemctl did not free the port: the listener is not the managed
        # service (for example a manually started binary), fall through to
        # the unknown-process termination below.

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    deadline = time.monotonic() + kill_grace_seconds
    while time.monotonic() < deadline:
        if port_listener_pid(port, timeout) is None:
            return f"terminated unknown process {pid} on port {port}"
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return None
    if port_listener_pid(port, timeout) is not None:
        raise RuntimeError(
            f"process {pid} still listens on port {port} after SIGKILL"
        )
    return f"killed unknown process {pid} on port {port}"


def ensure_root_owner(path: Path, owner_uid: int, owner_gid: int) -> None:
    """Set the configured owner when the process runs as root.

    The owner is the pair the [engine] config carries, root_owner_uid and
    root_owner_gid, so no module writes the uid or the gid of root itself.
    The installer runs under sudo, so the ownership is applied on real
    machines; non-root test runs skip the chown, because it would fail
    without privileges.
    """

    if os.geteuid() == 0:
        os.chown(path, owner_uid, owner_gid)


def session_environment_command(
    username: str, command_template: tuple[str, ...]
) -> list[str]:
    """The configured session environment command for one user.

    The command form lives in the config with a {username} placeholder, the
    same way the release query carries {repo}; the account name is the only
    part of the command the run decides.
    """

    return [part.replace("{username}", username) for part in command_template]


def session_environment_value(entry: str) -> str | None:
    """The usable value of one session environment line, or None.

    The session manager prints the environment in shell-quoted form: a plain
    value stays as it is, a value may be wrapped in double quotes, and a value
    with spaces or escapes is printed as an ANSI-C quoted string ($'...'). The
    run takes plain and double-quoted values only: guessing at an escape would
    put a wrong value into the environment of every child process, so an
    escaped value is skipped and named in the log instead.
    """

    if entry.startswith("$'") or "\\" in entry:
        return None
    if len(entry) >= 2 and entry.startswith('"') and entry.endswith('"'):
        return entry[1:-1]
    return entry


def parse_session_environment(text: str, keys: tuple[str, ...]) -> dict[str, str]:
    """The configured session variables of a session environment text.

    Every line is KEY=VALUE. A key outside the configured list is ignored:
    the same text carries HOME, PATH, SSH_AUTH_SOCK and the locale, which
    belong to the root process of the run and are never taken. A value the
    session manager printed in escaped form is skipped and reported.
    """

    environment: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, entry = line.partition("=")
        if not separator or key not in keys:
            continue
        value = session_environment_value(entry)
        if value is None:
            logger.log_progress(
                f"session variable {key} is printed in escaped form, left alone"
            )
            continue
        environment[key] = value
    return environment


def user_session_environment(
    username: str,
    *,
    command_template: tuple[str, ...],
    keys: tuple[str, ...],
    timeout: float,
) -> dict[str, str]:
    """The session environment of one user, or an empty dict.

    The configured command asks the session manager of that user for its
    environment, so a run started over a remote console reads the variables
    of the live desktop session without depending on the caller. A missing
    user manager, a user without a session and a failed command all return an
    empty dict with a progress line: the caller then applies its settings for
    the next login instead of failing.
    """

    if not username or not command_template or not keys:
        return {}
    command = session_environment_command(username, command_template)
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.log_progress(f"cannot read the desktop session environment: {exc}")
        return {}
    if result.returncode != 0:
        logger.log_progress(
            f"cannot read the desktop session environment: {' '.join(command)} "
            f"exited {result.returncode}"
        )
        return {}
    return parse_session_environment(result.stdout, keys)


def session_bus_address(
    username: str,
    *,
    command_template: tuple[str, ...],
    keys: tuple[str, ...],
    bus_key: str,
    timeout: float,
) -> str | None:
    """The session bus address of a user, or None when no session exists.

    The address reaches the DBus services of the session, so the DBus clients
    of the tasks need this one value and no display variable. A missing
    session returns None, so the caller can skip the live action and let the
    settings apply at the next login.
    """

    if not bus_key:
        return None
    environment = user_session_environment(
        username, command_template=command_template, keys=keys, timeout=timeout
    )
    return environment.get(bus_key) or None


def session_environment(
    username: str,
    *,
    command_template: tuple[str, ...],
    keys: tuple[str, ...],
    bus_key: str,
    display_keys: tuple[str, ...],
    timeout: float,
) -> dict[str, str]:
    """The session environment of a user, only when the session is live.

    A session counts as live when its bus variable and one of its display
    variables carry a value: the bus reaches the DBus services, a display
    variable lets a GUI tool connect to the running compositor instead of
    picking a platform plugin that aborts. A partial environment is reported
    and dropped, because a GUI tool without a display variable fails before it
    applies anything.
    """

    environment = user_session_environment(
        username, command_template=command_template, keys=keys, timeout=timeout
    )
    if not environment:
        return {}
    if bus_key not in environment:
        logger.log_progress(
            f"the desktop session of {username} reports no {bus_key}, "
            "its settings apply at the next login"
        )
        return {}
    present = [key for key in display_keys if key in environment]
    if not present:
        logger.log_progress(
            f"the desktop session of {username} reports no display variable "
            f"({', '.join(display_keys)}), its GUI settings apply at the next login"
        )
        return {}
    return environment


def export_session_environment(
    environment: Mapping[str, str],
    target: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Put the session variables into the environment of this process.

    Every child process the run starts inherits the target mapping, so one
    export reaches every task and every tool a task calls, no matter where the
    run itself was started. The mapping defaults to the environment of the
    process. The exported names are returned, so the caller can report what it
    handed to the run.
    """

    destination = os.environ if target is None else target
    for key, value in environment.items():
        destination[key] = value
    return tuple(environment)


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
