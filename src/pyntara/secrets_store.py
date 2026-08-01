from __future__ import annotations

import importlib
import os
import select
import signal
import sys
import termios
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Protocol, cast

import yaml

_KDBX_SIGNATURE_PREFIX = b"\x03\xd9\xa2\x9a"
_PASSWORD_ATTEMPTS = 3
_KDF_TIMEOUT_SEC: float = 30.0
_PRODUCTION_PROMPT_TIMEOUT_SEC: float = 11.0


class _KeepassEntry(Protocol):
    title: str | None
    password: str | None
    group: object | None


class _KeepassDatabase(Protocol):
    entries: list[_KeepassEntry]


class _PyKeePassModule(Protocol):
    CredentialsError: type[Exception]

    def open(self, vault_path: Path, *, password: str) -> _KeepassDatabase: ...


class VaultSecretsStore:
    """Secrets store backed by KeePass vault files.

    Resolution order:
      1. PYNTARA_VAULT_PASSWORD env var overrides everything.
      2. <vault_path>.password file (e.g. secrets/production.password).
      3. Interactive prompt with 11s timeout (production vault only).

    If production vault fails to open, falls back to default vault.
    """

    def __init__(
        self,
        *,
        default_vault: Path,
        production_vault: Path,
        use_production: bool,
        password_provider: Callable[[Path], str] | None = None,
        kdf_timeout_sec: float | None = None,
    ) -> None:
        self._default_vault = default_vault
        self._production_vault = production_vault
        self._use_production = use_production
        self._password_provider = (
            _default_password_provider if password_provider is None else password_provider
        )
        self._kdf_timeout_sec = _KDF_TIMEOUT_SEC if kdf_timeout_sec is None else kdf_timeout_sec
        self._values: dict[str, str] = {}
        self._loaded = False

    def load(self) -> None:
        """Load secrets from the appropriate vault.

        Tries production vault first if selected, with fallback to default.
        """
        if self._use_production:
            self._values = self._try_load_vault(self._production_vault)
            if self._values is not None:
                self._loaded = True
                return
            # Production failed — fall back to default
            print(
                "Falling back to default secrets vault.",
                file=sys.stderr,
            )

        self._values = self._try_load_vault(self._default_vault)
        if self._values is None:
            raise RuntimeError(
                "Failed to open default KeePass vault: password attempts exhausted."
            )
        self._loaded = True

    def _try_load_vault(self, vault_path: Path) -> dict[str, str] | None:
        """Try to load a vault. Returns None on failure (wrong password)."""
        if not vault_path.exists():
            return None
        if not _is_keepass_file(vault_path):
            return _load_yaml_values(vault_path)

        password = self._resolve_password(vault_path)
        if password is None:
            return None

        try:
            return _load_keepass_values(
                vault_path=vault_path,
                password=password,
                kdf_timeout_sec=self._kdf_timeout_sec,
            )
        except RuntimeError as exc:
            if "password attempts exhausted" in str(exc):
                return None
            raise

    def _resolve_password(self, vault_path: Path) -> str | None:
        """Resolve the password for a vault.

        Priority: env var > password file > password_provider callback > interactive prompt.
        Returns None if no password can be resolved.
        """
        # 1. Env var override
        env_password = os.environ.get("PYNTARA_VAULT_PASSWORD")
        if env_password is not None:
            return env_password

        # 2. Password file
        password_file = vault_path.with_suffix(".password")
        if password_file.exists():
            return password_file.read_text(encoding="utf-8").strip()

        # 3. Custom password provider (used by tests)
        if self._password_provider is not _default_password_provider:
            return self._password_provider(vault_path)

        # 4. Interactive prompt with timeout (production vault only)
        is_production = vault_path == self._production_vault
        if is_production and _interactive_prompt_available():
            return _prompt_production_password_with_timeout(
                vault_path=vault_path,
                timeout_sec=_PRODUCTION_PROMPT_TIMEOUT_SEC,
            )

        return None

    def get(self, key: str, default: str | None = None) -> str | None:
        if not self._loaded:
            raise RuntimeError("Secrets store must be loaded before use.")
        return self._values.get(key, default)


