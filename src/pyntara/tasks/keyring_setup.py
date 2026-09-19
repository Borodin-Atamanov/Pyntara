"""Task keyring_setup: make the login keyring open without a password.

A machine that logs in automatically never types the account password, and
the PAM service of that path (/etc/pam.d/sddm-autologin) carries no keyring
module, so neither gnome-keyring nor KWallet ever receives a password at
login. The login collection of the Secret Service is then protected by a
password nobody knows, and the first program that stores or reads a secret,
Chrome, VS Code or a KDE program, opens a dialog that asks for it. The user
cannot answer it, so the dialog comes back at every start.

The task gives that collection an empty master password. A machine under full
disk encryption can afford it: the collection stays a private file of the
user, and an empty password means it opens at once instead of asking. Two
states lead to the goal. A session whose default alias names no collection
yet gets the login collection created with the empty password. A session that
already has one is asked to open with the empty password, which is a call
without a prompt: an answer means the task is already done and nothing is
written, and a refusal means the collection keeps a real password, so it is
left untouched and reported as a warning, because replacing it would throw
away every secret inside it.

The client that speaks the protocol ships under task_data/ of the clone and
runs as the desktop user on the session bus of that user, because the secret
service belongs to the session and not to the root process of the run. The
names of the service and the outcome vocabulary come from the values module
of the section and are substituted into the client, so they stand in one
place. A session that cannot be found leaves the keyring alone and reports
it, so a machine without a desktop is never touched.
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

    The names of the secret service and the words of the answer protocol are
    values, so the client carries placeholders and every name reaches it from
    one place.
    """

    template = Template(script_path.read_text(encoding="utf-8"))
    return template.substitute(
        bus_name=values.BUS_NAME,
        service_object_path=values.SERVICE_OBJECT_PATH,
        service_interface_name=values.SERVICE_INTERFACE_NAME,
        internal_interface_name=values.INTERNAL_INTERFACE_NAME,
        label_property=values.LABEL_PROPERTY,
        login_collection_label=values.LOGIN_COLLECTION_LABEL,
        default_alias=values.DEFAULT_ALIAS,
        session_algorithm=values.SESSION_ALGORITHM,
        secret_content_type=values.SECRET_CONTENT_TYPE,
        outcome_key=values.OUTCOME_KEY,
        detail_key=values.DETAIL_KEY,
        outcome_created=values.OUTCOME_CREATED,
        outcome_already_passwordless=values.OUTCOME_ALREADY_PASSWORDLESS,
        outcome_protected=values.OUTCOME_PROTECTED,
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
        return {}, f"cannot read the keyring client {script_path}: {exc}"
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
            timeout=engine_values.PROCESS_CHECK_TIMEOUT_SECONDS,
            capture=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = trim_whitespace(exc.stderr or "")
        suffix = f": {detail}" if detail else ""
        return {}, f"cannot run the keyring client: {exc}{suffix}"
    except subprocess.TimeoutExpired as exc:
        return {}, f"cannot run the keyring client: {exc}"
    return _parse_answer(result.stdout), None


def _done(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """A completed task carrying its warnings."""

    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def task(ctx: Context) -> TaskResult:
    """Give the login keyring of the desktop user an empty master password."""

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        return TaskResult(
            success=True,
            changed=False,
            message="the keyring values are not declared",
            warnings=(f"the keyring values are not declared: {', '.join(absent)}",),
        )
    bus_env = _session_bus_env()
    if not bus_env:
        return TaskResult(
            success=True,
            changed=False,
            message="no live desktop session found",
            warnings=(
                (
                    "no live desktop session of "
                    f"{common_values.DESKTOP_USERNAME} was found, so the login "
                    "keyring was left alone and may still ask for a password"
                ),
            ),
        )
    warnings: list[str] = []
    _, installed, failures, package_warnings = install_missing_packages(
        ctx, values.PACKAGES
    )
    warnings.extend(package_warnings)
    warnings.extend(f"{name}: {reason}" for name, reason in failures)
    if installed:
        _log(f"installed: {', '.join(installed)}")
    script_path = (
        task_data_dir(ctx.repo_root, ctx.task_name) / values.CLIENT_SCRIPT_FILE_NAME
    )
    _log(f"checking the login keyring of {common_values.DESKTOP_USERNAME}")
    answer, error = _run_client(script_path=script_path, bus_env=bus_env)
    if error is not None:
        warnings.append(error)
        return _done(
            changed=False,
            message="the login keyring was not checked",
            warnings=warnings,
        )
    outcome = answer.get(values.OUTCOME_KEY, "")
    detail = answer.get(values.DETAIL_KEY, "")
    if outcome == values.OUTCOME_CREATED:
        _log(f"the login keyring opens without a password now: {detail}")
        return _done(
            changed=True,
            message=f"created the login keyring without a password: {detail}",
            warnings=warnings,
        )
    if outcome == values.OUTCOME_ALREADY_PASSWORDLESS:
        return _done(
            changed=False,
            message="the login keyring already opens without a password",
            warnings=warnings,
        )
    if outcome == values.OUTCOME_PROTECTED:
        warnings.append(
            "the login keyring is protected by a password and was left "
            f"untouched, so it may still ask for it: {detail}"
        )
        return _done(
            changed=False,
            message="the login keyring keeps its password",
            warnings=warnings,
        )
    warnings.append(
        f"the keyring client answered {outcome or 'nothing'}: {detail or 'no detail'}"
    )
    return _done(
        changed=False, message="the login keyring was not checked", warnings=warnings
    )
