#!/usr/bin/env python3
"""Read Google script credentials from the vault databases.

The deploy script for the System Metrics Google Drive web app needs the
script ID of the Apps Script project, the deployment ID whose URL stays
stable across redeploys and the shared auth key. All three live in the
google_script_key entry of the vault databases, whose title and URL
pattern come from system_metrics_setup.google_script_key_entry_title and
system_metrics_setup.google_script_deployment_url_regex in the
repository config.toml, the same single source of truth the deployed
service uses: the username field holds the script ID, the url field
holds the web app endpoint from which the deployment ID is extracted
with the configured URL pattern, the password field holds the auth key
that the deploy script substitutes into the script template. This
maintenance script prints the values as key=value lines for the deploy
script to consume; it is a standalone script like
secrets/regenerate_vault_by_config.py and is invoked with the project
interpreter.

The production vault is tried first, then the default vault, both with the
vault password from the PYNTARA_VAULT_PASSWORD environment variable or the
.password file next to the vault; PYNTARA_VAULT_SOURCE (production or
default) forces one source. The first vault that opens is authoritative:
a missing entry, an empty username or a url that is not a web app URL are
errors, never a reason to fall back to the other vault. When no vault
opens, the script exits 1 with an error on stderr and prints nothing on
stdout, so a caller can never consume a half-filled value.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

# The script lives in secrets/, so the repository root is one level up and
# the project virtualenv interpreter sits at its well-known location.
REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# pykeepass is installed into the project virtualenv, not into the system
# python; when the script is invoked directly the kernel starts the system
# python3 and the import fails, so the script re-executes itself with the
# venv interpreter, the same pattern as regenerate_vault_by_config.py.
try:
    from pykeepass import PyKeePass
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


class ScriptError(RuntimeError):
    """Fatal problem with the config or the vault databases."""


def _google_script_config() -> tuple[str, re.Pattern[str]]:
    """The entry title and the compiled deployment URL pattern from config.

    system_metrics_setup.google_script_key_entry_title names the vault
    entry that carries the Google script credentials, and
    system_metrics_setup.google_script_deployment_url_regex is the
    regular expression whose single capture group yields the deployment
    ID; the deployed service reads the same keys from the system config.
    A missing config, a missing or non-string key, a regex that does not
    compile or one without exactly one capture group is a loud error,
    never a silent hardcoded fallback.
    """

    config_path = REPO_ROOT / "config.toml"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ScriptError(f"config file not found: {config_path}") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScriptError(
            f"cannot read config file {config_path}: {exc}"
        ) from exc
    try:
        section = data["system_metrics_setup"]
        title = section["google_script_key_entry_title"]
        pattern = section["google_script_deployment_url_regex"]
    except KeyError:
        raise ScriptError(
            "system_metrics_setup.google_script_key_entry_title and "
            "google_script_deployment_url_regex must be present in "
            f"{config_path}"
        ) from None
    if not isinstance(title, str) or not title:
        raise ScriptError(
            "system_metrics_setup.google_script_key_entry_title must be a "
            "non-empty string"
        )
    if not isinstance(pattern, str) or not pattern:
        raise ScriptError(
            "system_metrics_setup.google_script_deployment_url_regex must "
            "be a non-empty string"
        )
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ScriptError(
            "system_metrics_setup.google_script_deployment_url_regex is not "
            f"a valid regular expression: {exc}"
        ) from None
    if compiled.groups != 1:
        raise ScriptError(
            "system_metrics_setup.google_script_deployment_url_regex must "
            "contain exactly one capture group"
        )
    return title, compiled


def resolve_vault_password(
    vault_path: Path, environ: Mapping[str, str]
) -> str | None:
    """The vault password from the environment or the .password file.

    The environment variable wins; otherwise the file next to the vault
    with the same name and the .password extension is read and trimmed of
    surrounding whitespace. No interactive prompt: the deploy script runs
    non-interactively. An existing but unreadable password file is a fatal
    error, so a broken setup fails loudly.
    """

    env_password = environ.get("PYNTARA_VAULT_PASSWORD")
    if env_password is not None:
        stripped = env_password.strip()
        if stripped:
            return stripped
    password_file = vault_path.with_suffix(".password")
    if not password_file.exists():
        return None
    try:
        file_password = password_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ScriptError(
            f"cannot read password file {password_file}: {exc}"
        ) from exc
    return file_password or None


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


def read_credentials(environ: Mapping[str, str]) -> str:
    """script_id, deployment_id and script_key lines from the first vault.

    The entry title and the deployment URL pattern come from the
    system_metrics_setup table of the repository config.toml. Production
    is tried first, then default; PYNTARA_VAULT_SOURCE (production or
    default) forces one source. The first vault that opens is
    authoritative: a missing entry, an empty username, an empty password
    or a url that is not a web app URL are errors, not reasons to fall
    back. When no vault opens, ScriptError is raised.
    """

    title, _ = _google_script_config()
    production_path, default_path = _source_vault_paths()
    source = environ.get("PYNTARA_VAULT_SOURCE", "")
    if source not in ("", "production", "default"):
        raise ScriptError(
            "PYNTARA_VAULT_SOURCE must be production or default, got "
            f"{source!r}"
        )
    if source == "production":
        candidates: list[tuple[str, Path]] = [("production", production_path)]
    elif source == "default":
        candidates = [("default", default_path)]
    else:
        candidates = [
            ("production", production_path),
            ("default", default_path),
        ]

    for name, path in candidates:
        if not path.is_file():
            continue
        password = resolve_vault_password(path, environ)
        if password is None:
            continue
        try:
            kp = PyKeePass(str(path), password=password)
        except CredentialsError:
            continue
        except Exception as exc:  # noqa: BLE001 - any open failure is fatal
            raise ScriptError(
                f"cannot open {name} vault {path}: {exc}"
            ) from exc
        entry = kp.find_entries(
            title=title, group=kp.root_group, recursive=False, first=True
        )
        if entry is None:
            raise ScriptError(
                f"entry {title!r} not found in the {name} vault {path}"
            )
        script_id = (entry.username or "").strip()
        if not script_id:
            raise ScriptError(
                f"entry {title!r} in the {name} vault has an empty "
                "username; fill the Apps Script project script ID there"
            )
        script_key = (entry.password or "").strip()
        if not script_key:
            raise ScriptError(
                f"entry {title!r} in the {name} vault has an empty "
                "password; fill the shared auth key there"
            )
        deployment_id = deployment_id_from_url(entry.url or "")
        return (
            f"script_id={script_id}\n"
            f"deployment_id={deployment_id}\n"
            f"script_key={script_key}\n"
        )
    raise ScriptError(
        "cannot open any vault: neither production nor default opened with "
        "the provided password"
    )


def main() -> int:
    """Script entry point; returns the process exit code."""

    try:
        sys.stdout.write(read_credentials(os.environ))
        return 0
    except ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
