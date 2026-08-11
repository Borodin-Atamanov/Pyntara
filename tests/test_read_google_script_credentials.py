"""Tests for the Google script credentials reader.

The standalone script secrets/read_google_script_credentials.py is loaded
as a module through importlib.util (the secrets directory is not a package)
and its functions are exercised against real KeePass databases in
temporary directories. REPO_ROOT is monkeypatched so the repository vaults
are never touched, and the environment is injected explicitly through the
function arguments.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pykeepass import PyKeePass, create_database
from pykeepass.exceptions import CredentialsError

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "secrets"
    / "read_google_script_credentials.py"
)

PRODUCTION_PASSWORD = "production-secret"
DEFAULT_PASSWORD = "default-secret"

# The entry shape the deploy helper consumes; mirrors the real config entry.
GOOGLE_ENTRY = {
    "title": "google_script_key",
    "username": "test-script-id",
    "url": "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec",
    "password": "test-key",
    "notes": "Test credentials.",
}


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    """The script loaded as a module from its file location."""

    spec = importlib.util.spec_from_file_location(
        "read_google_script_credentials", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real environment must never leak into tests.
    monkeypatch.delenv("PYNTARA_VAULT_PASSWORD", raising=False)
    monkeypatch.delenv("PYNTARA_VAULT_SOURCE", raising=False)


def _make_vault(path: Path, password: str, entry: dict[str, str] | None) -> None:
    """Create a vault with an optional google_script_key entry in the root group."""

    path.parent.mkdir(parents=True, exist_ok=True)
    create_database(str(path), password=password)
    if entry is None:
        return
    kp = PyKeePass(str(path), password=password)
    kp.add_entry(
        kp.root_group,
        title=entry["title"],
        username=entry["username"],
        password=entry["password"],
        url=entry["url"],
        notes=entry["notes"],
    )
    kp.save()


def _point_at(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Point the script at temp vaults; return (production, default) paths."""

    production = tmp_path / "secrets" / "production.vault"
    default = tmp_path / "secrets" / "default.vault"
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    return production, default


def test_deployment_id_from_url_valid(gen: ModuleType) -> None:
    assert (
        gen.deployment_id_from_url(
            "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec"
        )
        == "AKfycbwEXAMPLE"
    )
    # The surrounding whitespace must not matter.
    assert (
        gen.deployment_id_from_url(
            "  https://script.google.com/macros/s/id_123/exec  "
        )
        == "id_123"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://example.com/macros/s/id/exec",
        "https://script.google.com/macros/s/",
        "https://script.google.com/macros/s/id",
        "https://script.google.com/macros/exec",
    ],
)
def test_deployment_id_from_url_rejects_other_shapes(
    gen: ModuleType, url: str
) -> None:
    # A url that is not the exact web app endpoint shape is a fatal error:
    # the deploy helper must never guess a deployment ID.
    with pytest.raises(gen.ScriptError):
        gen.deployment_id_from_url(url)


