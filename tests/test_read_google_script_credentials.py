"""Tests for the Google script deploy helper.

The standalone script secrets/read_google_script_credentials.py is loaded
as a module through importlib.util (the secrets directory is not a package)
and its functions are exercised against real KeePass databases in temporary
directories. REPO_ROOT is monkeypatched so the repository vaults and the
repository config are never touched; a config/ directory with the entry
title and the deployment URL pattern is written into the temporary root,
and the environment is injected explicitly through the function arguments.

Two rules carry most of the cases. The production vault alone supplies the
script ID and the deployment ID, because its project owns the deployed URL.
Every vault supplies an auth key, because the deployed web app must accept
the telemetry of the machines provisioned from either vault, and a deploy
that carried one key would silently drop the machines of the other.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest
from pykeepass import PyKeePass, create_database

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "secrets"
    / "read_google_script_credentials.py"
)
REPO_ROOT = SCRIPT_PATH.parents[1]

PRODUCTION_PASSWORD = "production-secret"
DEFAULT_PASSWORD = "default-secret"

# The entry shape the deploy helper consumes; mirrors the real config entry.
PRODUCTION_ENTRY = {
    "title": "google_script_key",
    "username": "production-script-id",
    "url": "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec",
    "password": "production-key",
    "notes": "Production credentials.",
}

DEFAULT_ENTRY = {
    "title": "google_script_key",
    "username": "default-script-id",
    "url": "https://script.google.com/macros/s/AKfycbwDEFAUL/exec",
    "password": "default-key",
    "notes": "Default credentials.",
}

# The deployment URL pattern that mirrors the real config value.
DEPLOYMENT_PATTERN = r"^https://script\.google\.com/macros/s/([A-Za-z0-9_-]+)/exec$"

TEMPLATE_TEXT = "const ALLOWED_KEYS = __GOOGLE_SCRIPT_KEYS__;\n"

IDENTIFIER_LINES = "script_id=production-script-id\ndeployment_id=AKfycbwEXAMPLE\n"


def _write_config(
    tmp_path: Path,
    *,
    title: str = "google_script_key",
    pattern: str = DEPLOYMENT_PATTERN,
) -> None:
    """Write a config/system_metrics_setup.toml with the Google keys."""

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "system_metrics_setup.toml").write_text(
        "[system_metrics_setup]\n"
        f'google_script_key_entry_title = "{title}"\n'
        f"google_script_deployment_url_regex = '{pattern}'\n",
        encoding="utf-8",
    )


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
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    production_password_file: bool = True,
    default_password_file: bool = True,
) -> tuple[Path, Path]:
    """Point the script at temp vaults and config; return (production, default).

    The .password files next to the vaults are the normal way a deploy opens
    both vaults without an environment value, so they exist unless a test
    removes one on purpose to exercise the password candidates.
    """

    production = tmp_path / "secrets" / "production.vault"
    default = tmp_path / "secrets" / "default.vault"
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    _write_config(tmp_path)
    # The password files are written before the vaults exist, so the
    # directory has to be there first.
    production.parent.mkdir(parents=True, exist_ok=True)
    if production_password_file:
        production.with_suffix(".password").write_text(
            PRODUCTION_PASSWORD, encoding="utf-8"
        )
    if default_password_file:
        default.with_suffix(".password").write_text(DEFAULT_PASSWORD, encoding="utf-8")
    return production, default


def _write_template(tmp_path: Path, text: str = TEMPLATE_TEXT) -> Path:
    """A web app template whose only content is the placeholder line."""

    template = tmp_path / "google_drive_script.js"
    template.write_text(text, encoding="utf-8")
    return template


def test_deployment_id_from_url_valid(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    _write_config(tmp_path)
    assert (
        gen.deployment_id_from_url(
            "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec"
        )
        == "AKfycbwEXAMPLE"
    )
    # The surrounding whitespace must not matter.
    assert (
        gen.deployment_id_from_url("  https://script.google.com/macros/s/id_123/exec  ")
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
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    # A url that is not the exact web app endpoint shape is a fatal error:
    # the deploy helper must never guess a deployment ID.
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    _write_config(tmp_path)
    with pytest.raises(gen.ScriptError):
        gen.deployment_id_from_url(url)


def test_production_supplies_the_identifiers_and_every_vault_supplies_a_key(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The script ID and the deployment ID come from the production vault
    # alone, because its project owns the deployed URL; both vaults
    # contribute their auth key, so the deployed web app accepts the
    # machines provisioned from either vault.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    credentials = gen.read_deploy_credentials({})
    assert credentials.script_id == "production-script-id"
    assert credentials.deployment_id == "AKfycbwEXAMPLE"
    assert credentials.auth_keys == ("production-key", "default-key")


def test_environment_password_opens_one_vault_and_the_file_opens_the_other(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One environment value serves the vault it opens and never hides the
    # password file of the other vault, so a deploy needs no two-value
    # dance around the environment.
    production, default = _point_at(
        gen, tmp_path, monkeypatch, production_password_file=False
    )
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    credentials = gen.read_deploy_credentials(
        {"PYNTARA_VAULT_PASSWORD": PRODUCTION_PASSWORD}
    )
    assert credentials.script_id == "production-script-id"
    assert credentials.auth_keys == ("production-key", "default-key")


def test_equal_keys_are_carried_once(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A vault that carries the key of the other one must not put the same
    # value into ALLOWED_KEYS twice.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    same_key_entry = dict(DEFAULT_ENTRY)
    same_key_entry["password"] = PRODUCTION_ENTRY["password"]
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, same_key_entry)
    credentials = gen.read_deploy_credentials({})
    assert credentials.auth_keys == ("production-key",)


def test_vault_source_is_not_a_deploy_switch(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deploy always targets the production project, which owns the
    # deployed URL, and always carries the keys of both vaults: the
    # installer flag PYNTARA_VAULT_SOURCE selects a vault for a run and
    # never redirects a deploy.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    credentials = gen.read_deploy_credentials({"PYNTARA_VAULT_SOURCE": "default"})
    assert credentials.script_id == "production-script-id"
    assert credentials.auth_keys == ("production-key", "default-key")


def test_missing_production_vault_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the production vault there is no project to deploy to: the
    # helper fails instead of deploying with the default identity.
    _production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(gen.ScriptError, match="production vault does not exist"):
        gen.read_deploy_credentials({})


def test_missing_default_vault_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the default vault the deploy would accept one key and drop
    # the telemetry of every machine provisioned from that vault.
    production, _default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    with pytest.raises(gen.ScriptError, match="default vault does not exist"):
        gen.read_deploy_credentials({})


def test_no_password_for_a_vault_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No environment value and no .password file means the vault cannot be
    # opened at all, and the message names what to create.
    production, default = _point_at(
        gen,
        tmp_path,
        monkeypatch,
        production_password_file=False,
        default_password_file=False,
    )
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(gen.ScriptError, match="no password for the production vault"):
        gen.read_deploy_credentials({})


def test_wrong_password_names_the_vault(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A password that opens no candidate of a vault is a loud error naming
    # that vault, never a silent deploy of the other one.
    production, default = _point_at(
        gen, tmp_path, monkeypatch, production_password_file=False
    )
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(gen.ScriptError, match="cannot open the production vault"):
        gen.read_deploy_credentials({"PYNTARA_VAULT_PASSWORD": "wrong-password"})


def test_empty_username_in_production_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The production entry carries the project the deploy pushes to, so an
    # empty username is an error and never a reason to use the other vault.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(PRODUCTION_ENTRY)
    entry["username"] = ""
    _make_vault(production, PRODUCTION_PASSWORD, entry)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(gen.ScriptError, match="empty username"):
        gen.read_deploy_credentials({})


def test_empty_password_in_production_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing auth key must never reach a deploy: an entry without a key
    # would leave the app refusing the machines of that vault.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(PRODUCTION_ENTRY)
    entry["password"] = ""
    _make_vault(production, PRODUCTION_PASSWORD, entry)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(
        gen.ScriptError, match="in the production vault has an empty password"
    ):
        gen.read_deploy_credentials({})


def test_empty_password_in_default_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same rule protects the default vault, whose key is the reason the
    # deploy carries a second value at all.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(DEFAULT_ENTRY)
    entry["password"] = ""
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, entry)
    with pytest.raises(
        gen.ScriptError, match="in the default vault has an empty password"
    ):
        gen.read_deploy_credentials({})


def test_missing_entry_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A vault without the google_script_key entry cannot supply its key.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, None)
    with pytest.raises(gen.ScriptError, match="not found in the default vault"):
        gen.read_deploy_credentials({})


def test_invalid_url_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed url must never yield a guessed deployment ID.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    entry = dict(PRODUCTION_ENTRY)
    entry["url"] = "https://script.google.com/macros/s/"
    _make_vault(production, PRODUCTION_PASSWORD, entry)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(gen.ScriptError, match="not a web app URL"):
        gen.read_deploy_credentials({})


def test_entry_title_comes_from_config(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A vault entry under a custom title configured in config.toml is
    # found: the title is not hardcoded in the script.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    production_entry = dict(PRODUCTION_ENTRY)
    production_entry["title"] = "custom_key_title"
    default_entry = dict(DEFAULT_ENTRY)
    default_entry["title"] = "custom_key_title"
    _make_vault(production, PRODUCTION_PASSWORD, production_entry)
    _make_vault(default, DEFAULT_PASSWORD, default_entry)
    _write_config(tmp_path, title="custom_key_title")
    credentials = gen.read_deploy_credentials({})
    assert credentials.script_id == "production-script-id"
    assert credentials.auth_keys == ("production-key", "default-key")


def test_deployment_pattern_comes_from_config(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deployment URL pattern is not hardcoded: a custom pattern from
    # config.toml drives the ID extraction, and a URL outside the pattern
    # is rejected even when it matches the old Google shape.
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    _write_config(
        tmp_path,
        pattern=r"^https://example\.com/deploy/([A-Za-z0-9_-]+)/exec$",
    )
    assert (
        gen.deployment_id_from_url("https://example.com/deploy/ABC123/exec") == "ABC123"
    )
    with pytest.raises(gen.ScriptError, match="not a web app URL"):
        gen.deployment_id_from_url(
            "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec"
        )


def test_missing_config_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without config.toml the entry title is unknown: a loud error, never
    # a silent hardcoded fallback.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    shutil.rmtree(tmp_path / "config")
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    with pytest.raises(gen.ScriptError, match="config file not found"):
        gen.read_deploy_credentials({})


def test_renders_every_key_into_the_placeholder(
    gen: ModuleType, tmp_path: Path
) -> None:
    # The placeholder becomes the JSON array of the keys, the array Apps
    # Script loads as ALLOWED_KEYS; the rest of the template survives
    # untouched.
    template = _write_template(tmp_path)
    output = tmp_path / "Code.gs"
    gen.render_web_app_file(template, ("production-key", "default-key"), output)
    text = output.read_text(encoding="utf-8")
    assert text == 'const ALLOWED_KEYS = ["production-key", "default-key"];\n'
    array = text.split("= ", 1)[1].rstrip(";\n")
    assert json.loads(array) == ["production-key", "default-key"]


def test_render_touches_only_the_assignment_line(
    gen: ModuleType, tmp_path: Path
) -> None:
    # The placeholder is a name in the prose of the template as well, so the
    # render replaces the assignment line alone: a key must not be spliced
    # into a comment.
    text = (
        "// the placeholder __GOOGLE_SCRIPT_KEYS__ appears here as a name\n"
        "const ALLOWED_KEYS = __GOOGLE_SCRIPT_KEYS__;\n"
    )
    template = _write_template(tmp_path, text)
    output = tmp_path / "Code.gs"
    gen.render_web_app_file(template, ("production-key",), output)
    assert output.read_text(encoding="utf-8") == (
        "// the placeholder __GOOGLE_SCRIPT_KEYS__ appears here as a name\n"
        'const ALLOWED_KEYS = ["production-key"];\n'
    )


def test_render_rejects_a_template_without_the_placeholder(
    gen: ModuleType, tmp_path: Path
) -> None:
    # A template whose placeholder was renamed would deploy an app that
    # does not start, so the render fails instead of copying it.
    template = _write_template(tmp_path, "const ALLOWED_KEYS = [];\n")
    output = tmp_path / "Code.gs"
    with pytest.raises(gen.ScriptError, match="not found in"):
        gen.render_web_app_file(template, ("production-key",), output)
    assert not output.exists()


def test_render_rejects_a_missing_template(gen: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(gen.ScriptError, match="cannot read template"):
        gen.render_web_app_file(
            tmp_path / "absent.js", ("production-key",), tmp_path / "Code.gs"
        )


def test_render_rejects_an_empty_key_list(gen: ModuleType, tmp_path: Path) -> None:
    # An app that accepts nothing is never a deploy worth making.
    template = _write_template(tmp_path)
    with pytest.raises(gen.ScriptError, match="accept nothing"):
        gen.render_web_app_file(template, (), tmp_path / "Code.gs")


def test_repository_template_carries_the_helper_placeholder(
    gen: ModuleType,
) -> None:
    # The shipped template and the helper must name the same placeholder: a
    # rename on one side alone would deploy a file whose placeholder is an
    # undefined name in Apps Script.
    template = (
        REPO_ROOT / "task_data" / "system_metrics_setup" / "google_drive_script.js"
    )
    text = template.read_text(encoding="utf-8")
    assert f"const ALLOWED_KEYS = {gen.PLACEHOLDER};" in text
    assert "__GOOGLE_SCRIPT_KEY__" not in text


def test_deploy_script_renders_through_the_helper(
    gen: ModuleType,
) -> None:
    # The shell script holds no key and no placeholder of its own: it hands
    # the template and the output path to the helper and consumes the two
    # identifiers, so the keys never reach a command line.
    script = (
        REPO_ROOT / "task_data" / "system_metrics_setup" / "deploy_google_script.sh"
    ).read_text(encoding="utf-8")
    assert "read_google_script_credentials.py" in script
    assert '"$SCRIPT_FILE" "$workdir/Code.gs"' in script
    assert gen.PLACEHOLDER not in script
    assert "GOOGLE_SCRIPT_KEY_VALUE" not in script


def test_main_renders_and_prints_the_identifiers(
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # main() renders the web app file from the given template and writes
    # the two identifiers to stdout, the contract the deploy script
    # parses; no key ever appears on stdout.
    production, default = _point_at(gen, tmp_path, monkeypatch)
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    template = _write_template(tmp_path)
    output = tmp_path / "Code.gs"
    assert gen.main([str(template), str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.out == IDENTIFIER_LINES
    assert captured.err == ""
    assert "production-key" not in captured.out
    assert "default-key" not in captured.out
    assert (
        output.read_text(encoding="utf-8")
        == 'const ALLOWED_KEYS = ["production-key", "default-key"];\n'
    )


def test_main_without_two_arguments_is_a_usage_error(
    gen: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    # The deploy script always passes both paths; a caller that does not is
    # told how to call the helper and nothing is written.
    assert gen.main([]) == 2
    assert gen.main(["only-template.js"]) == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err
    assert captured.out == ""


def test_main_error_exits_one_with_empty_stdout(
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A failing read prints nothing on stdout, so the deploy script can
    # never consume a partial value, and the rendered file is not created.
    production, default = _point_at(
        gen, tmp_path, monkeypatch, production_password_file=False
    )
    _make_vault(production, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
    _make_vault(default, DEFAULT_PASSWORD, DEFAULT_ENTRY)
    template = _write_template(tmp_path)
    output = tmp_path / "Code.gs"
    monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", "wrong-password")
    assert gen.main([str(template), str(output)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert not output.exists()


def test_opens_with_pykeepass() -> None:
    # Sanity check that the created vault really opens with its password;
    # guards the fixture against silent password mistakes.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "vault.kdbx"
        _make_vault(path, PRODUCTION_PASSWORD, PRODUCTION_ENTRY)
        kp = PyKeePass(str(path), password=PRODUCTION_PASSWORD)
        entry = kp.find_entries(
            title="google_script_key",
            group=kp.root_group,
            recursive=False,
            first=True,
        )
        assert entry is not None
        assert entry.username == "production-script-id"
        assert entry.url == "https://script.google.com/macros/s/AKfycbwEXAMPLE/exec"
        assert entry.password == "production-key"
