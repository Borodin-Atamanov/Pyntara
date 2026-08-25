"""Unit tests for the shared 3x-ui panel REST API client.

All external resources (urllib.request, filesystem) are mocked via
monkeypatch; the tests only touch temporary fixtures.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pyntara import xui as xui_client
from pyntara.config import ThreeXuiXraySetupConfig


def _cfg(**overrides: object) -> ThreeXuiXraySetupConfig:
    """A minimal ThreeXuiXraySetupConfig with overridable fields."""

    defaults: dict[str, object] = {
        "github_repo": "MHSanaei/3x-ui",
        "install_script_url": "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh",
        "install_dir": Path("/usr/local/x-ui"),
        "service_unit_name": "x-ui.service",
        "start_check_attempts": 10,
        "start_check_retry_delay_seconds": 1,
        "install_result_env_path": Path("/etc/x-ui/install-result.env"),
        "panel_http_address": "127.0.0.1",
        "vault_entry_title": "three_x_ui_credentials",
    }
    defaults.update(overrides)
    return ThreeXuiXraySetupConfig(**defaults)  # type: ignore[arg-type]


class TestParseInstallResultEnv:
    """Tests for parse_install_result_env."""

    def test_parses_full_file(self, tmp_path: Path) -> None:
        path = tmp_path / "install-result.env"
        path.write_text(
            "XUI_USERNAME=admin\n"
            "XUI_PASSWORD=secret123\n"
            "XUI_PANEL_PORT=3579\n"
            "XUI_WEB_BASE_PATH=/panel\n"
            "XUI_API_TOKEN=abc123\n"
            "XUI_DB_TYPE=sqlite\n"
            "XUI_ACCESS_URL=http://example.com:3579/panel\n",
            encoding="utf-8",
        )
        result = xui_client.parse_install_result_env(path)
        assert result["XUI_USERNAME"] == "admin"
        assert result["XUI_PASSWORD"] == "secret123"
        assert result["XUI_PANEL_PORT"] == "3579"
        assert result["XUI_WEB_BASE_PATH"] == "/panel"
        assert result["XUI_API_TOKEN"] == "abc123"
        assert result["XUI_DB_TYPE"] == "sqlite"

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.env"
        with pytest.raises(FileNotFoundError):
            xui_client.parse_install_result_env(path)

    def test_raises_on_missing_required_key(self, tmp_path: Path) -> None:
        path = tmp_path / "partial.env"
        path.write_text("XUI_USERNAME=admin\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="missing required key"):
            xui_client.parse_install_result_env(path)

    def test_ignores_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "blank.env"
        path.write_text(
            "\n\nXUI_USERNAME=admin\n\nXUI_PASSWORD=pass\nXUI_PANEL_PORT=3579\n\n",
            encoding="utf-8",
        )
        result = xui_client.parse_install_result_env(path)
        assert result["XUI_USERNAME"] == "admin"
        assert result["XUI_PASSWORD"] == "pass"
        assert result["XUI_PANEL_PORT"] == "3579"


class TestBuildPanelUrl:
    """Tests for build_panel_url."""

    def test_with_base_path(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "3579", "/panel/")
        assert url == "http://127.0.0.1:3579/panel"

    def test_without_base_path(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "3579", None)
        assert url == "http://127.0.0.1:3579"

    def test_empty_base_path(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "3579", "")
        assert url == "http://127.0.0.1:3579"

    def test_custom_address(self) -> None:
        url = xui_client.build_panel_url("0.0.0.0", "8080", "/xui")
        assert url == "http://0.0.0.0:8080/xui"


class TestLoginAndVerify:
    """Tests for login_and_verify."""

    def _mock_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        csrf_ok: bool = True,
        login_ok: bool = True,
        verify_ok: bool = True,
    ) -> None:
        """Install a _request mock that simulates the panel."""

        call_count: list[int] = [0]

        def fake_request(
            opener: object,
            url: str,
            **kwargs: object,
        ) -> tuple[int, str]:
            del opener
            call_count[0] += 1

            if "/csrf-token" in url:
                if csrf_ok:
                    return (200, json.dumps({"success": True, "obj": "tok123"}))
                return (200, json.dumps({"success": False}))
            elif "/login" in url:
                if login_ok:
                    return (200, json.dumps({"success": True, "msg": "ok"}))
                return (200, json.dumps({"success": False, "msg": "fail"}))
            elif "/panel/api/inbounds/list" in url:
                if verify_ok:
                    return (200, json.dumps({"success": True, "obj": []}))
                return (200, json.dumps({"success": False}))
            return (0, "")

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_successful_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch)
        cfg = _cfg()
        env = {"XUI_USERNAME": "admin", "XUI_PASSWORD": "pass", "XUI_PANEL_PORT": "3579"}
        assert xui_client.login_and_verify(cfg, env, 5) is True

    def test_fails_on_csrf_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, csrf_ok=False)
        cfg = _cfg()
        env = {"XUI_USERNAME": "admin", "XUI_PASSWORD": "pass", "XUI_PANEL_PORT": "3579"}
        assert xui_client.login_and_verify(cfg, env, 5) is False

    def test_fails_on_login_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, login_ok=False)
        cfg = _cfg()
        env = {"XUI_USERNAME": "admin", "XUI_PASSWORD": "wrong", "XUI_PANEL_PORT": "3579"}
        assert xui_client.login_and_verify(cfg, env, 5) is False

    def test_fails_on_verify_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, verify_ok=False)
        cfg = _cfg()
        env = {"XUI_USERNAME": "admin", "XUI_PASSWORD": "pass", "XUI_PANEL_PORT": "3579"}
        assert xui_client.login_and_verify(cfg, env, 5) is False

    def test_uses_web_base_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch)
        cfg = _cfg()
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
            "XUI_WEB_BASE_PATH": "/xui",
        }
        assert xui_client.login_and_verify(cfg, env, 5) is True


class TestVerifyBearer:
    """Tests for verify_bearer."""

    def _mock_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        ok: bool = True,
    ) -> None:
        def fake_request(
            opener: object,
            url: str,
            **kwargs: object,
        ) -> tuple[int, str]:
            del opener, url, kwargs
            if ok:
                return (200, json.dumps({"success": True, "obj": []}))
            return (200, json.dumps({"success": False}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_successful(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=True)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        assert xui_client.verify_bearer(cfg, env, 5) is True

    def test_fails_on_bad_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=False)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "bad", "XUI_PANEL_PORT": "3579"}
        assert xui_client.verify_bearer(cfg, env, 5) is False