def test_reads_production_when_password_matches(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production opens with the given password and carries the entry: the
    # reader returns its values and never looks at the default vault.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, GOOGLE_ENTRY)
    output = gen.read_credentials({"PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD})
    assert output == "script_id=test-script-id\ndeployment_id=AKfycbwEXAMPLE\nscript_key=test-key\n"


def test_falls_back_to_default_when_production_does_not_open(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production does not open with the given password, so the default
    # vault is tried and its values are returned.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, GOOGLE_ENTRY)
    output = gen.read_credentials({"PYNTARA_VAULT_PASSWORD": DEFAULT_PASSWORD})
    assert output == "script_id=test-script-id\ndeployment_id=AKfycbwEXAMPLE\nscript_key=test-key\n"


def test_vault_source_forces_default(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PYNTARA_VAULT_SOURCE=default selects the default vault even when
    # production opens with the same password and would win by default.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    production_entry = dict(GOOGLE_ENTRY)
    production_entry["username"] = "production-script-id"
    _make_vault(production, PRODUCTION_PASSWORD, production_entry)
    _make_vault(default, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
    output = gen.read_credentials(
        {
            "PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD,
            "PYNTARA_VAULT_SOURCE": "default",
        }
    )
    assert output == "script_id=test-script-id\ndeployment_id=AKfycbwEXAMPLE\nscript_key=test-key\n"


def test_password_file_used_when_no_environment(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without PYNTARA_VAULT_PASSWORD the .password file next to the vault
    # supplies the password, trimmed of surrounding whitespace.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(default, DEFAULT_PASSWORD, GOOGLE_ENTRY)
    default.with_suffix(".password").write_text(
        f"  {DEFAULT_PASSWORD}  \n", encoding="utf-8"
    )
    output = gen.read_credentials({})
    assert output == "script_id=test-script-id\ndeployment_id=AKfycbwEXAMPLE\nscript_key=test-key\n"


def test_empty_username_is_an_error_not_a_fallback(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production opens but its entry has no username: the reader fails
    # loudly instead of silently reading the default vault.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(GOOGLE_ENTRY)
    entry["username"] = ""
    _make_vault(production, PRODUCTION_PASSWORD, entry)
    _make_vault(default, DEFAULT_PASSWORD, GOOGLE_ENTRY)
    with pytest.raises(gen.ScriptError, match="empty username"):
        gen.read_credentials({"PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD})


def test_empty_password_is_an_error_not_a_fallback(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production opens but its entry has no password: the reader fails
    # loudly instead of silently reading the default vault, because the
    # deploy script must never substitute a missing auth key.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(GOOGLE_ENTRY)
    entry["password"] = ""
    _make_vault(production, PRODUCTION_PASSWORD, entry)
    _make_vault(default, DEFAULT_PASSWORD, GOOGLE_ENTRY)
    with pytest.raises(gen.ScriptError, match="empty password"):
        gen.read_credentials({"PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD})


def test_missing_entry_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production opens but the google_script_key entry is absent: an error.
    production, _ = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, None)
    with pytest.raises(gen.ScriptError, match="not found"):
        gen.read_credentials({"PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD})


def test_invalid_url_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed url must never yield a guessed deployment ID.
    production, _ = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(GOOGLE_ENTRY)
    entry["url"] = "https://script.google.com/macros/s/"
    _make_vault(production, PRODUCTION_PASSWORD, entry)
    with pytest.raises(gen.ScriptError, match="not a web app URL"):
        gen.read_credentials({"PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD})


def test_invalid_vault_source_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, GOOGLE_ENTRY)
    with pytest.raises(gen.ScriptError, match="PYNTARA_VAULT_SOURCE"):
        gen.read_credentials({"PYNTARA_VAULT_SOURCE": "bogus"})


def test_no_vault_opens_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    production, _ = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
    with pytest.raises(gen.ScriptError, match="cannot open any vault"):
        gen.read_credentials({"PYNTARA_VAULT_PASSWORD": "wrong-password"})


def test_main_prints_credentials_and_exits_zero(
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # main() reads the real environment and writes the key=value lines to
    # stdout, the contract the deploy script parses.
    production, _ = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
    monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", PRODUCTION_PASSWORD)
    assert gen.main() == 0
    captured = capsys.readouterr()
    assert captured.out == "script_id=test-script-id\ndeployment_id=AKfycbwEXAMPLE\nscript_key=test-key\n"
    assert captured.err == ""


def test_main_error_exits_one_with_empty_stdout(
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A failing read prints nothing on stdout, so the deploy script can
    # never consume a partial value; the error goes to stderr.
    _point_at(gen, tmp_path, monkeypatch)
    monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", "wrong-password")
    assert gen.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_opens_with_pykeepass() -> None:
    # Sanity check that the created vault really opens with its password;
    # guards the fixture against silent password mistakes.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "vault.kdbx"
        _make_vault(path, PRODUCTION_PASSWORD, GOOGLE_ENTRY)
        kp = PyKeePass(str(path), password=PRODUCTION_PASSWORD)
        entry = kp.find_entries(
            title="google_script_key",
            group=kp.root_group,
            recursive=False,
            first=True,
        )
        assert entry is not None
        assert entry.username == "test-script-id"
        assert entry.url == "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec"
        assert entry.password == "test-key"
