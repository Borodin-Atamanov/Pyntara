from __future__ import annotations

import importlib
import os
import sys
import termios
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Protocol, cast

import yaml

_KDBX_SIGNATURE_PREFIX = b"\x03\xd9\xa2\x9a"
_PASSWORD_ATTEMPTS = 3
_KDF_TIMEOUT_SEC: float = 30.0


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
        vault_path = self._production_vault if self._use_production else self._default_vault
        self._values = (
            _load_keepass_values(
                vault_path=vault_path,
                password_provider=self._password_provider,
                kdf_timeout_sec=self._kdf_timeout_sec,
            )
            if _is_keepass_file(vault_path)
            else _load_yaml_values(vault_path)
        )
        self._loaded = True

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
    password_provider: Callable[[Path], str],
    kdf_timeout_sec: float = _KDF_TIMEOUT_SEC,
) -> dict[str, str]:
    pykeepass = _import_pykeepass()
    database = _open_keepass_database(
        pykeepass=pykeepass,
        vault_path=vault_path,
        password_provider=password_provider,
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
    password_provider: Callable[[Path], str],
    kdf_timeout_sec: float = _KDF_TIMEOUT_SEC,
) -> _KeepassDatabase:
    for attempt in range(1, _PASSWORD_ATTEMPTS + 1):
        password = _password_from_env_or_prompt(
            vault_path=vault_path, password_provider=password_provider
        )
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


def _password_from_env_or_prompt(
    *,
    vault_path: Path,
    password_provider: Callable[[Path], str],
) -> str:
    password_from_env = os.environ.get("PYNTARA_VAULT_PASSWORD")
    if password_from_env is not None:
        return password_from_env
    if password_provider is _default_password_provider and not _interactive_prompt_available():
        raise RuntimeError(
            "KeePass password is required, but interactive prompt is unavailable. "
            "Set PYNTARA_VAULT_PASSWORD for non-interactive bootstrap."
        )
    return password_provider(vault_path)


def _default_password_provider(vault_path: Path) -> str:
    """Read a password from the terminal with echo disabled.

    Bypasses getpass.getpass() because process managers like 'uv run'
    may create subprocesses where getpass cannot properly access /dev/tty.
    Uses termios directly on /dev/tty for reliable hidden input.
    """
    print(
        f"KeePass password is required to open {vault_path.name}. Input is hidden.",
        file=sys.stderr,
    )
    prompt = f"KeePass password for {vault_path.name}: "
    password = _read_password_hidden(prompt)
    if password is None:
        raise RuntimeError(
            "KeePass password is required, but interactive prompt is unavailable. "
            "Set PYNTARA_VAULT_PASSWORD for non-interactive bootstrap."
        )
    return password


def _interactive_prompt_available() -> bool:
    """Check if hidden password input is possible.

    Attempts to open /dev/tty — this is what _read_password_hidden
    uses for hidden input. Fall back to sys.stdin if it's a real TTY.
    """
    try:
        with Path("/dev/tty").open("rb"):
            return True
    except OSError:
        pass
    return sys.stdin.isatty()


def _read_password_hidden(prompt: str) -> str | None:
    """Read a password from the terminal with echo disabled.

    Tries fd 3 (/dev/tty opened by i.sh), then /dev/tty directly,
    then sys.stdin if it is a TTY. Uses termios to disable echo.

    fd 3 is preferred because it is the actual /dev/tty fd opened by
    the parent process (i.sh's exec 3<"/dev/tty"), which is inherited
    by the uv run subprocess. This bypasses any pipe that uv may have
    inserted as stdin.
    """
    fd = -1

    # Try fd 3 first — opened by i.sh: exec 3<"${PYNTARA_CLI_STDIN_PATH}"
    # This fd is inherited by the uv run subprocess and points to the
    # real terminal, bypassing any pipe uv may have created for stdin.
    try:
        if os.isatty(3):
            fd = 3
    except (OSError, ValueError):
        pass

    if fd < 0:
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
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)
            try:
                os.write(fd, prompt.encode("utf-8"))
                password_bytes = _read_line_from_fd(fd)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return None
    except (termios.error, OSError):
        return None
    finally:
        if fd >= 3 and fd != sys.stdin.fileno():
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