def _is_keepass_file(vault_path: Path) -> bool:
    with vault_path.open("rb") as vault_file:
        return vault_file.read(4) == _KDBX_SIGNATURE_PREFIX


def _load_yaml_values(vault_path: Path) -> dict[str, str]:
    try:
        with vault_path.open("r", encoding="utf-8") as vault_file:
            parsed = yaml.safe_load(vault_file) or {}
    except UnicodeDecodeError as decode_error:
        raise ValueError(
            f"Vault file {vault_path} is not UTF-8 YAML and is not a recognized KeePass file."
        ) from decode_error

    if not isinstance(parsed, dict):
        raise ValueError(f"Vault file {vault_path} must contain a mapping.")
    return {str(key): str(value) for key, value in parsed.items()}


def _load_keepass_values(
    *,
    vault_path: Path,
    password: str,
    kdf_timeout_sec: float = _KDF_TIMEOUT_SEC,
) -> dict[str, str]:
    """Open a KeePass vault with a known password and extract all entries."""
    pykeepass = _import_pykeepass()
    database = _open_keepass_database(
        pykeepass=pykeepass,
        vault_path=vault_path,
        password=password,
        kdf_timeout_sec=kdf_timeout_sec,
    )
    values: dict[str, str] = {}
    for entry in database.entries:
        title = (entry.title or "").strip()
        if title == "":
            continue
        key_parts = [*_group_path(entry.group), title]
        values[".".join(key_parts)] = entry.password or ""
    return values


def _open_keepass_database(
    *,
    pykeepass: _PyKeePassModule,
    vault_path: Path,
    password: str,
    kdf_timeout_sec: float = _KDF_TIMEOUT_SEC,
) -> _KeepassDatabase:
    """Open a KeePass database with retries on wrong password."""
    for attempt in range(1, _PASSWORD_ATTEMPTS + 1):
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(pykeepass.open, vault_path, password=password)
            try:
                return future.result(timeout=kdf_timeout_sec)
            except TimeoutError:
                future.cancel()
                raise RuntimeError(
                    f"KeePass KDF timed out after {kdf_timeout_sec}s "
                    f"for {vault_path.name}. Possible causes: wrong password, "
                    f"corrupted vault, or excessive KDF parameters."
                ) from None
        except pykeepass.CredentialsError:
            attempts_left = _PASSWORD_ATTEMPTS - attempt
            if attempts_left > 0:
                print(
                    f"Wrong KeePass password for {vault_path.name}. Attempts left: {attempts_left}",
                    file=sys.stderr,
                )
            continue
        finally:
            pool.shutdown(wait=False)
    raise RuntimeError("Failed to open KeePass vault: password attempts exhausted.")


def _group_path(group: object | None) -> list[str]:
    names: list[str] = []
    current = group
    while current is not None:
        typed = current
        maybe_name = getattr(typed, "name", None)
        name = maybe_name.strip() if isinstance(maybe_name, str) else ""
        if name and name.lower() != "root":
            names.append(name)
        current = getattr(typed, "parentgroup", None)
    names.reverse()
    return names


def _default_password_provider(vault_path: Path) -> str:
    """Default password provider (sentinel value for VaultSecretsStore).

    This function is used as a sentinel to detect whether a custom
    password_provider was passed to VaultSecretsStore. It is not
    actually called — passwords are resolved via _resolve_password.
    """
    raise RuntimeError(
        "Default password provider should not be called directly. "
        "Passwords are resolved via _resolve_password."
    )


def _interactive_prompt_available() -> bool:
    """Check if hidden password input is possible via /dev/tty."""
    try:
        with Path("/dev/tty").open("rb"):
            return True
    except OSError:
        pass
    return sys.stdin.isatty()


