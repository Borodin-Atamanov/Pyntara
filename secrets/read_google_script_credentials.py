#!/usr/bin/env python3
"""Read the Google script credentials of both vaults and render the web app file.

The deploy script for the System Metrics Google Drive web app needs the
script ID of the Apps Script project, the deployment ID whose URL stays
stable across redeploys and the auth keys the web app accepts. The values
live in the google_script_key entry of the vault databases, whose title and
URL pattern come from system_metrics_setup.google_script_key_entry_title
and system_metrics_setup.google_script_deployment_url_regex in the
repository config.toml, the same single source of truth the deployed
service uses: the username field holds the script ID, the url field holds
the web app endpoint from which the deployment ID is extracted with the
configured URL pattern, the password field holds the auth key of that
vault.

The production vault supplies the script ID and the deployment ID, because
its project owns the deployed URL. Every vault supplies one auth key: a
machine provisioned from the default vault sends its telemetry with the
default key, so a deployed web app that accepted one key would silently
drop those machines. The script therefore renders the web app file from the
repository template task_data/system_metrics_setup/google_drive_script.js,
whose ALLOWED_KEYS assignment line becomes the JSON array of the keys, and
prints the two IDs as key=value lines for the deploy script to
consume. The keys never leave this process: they are written into the
rendered file and never printed, so a caller cannot copy a secret by
accident.

One vault opens with the password of the PYNTARA_VAULT_PASSWORD environment
variable when that value opens it, otherwise with the .password file next to
it, so one environment value never has to match two vaults. Both vaults must
open and both entries must carry a key. Every failure, a vault that does not
exist, no password at all, a missing entry, an empty username, a url that is
not a web app URL and a template without the placeholder, exits 1 with an
error on stderr and prints nothing on stdout, so a caller can never consume
a half-filled value. This is a standalone maintenance script, like
secrets/regenerate_vault_by_config.py, invoked with the project interpreter.

Usage:
  read_google_script_credentials.py TEMPLATE_PATH OUTPUT_PATH
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

# The script lives in secrets/, so the repository root is one level up and
# the project virtualenv interpreter sits at its well-known location.
REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# Placeholder of the web app template that the deploy step replaces with the
# JSON array of the auth keys, the template being
# task_data/system_metrics_setup/google_drive_script.js. The file does not work
# without the substitution, because the placeholder is not a defined name in
# Apps Script.
PLACEHOLDER = "__GOOGLE_SCRIPT_KEYS__"

# The whole line the render replaces, so the placeholder stays a name in the
# prose of the template and the keys land in exactly one line of the rendered
# file.
ALLOWED_KEYS_LINE = f"const ALLOWED_KEYS = {PLACEHOLDER};"

# pykeepass is installed into the project virtualenv, not into the system
# python; when the script is invoked directly the kernel starts the system
# python3 and the import fails, so the script re-executes itself with the
# venv interpreter, the same pattern as regenerate_vault_by_config.py.
try:
    from pykeepass import Entry, PyKeePass
    from pykeepass.exceptions import CredentialsError
except ModuleNotFoundError:
    if __name__ == "__main__":
        if os.environ.get("PYNTARA_REEXEC") != "1" and VENV_PYTHON.is_file():
            os.environ["PYNTARA_REEXEC"] = "1"
            os.execv(
                str(VENV_PYTHON),
                [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            )
        print(
            "pykeepass is not available: install the project dependencies "
            "(uv sync) and run the script with the project interpreter, "
            f"for example {VENV_PYTHON} "
            "secrets/read_google_script_credentials.py",
            file=sys.stderr,
        )
        sys.exit(1)
    raise

# The declared values of the metrics section live in the package, the same
# source the deployed service reads; the script never re-implements the
# config reading.
from pyntara.values import system_metrics_setup as values


class ScriptError(RuntimeError):
    """Fatal problem with the config or the vault databases."""


def _google_script_config() -> tuple[str, re.Pattern[str]]:
    """The entry title and the compiled deployment URL pattern.

    GOOGLE_SCRIPT_KEY_ENTRY_TITLE names the vault entry that carries the
    Google script credentials, and GOOGLE_SCRIPT_DEPLOYMENT_URL_REGEX is
    the regular expression whose single capture group yields the
    deployment ID; both are declared values of
    pyntara.values.system_metrics_setup, the same source the deployed
    service reads, so this tool and the service can never disagree. A
    regex that does not compile or one without exactly one capture group
    is a loud error, never a silent hardcoded fallback.
    """

    title = values.GOOGLE_SCRIPT_KEY_ENTRY_TITLE
    pattern = values.GOOGLE_SCRIPT_DEPLOYMENT_URL_REGEX
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ScriptError(
            "GOOGLE_SCRIPT_DEPLOYMENT_URL_REGEX is not "
            f"a valid regular expression: {exc}"
        ) from None
    if compiled.groups != 1:
        raise ScriptError(
            "GOOGLE_SCRIPT_DEPLOYMENT_URL_REGEX must "
            "contain exactly one capture group"
        )
    return title, compiled


def get_password_candidates(
    vault_path: Path, environ: Mapping[str, str]
) -> tuple[str, ...]:
    """The passwords to try for one vault, in the order they are tried.

    The value of the PYNTARA_VAULT_PASSWORD environment variable comes
    first, then the content of the .password file next to the vault with
    the same name and the .password extension, trimmed of surrounding
    whitespace. Both are candidates and not one answer, so an environment
    value meant for one vault never hides the password file of the other.
    No interactive prompt: the deploy script runs non-interactively. An
    existing but unreadable password file is a fatal error, so a broken
    setup fails loudly.
    """

    candidates: list[str] = []
    env_password = (environ.get("PYNTARA_VAULT_PASSWORD") or "").strip()
    if env_password:
        candidates.append(env_password)
    password_file = vault_path.with_suffix(".password")
    if password_file.exists():
        try:
            file_password = password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ScriptError(
                f"cannot read password file {password_file}: {exc}"
            ) from exc
        if file_password and file_password not in candidates:
            candidates.append(file_password)
    return tuple(candidates)


def open_vault(
    name: str, vault_path: Path, environ: Mapping[str, str]
) -> PyKeePass:
    """Open one vault with the first password that opens it.

    A wrong password is not an error of the vault, it is the turn of the
    next candidate. A missing file, no candidate at all, a vault that no
    candidate opens and any other open failure raise ScriptError, because
    the deploy needs both vaults and never one of them.
    """

    if not vault_path.is_file():
        raise ScriptError(f"the {name} vault does not exist: {vault_path}")
    candidates = get_password_candidates(vault_path, environ)
    if not candidates:
        raise ScriptError(
            f"no password for the {name} vault {vault_path}: set "
            "PYNTARA_VAULT_PASSWORD or create "
            f"{vault_path.with_suffix('.password')}"
        )
    for password in candidates:
        try:
            return PyKeePass(str(vault_path), password=password)
        except CredentialsError:
            continue
        except Exception as exc:
            raise ScriptError(
                f"cannot open the {name} vault {vault_path}: {exc}"
            ) from exc
    raise ScriptError(
        f"cannot open the {name} vault {vault_path} with the provided "
        "passwords"
    )


def get_entry(
    kp: PyKeePass, title: str, name: str, vault_path: Path
) -> Entry:
    """The entry under the configured title, or a ScriptError naming it."""

    entry = kp.find_entries(
        title=title, group=kp.root_group, recursive=False, first=True
    )
    if entry is None:
        raise ScriptError(
            f"entry {title!r} not found in the {name} vault {vault_path}"
        )
    return entry


def deployment_id_from_url(url: str) -> str:
    """The deployment ID embedded in a web app URL.

    The URL must match the deployment URL pattern from the config, whose
    single capture group yields the ID; any other shape raises
    ScriptError, so a wrong url fails loudly instead of deploying to an
    unexpected place.
    """

    _, pattern = _google_script_config()
    match = pattern.match(url.strip())
    if match is None:
        raise ScriptError(f"url is not a web app URL: {url!r}")
    return match.group(1)


def _source_vault_paths() -> tuple[Path, Path]:
    """Production and default vault paths under the repository secrets dir."""

    secrets_dir = REPO_ROOT / "secrets"
    return secrets_dir / "production.vault", secrets_dir / "default.vault"


class DeployCredentials(NamedTuple):
    """What a deploy of the web app needs: the project and the auth keys."""

    script_id: str
    deployment_id: str
    auth_keys: tuple[str, ...]


def read_deploy_credentials(environ: Mapping[str, str]) -> DeployCredentials:
    """The project identity and the auth key of every vault.

    The entry title and the deployment URL pattern come from the
    system_metrics_setup table of the repository config.toml. The production
    vault supplies the script ID and the deployment ID, because its project
    owns the deployed URL; every vault supplies one auth key, so the deployed
    web app accepts the telemetry of the machines provisioned from either
    vault. Both vaults must open and both entries must carry a key: a deploy
    with one key would silently drop the machines of the other vault, so a
    half-filled result is an error and never a fallback.
    """

    title, _ = _google_script_config()
    production_path, default_path = _source_vault_paths()
    production = open_vault("production", production_path, environ)
    default = open_vault("default", default_path, environ)
    production_entry = get_entry(
        production, title, "production", production_path
    )
    default_entry = get_entry(default, title, "default", default_path)

    script_id = (production_entry.username or "").strip()
    if not script_id:
        raise ScriptError(
            f"entry {title!r} in the production vault has an empty username; "
            "fill the Apps Script project script ID there"
        )
    deployment_id = deployment_id_from_url(production_entry.url or "")

    auth_keys: list[str] = []
    for name, entry in (
        ("production", production_entry),
        ("default", default_entry),
    ):
        auth_key = (entry.password or "").strip()
        if not auth_key:
            raise ScriptError(
                f"entry {title!r} in the {name} vault has an empty password; "
                "fill the auth key there"
            )
        if auth_key not in auth_keys:
            auth_keys.append(auth_key)
    return DeployCredentials(
        script_id=script_id,
        deployment_id=deployment_id,
        auth_keys=tuple(auth_keys),
    )


def render_web_app_file(
    template_path: Path, auth_keys: tuple[str, ...], output_path: Path
) -> None:
    """Write the deployable web app file with the assignment line replaced.

    The line that assigns the placeholder becomes the assignment of the JSON
    array of the keys, which is a valid JavaScript array literal, so the
    rendered file carries every key the web app accepts and the prose of the
    template keeps the placeholder as a name. A missing template, a template
    without that line and an unwritable output are ScriptError.
    """

    if not auth_keys:
        raise ScriptError(
            "no auth keys to render: the web app would accept nothing"
        )
    try:
        text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptError(
            f"cannot read template {template_path}: {exc}"
        ) from exc
    if ALLOWED_KEYS_LINE not in text:
        raise ScriptError(
            f"the line {ALLOWED_KEYS_LINE!r} not found in {template_path}"
        )
    try:
        output_path.write_text(
            text.replace(
                ALLOWED_KEYS_LINE,
                f"const ALLOWED_KEYS = {json.dumps(list(auth_keys))};",
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ScriptError(f"cannot write {output_path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    """Script entry point; returns the process exit code.

    Two arguments: the template of the web app file and the path it is
    rendered into. The two IDs go to stdout for the deploy script to
    consume, the auth keys go into the rendered file only.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print(
            "usage: read_google_script_credentials.py "
            "TEMPLATE_PATH OUTPUT_PATH",
            file=sys.stderr,
        )
        return 2
    try:
        credentials = read_deploy_credentials(os.environ)
        render_web_app_file(
            Path(arguments[0]), credentials.auth_keys, Path(arguments[1])
        )
        sys.stdout.write(
            f"script_id={credentials.script_id}\n"
            f"deployment_id={credentials.deployment_id}\n"
        )
        return 0
    except ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
