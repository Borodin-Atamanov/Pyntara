"""Task keyring_setup: create the KDE wallet of a machine that logs in automatically.

A machine that logs in automatically never types the account password, and the
PAM service of that path carries no wallet module, so nothing creates the KDE
wallet at login. The first program that asks for one starts its creation, and
the daemon answers that request with a dialog: it offers a new wallet and asks
for a password. A wallet created that way is protected, so every later program
asks for that password again, and the user of a machine that never types a
password has none to give.

The task takes the dialog out of the way by creating the wallet itself, before
any program asks for it, and by giving it an empty password. A machine under
full disk encryption can afford that: the wallet stays a private file of the
user and opens without a question. The creation uses the entry point the PAM
module of the wallet uses, which takes a ready key instead of asking for a
password, and the key is the one that module derives from an empty password,
so the wallet opens without a dialog now and on every later start of the
daemon.

Two states lead to the goal. A session whose wallet file is missing gets the
wallet created without a password. A session whose wallet file exists is left
untouched and reported, because a wallet may carry a password and asking it to
open is the very call that shows the dialog. Force mode replaces such a wallet:
every file of every wallet of the desktop user goes to the trash of that user
and the wallet is created again without a password, which is the state a fresh
installation plus a normal run would reach.

The client that speaks the protocol ships under task_data/ of the clone and
runs as the desktop user on the session bus of that user, because the wallet
belongs to the session and not to the root process of the run. The names of
the service, the constants of the key and the outcome vocabulary come from the
values module of the section and are substituted into the client, so they
stand in one place. A session that cannot be found leaves the wallet alone and
reports it, and force mode does nothing there either: a wallet that is missing
would be created by the daemon with the dialog this task removes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import install_missing_packages
from pyntara.utils import (
    move_paths_to_trash,
    run_command,
    session_bus_address,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import keyring_setup as values
from pyntara.values import missing_value_names


def _session_bus_env() -> dict[str, str]:
    """The one-entry environment that reaches the live session bus.

    The bus address comes from the session manager of the desktop user, so the
    client works the same whether the run started inside the session or over a
    remote console. An empty dict means no live session was found.
    """

    bus = session_bus_address(
        common_values.DESKTOP_USERNAME,
        command_template=engine_values.SESSION_ENVIRONMENT_COMMAND,
        keys=engine_values.SESSION_ENVIRONMENT_KEYS,
        bus_key=engine_values.SESSION_BUS_KEY,
        timeout=engine_values.PROCESS_CHECK_TIMEOUT_SECONDS,
    )
    if bus is None:
        return {}
    return {engine_values.SESSION_BUS_KEY: bus}


def _home_env() -> dict[str, str]:
    """Environment that points the client at the home of the desktop user."""

    return {"HOME": common_values.DESKTOP_HOME_DIR}


def _client_source(script_path: Path) -> str:
    """The client of the clone with the names of the section substituted in.

    The names of the wallet service, the constants of the key derivation and
    the words of the answer protocol are values, so the client carries
    placeholders and every name reaches it from one place.
    """

    template = Template(script_path.read_text(encoding="utf-8"))
    return template.substitute(
        wallet_bus_name=values.WALLET_BUS_NAME,
        wallet_object_path=values.WALLET_OBJECT_PATH,
        wallet_interface_name=values.WALLET_INTERFACE_NAME,
        wallet_name_method_name=values.WALLET_NAME_METHOD_NAME,
        daemon_bus_name=values.DAEMON_BUS_NAME,
        daemon_object_path=values.DAEMON_OBJECT_PATH,
        daemon_open_method_name=values.DAEMON_OPEN_METHOD_NAME,
        daemon_open_signature=values.DAEMON_OPEN_SIGNATURE,
        key_algorithm=values.KEY_ALGORITHM,
        key_iterations=values.KEY_ITERATIONS,
        key_length_bytes=values.KEY_LENGTH_BYTES,
        salt_length_bytes=values.SALT_LENGTH_BYTES,
        wallet_directory_relative_path=values.WALLET_DIRECTORY_RELATIVE_PATH,
        wallet_file_suffix=values.WALLET_FILE_SUFFIX,
        wallet_salt_suffix=values.WALLET_SALT_SUFFIX,
        outcome_key=values.OUTCOME_KEY,
        detail_key=values.DETAIL_KEY,
        outcome_created=values.OUTCOME_CREATED,
        outcome_exists=values.OUTCOME_EXISTS,
        outcome_error=values.OUTCOME_ERROR,
    )


def _parse_answer(stdout: str) -> dict[str, str]:
    """The KEY=VALUE lines the client printed, as a mapping.

    The value may itself carry an equals sign, which happens in a DBus error
    text, so only the first one separates the key from the value.
    """

    answer: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            answer[key.strip()] = value.strip()
    return answer


def _run_client(
    *, script_path: Path, bus_env: dict[str, str]
) -> tuple[dict[str, str], str | None]:
    """Run the client as the desktop user; return (answer, error text)."""

    try:
        source = _client_source(script_path)
    except OSError as exc:
        return {}, f"cannot read the wallet client {script_path}: {exc}"
    command = [
        *substituted_command(
            values.RUNUSER_COMMAND, {"username": common_values.DESKTOP_USERNAME}
        ),
        *substituted_command(
            values.PYTHON_SCRIPT_COMMAND, {"python": engine_values.SYSTEM_PYTHON}
        ),
        source,
    ]
    try:
        result = run_command(
            command,
            extra_env={**_home_env(), **bus_env},
            timeout=values.CLIENT_TIMEOUT_SECONDS,
            capture=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = trim_whitespace(exc.stderr or "")
        suffix = f": {detail}" if detail else ""
        return {}, f"cannot run the wallet client: {exc}{suffix}"
    except subprocess.TimeoutExpired as exc:
        return {}, f"cannot run the wallet client: {exc}"
    return _parse_answer(result.stdout), None


def _wallet_file_paths() -> tuple[Path, ...]:
    """Every file of every wallet of the desktop user.

    A wallet owns three files that carry its name: the wallet itself, the salt
    of its key and the cache of item attributes. Force mode replaces all of
    them, so the files are found by their endings instead of a name written
    here, and a wallet under any name is replaced as well.
    """

    directory = (
        Path(common_values.DESKTOP_HOME_DIR) / values.WALLET_DIRECTORY_RELATIVE_PATH
    )
    if not directory.is_dir():
        return ()
    endings = (
        values.WALLET_FILE_SUFFIX,
        values.WALLET_SALT_SUFFIX,
        values.WALLET_ATTRIBUTES_SUFFIX,
    )
    return tuple(
        sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.name.endswith(endings)
        )
    )


def _replace_wallet_files() -> tuple[tuple[str, ...], list[str]]:
    """Move every wallet file of the desktop user into that user's trash."""

    paths = _wallet_file_paths()
    if not paths:
        return (), []
    moved, failures = move_paths_to_trash(
        paths,
        run_as_user_command=values.RUNUSER_COMMAND,
        username=common_values.DESKTOP_USERNAME,
        home_dir=common_values.DESKTOP_HOME_DIR,
        program=values.TRASH_PROGRAM,
        subcommand=values.TRASH_SUBCOMMAND,
        timeout=values.CLIENT_TIMEOUT_SECONDS,
    )
    return moved, list(failures)