def _prompt_production_password_with_timeout(
    *,
    vault_path: Path,
    timeout_sec: float = _PRODUCTION_PROMPT_TIMEOUT_SEC,
) -> str | None:
    """Prompt for production vault password with a countdown timeout.

    Shows a countdown on the terminal. If the user presses a key before
    the timeout, hidden input mode begins. If the timeout expires, returns
    None (caller should fall back to default vault).

    Returns the password string, or None on timeout.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        fd = -1

    if fd < 0 and sys.stdin.isatty():
        fd = sys.stdin.fileno()

    if fd < 0:
        return None

    try:
        old_settings = termios.tcgetattr(fd)
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~(termios.ECHO | termios.ECHONL)

        old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            try:
                os.tcsetpgrp(fd, os.getpgrp())
            except OSError:
                pass

            deadline = time.monotonic() + timeout_sec
            rendered_len = 0

            # Countdown phase: wait for first keypress or timeout
            while True:
                remaining = max(0.0, deadline - time.monotonic())
                remaining_sec = int(remaining + 0.999)
                line = (
                    f"Enter production vault password "
                    f"(auto-fallback to default in {remaining_sec:02d}s): "
                )
                padding = " " * max(0, rendered_len - len(line))
                os.write(fd, f"\r{line}{padding}".encode("utf-8"))
                rendered_len = len(line)

                if remaining <= 0:
                    os.write(fd, b"\n")
                    return None

                ready, _, _ = select.select([fd], [], [], min(0.1, remaining))
                if ready:
                    # User pressed a key — switch to hidden input mode
                    termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)
                    # Clear the countdown line
                    os.write(fd, b"\r" + b" " * rendered_len + b"\r")
                    os.write(fd, b"Production vault password: ")
                    password_bytes = _read_line_from_fd(fd)
                    password = password_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                    print(file=sys.stderr)
                    return password
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            signal.signal(signal.SIGTTOU, old_sigttou)
    except (termios.error, OSError, ValueError):
        return None
    finally:
        if fd != sys.stdin.fileno():
            try:
                os.close(fd)
            except OSError:
                pass

    return None


def _read_password_hidden(prompt: str) -> str | None:
    """Read a password from the terminal with echo disabled.

    Tries /dev/tty first, then sys.stdin if it is a TTY.
    Uses termios to disable echo with foreground process group management.
    """
    fd = -1
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        fd = -1

    if fd < 0 and sys.stdin.isatty():
        fd = sys.stdin.fileno()

    if fd < 0:
        return None

    try:
        old_settings = termios.tcgetattr(fd)
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~(termios.ECHO | termios.ECHONL)

        old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            try:
                os.tcsetpgrp(fd, os.getpgrp())
            except OSError:
                pass
            termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)
            os.write(fd, prompt.encode("utf-8"))
            password_bytes = _read_line_from_fd(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            signal.signal(signal.SIGTTOU, old_sigttou)
    except (termios.error, OSError, ValueError):
        return None
    finally:
        if fd != sys.stdin.fileno():
            try:
                os.close(fd)
            except OSError:
                pass

    password = password_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
    print(file=sys.stderr)
    return password


def _read_line_from_fd(fd: int) -> bytes:
    """Read one line from a file descriptor, byte by byte."""
    result = bytearray()
    while True:
        try:
            ch = os.read(fd, 1)
        except OSError:
            break
        if not ch:
            break
        if ch in (b"\n", b"\r"):
            if ch == b"\r":
                try:
                    next_ch = os.read(fd, 1)
                    if next_ch and next_ch != b"\n":
                        result.extend(next_ch)
                except OSError:
                    pass
            break
        result.extend(ch)
    return bytes(result)


def _import_pykeepass() -> _PyKeePassModule:
    pykeepass_module = importlib.import_module("pykeepass")
    exceptions_module = importlib.import_module("pykeepass.exceptions")
    pykeepass_ctor = cast(Callable[..., object], pykeepass_module.PyKeePass)
    credentials_error = cast(type[Exception], exceptions_module.CredentialsError)

    class _Module:
        CredentialsError = credentials_error

        @staticmethod
        def open(vault_path: Path, *, password: str) -> _KeepassDatabase:
            instance = pykeepass_ctor(str(vault_path), password=password)
            return cast(_KeepassDatabase, instance)

    return _Module()