def _done(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """A completed task carrying its warnings."""

    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def task(ctx: Context) -> TaskResult:
    """Create the KDE wallet of the desktop user without a password."""

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        return TaskResult(
            success=True,
            changed=False,
            message="the keyring values are not declared",
            warnings=(f"the keyring values are not declared: {', '.join(absent)}",),
        )
    warnings: list[str] = []
    _, installed, failures, package_warnings = install_missing_packages(
        ctx, values.PACKAGES
    )
    warnings.extend(package_warnings)
    warnings.extend(f"{name}: {reason}" for name, reason in failures)
    if installed:
        _log(f"installed: {', '.join(installed)}")
    bus_env = _session_bus_env()
    if not bus_env:
        warnings.append(
            "no live desktop session of "
            f"{common_values.DESKTOP_USERNAME} was found, so the KDE wallet was "
            "left alone and the first program that asks for it may open a dialog"
        )
        return _done(
            changed=False, message="no live desktop session found", warnings=warnings
        )
    force = ctx.task_name in ctx.force_tasks
    if force:
        moved, replace_failures = _replace_wallet_files()
        warnings.extend(replace_failures)
        if replace_failures:
            return _done(
                changed=False,
                message="the wallet files were not all replaced",
                warnings=warnings,
            )
        if moved:
            _log(
                f"moved {len(moved)} wallet files into the trash of "
                f"{common_values.DESKTOP_USERNAME}"
            )
    script_path = (
        task_data_dir(ctx.repo_root, ctx.task_name) / values.CLIENT_SCRIPT_FILE_NAME
    )
    _log(f"checking the KDE wallet of {common_values.DESKTOP_USERNAME}")
    answer, error = _run_client(script_path=script_path, bus_env=bus_env)
    if error is not None:
        warnings.append(error)
        return _done(
            changed=False, message="the KDE wallet was not checked", warnings=warnings
        )
    outcome = answer.get(values.OUTCOME_KEY, "")
    detail = answer.get(values.DETAIL_KEY, "")
    if outcome == values.OUTCOME_CREATED:
        _log(f"created the KDE wallet without a password: {detail}")
        return _done(
            changed=True,
            message=f"created the KDE wallet without a password: {detail}",
            warnings=warnings,
        )
    if outcome == values.OUTCOME_EXISTS:
        if force:
            warnings.append(
                "the file of the KDE wallet is back after it was moved into the "
                "trash, so the wallet still carries its password and may ask for "
                f"it: {detail}"
            )
            return _done(
                changed=False, message="the KDE wallet was not replaced", warnings=warnings
            )
        return _done(
            changed=False,
            message=f"the KDE wallet already exists and was left alone: {detail}",
            warnings=warnings,
        )
    warnings.append(
        f"the wallet client answered {outcome or 'nothing'}: {detail or 'no detail'}"
    )
    return _done(
        changed=False, message="the KDE wallet was not checked", warnings=warnings
    )
