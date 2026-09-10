"""Unit tests for the shared 3x-ui panel REST API client.

All external resources (urllib.request, filesystem) are mocked via
monkeypatch; the tests only touch temporary fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from support import FakeProc as _FakeProc

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
        "panel_port": 35353,
        "ssl_enabled": True,
        "panel_http_address": "127.0.0.1",
        "vault_entry_title": "three_x_ui_credentials",
        "connection_vault_entry_title": "xray_connection",
        "share_addr_strategy": "custom",
        "inbound_port": 443,
        "inbound_remark": "universal",
        "reality_dest": "www.google.com:443",
        "reality_server_names": ("www.google.com",),
        "reality_short_id": "6ba85179e30d4fc2",
        "reality_fingerprint": "chrome",
        "subscription_path": "/s/",
        "subscription_json_path": "/j/",
        "subscription_clash_path": "/c/",
        "acme_port": 80,
        "cert_dir": Path("/root/cert/ip"),
        "cert_fullchain": Path("/root/cert/ip/fullchain.pem"),
        "cert_privkey": Path("/root/cert/ip/privkey.pem"),
        "self_signed_cert_dir": Path("/root/cert/selfsigned"),
        "self_signed_cert_fullchain": Path(
            "/root/cert/selfsigned/fullchain.pem"
        ),
        "self_signed_cert_privkey": Path("/root/cert/selfsigned/privkey.pem"),
        "server_ip_timeout_seconds": 60,
        "server_ip_services": ("https://api4.ipify.org",),
        "upnp_enabled": True,
        "upnp_package": "miniupnpc",
        "upnp_client_command": "upnpc",
        "upnp_mapping_description": "pyntara xray",
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

    def test_https_scheme(self) -> None:
        url = xui_client.build_panel_url(
            "127.0.0.1", "35353", "/xui", scheme="https"
        )
        assert url == "https://127.0.0.1:35353/xui"


class TestPanelScheme:
    """Tests for panel_cert_value and panel_scheme."""

    def test_cert_configured_is_https(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A set certificate path means the panel serves TLS.
        monkeypatch.setattr(
            "pyntara.xui.run_command",
            lambda command, **kwargs: _FakeProc(
                0,
                "cert: /root/cert/ip/fullchain.pem\n"
                "key: /root/cert/ip/privkey.pem\n",
            ),
        )
        cfg = _cfg()
        assert (
            xui_client.panel_cert_value(cfg, 30) == "/root/cert/ip/fullchain.pem"
        )
        assert xui_client.panel_scheme(cfg, 30) == "https"

    def test_no_cert_is_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An empty cert value means plain HTTP.
        monkeypatch.setattr(
            "pyntara.xui.run_command",
            lambda command, **kwargs: _FakeProc(0, "cert: \nkey: \n"),
        )
        cfg = _cfg()
        assert xui_client.panel_cert_value(cfg, 30) is None
        assert xui_client.panel_scheme(cfg, 30) == "http"


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


class TestListInbounds:
    """Tests for list_inbounds."""

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
                return (200, json.dumps({"success": True, "obj": [{"id": 1, "port": 443}]}))
            return (200, json.dumps({"success": False}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_returns_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=True)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.list_inbounds(cfg, env, 5)
        assert len(result) == 1
        assert result[0]["port"] == 443

    def test_returns_empty_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, ok=False)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "bad", "XUI_PANEL_PORT": "3579"}
        result = xui_client.list_inbounds(cfg, env, 5)
        assert result == []


class TestFindInboundByPort:
    """Tests for find_inbound_by_port."""

    def _mock_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        inbounds: list[dict[str, object]] | None = None,
    ) -> None:
        if inbounds is None:
            inbounds = [{"id": 1, "port": 443}, {"id": 2, "port": 80}]

        def fake_request(
            opener: object,
            url: str,
            **kwargs: object,
        ) -> tuple[int, str]:
            del opener, url, kwargs
            return (200, json.dumps({"success": True, "obj": inbounds}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_finds_by_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.find_inbound_by_port(cfg, env, 443, 5)
        assert result is not None
        assert result["id"] == 1

    def test_returns_none_when_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.find_inbound_by_port(cfg, env, 9999, 5)
        assert result is None


class TestCreateInbound:
    """Tests for create_inbound."""

    def _mock_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        success: bool = True,
        msg: str = "inbound created",
        status: int = 200,
    ) -> None:
        def fake_request(
            opener: object,
            url: str,
            **kwargs: object,
        ) -> tuple[int, str]:
            del opener, url, kwargs
            return (status, json.dumps({"success": success, "msg": msg}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_creates_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, success=True)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        payload = {"remark": "test", "port": 443, "protocol": "vless"}
        ok, msg = xui_client.create_inbound(cfg, env, payload, 5)
        assert ok is True
        assert "created" in msg

    def test_rejects_duplicate_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(
            monkeypatch,
            success=False,
            msg="port 443 already used by inbound 'test' (#1)",
        )
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        payload = {"remark": "test", "port": 443, "protocol": "vless"}
        ok, msg = xui_client.create_inbound(cfg, env, payload, 5)
        assert ok is False
        assert "already used" in msg

    def test_handles_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, status=0)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        payload = {"remark": "test", "port": 443, "protocol": "vless"}
        ok, msg = xui_client.create_inbound(cfg, env, payload, 5)
        assert ok is False
        assert "unreachable" in msg


class TestGenerateRealityKey:
    """Tests for generate_reality_key."""

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
                return (
                    200,
                    json.dumps(
                        {
                            "success": True,
                            "obj": {
                                "privateKey": "priv123",
                                "publicKey": "pub123",
                            },
                        }
                    ),
                )
            return (200, json.dumps({"success": False}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_returns_keypair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=True)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.generate_reality_key(cfg, env, 5)
        assert result is not None
        priv, pub = result
        assert priv == "priv123"
        assert pub == "pub123"

    def test_returns_none_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, ok=False)
        cfg = _cfg()
        env = {"XUI_API_TOKEN": "bad", "XUI_PANEL_PORT": "3579"}
        result = xui_client.generate_reality_key(cfg, env, 5)
        assert result is None


class TestBuildVlessRealityPayload:
    """Tests for build_vless_reality_payload."""

    def test_builds_correct_structure(self) -> None:
        # The payload is a nested JSON object, so the test narrows the
        # dict[str, object] return to a checkable shape.
        payload = cast(
            dict[str, Any],
            xui_client.build_vless_reality_payload(
                port=443,
                remark="universal",
                dest="www.google.com:443",
                server_names=("www.google.com",),
                private_key="priv123",
                public_key="pub123",
                short_id="6ba85179e30d4fc2",
                fingerprint="chrome",
            ),
        )
        assert payload["port"] == 443
        assert payload["protocol"] == "vless"
        assert payload["remark"] == "universal"
        assert payload["enable"] is True
        assert payload["settings"]["decryption"] == "none"
        assert payload["streamSettings"]["security"] == "reality"
        assert payload["streamSettings"]["realitySettings"]["dest"] == "www.google.com:443"
        assert payload["streamSettings"]["realitySettings"]["privateKey"] == "priv123"
        assert payload["streamSettings"]["realitySettings"]["shortIds"] == ["6ba85179e30d4fc2"]
        assert payload["streamSettings"]["realitySettings"]["settings"] == {
            "publicKey": "pub123",
            "fingerprint": "chrome",
        }
        assert payload["sniffing"]["enabled"] is True
        assert payload["sniffing"]["destOverride"] == ["http", "tls"]

    def test_accepts_custom_values(self) -> None:
        # The payload is a nested JSON object, so the test narrows the
        # dict[str, object] return to a checkable shape.
        payload = cast(
            dict[str, Any],
            xui_client.build_vless_reality_payload(
                port=8443,
                remark="custom",
                dest="bing.com:443",
                server_names=("bing.com", "www.bing.com"),
                private_key="customkey",
                public_key="custompub",
                short_id="abc12345",
                fingerprint="firefox",
            ),
        )
        assert payload["port"] == 8443
        assert payload["remark"] == "custom"
        assert payload["streamSettings"]["realitySettings"]["dest"] == "bing.com:443"
        assert payload["streamSettings"]["realitySettings"]["serverNames"] == [
            "bing.com",
            "www.bing.com",
        ]
        assert payload["streamSettings"]["realitySettings"]["privateKey"] == "customkey"
        assert payload["streamSettings"]["realitySettings"]["shortIds"] == ["abc12345"]


class TestPanelSettings:
    """Tests for the panel settings helpers."""

    def _mock_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        settings: dict[str, object] | None = None,
        read_ok: bool = True,
        write_ok: bool = True,
        captured: list[dict[str, object]] | None = None,
    ) -> None:
        """Fake _request: answer the settings read and record the write."""

        current = dict(settings or {})

        def fake_request(
            opener: object,
            url: str,
            **kwargs: object,
        ) -> tuple[int, str]:
            del opener
            if url.endswith("/panel/api/setting/all"):
                if not read_ok:
                    return (0, "")
                return (200, json.dumps({"success": True, "obj": current}))
            if url.endswith("/panel/api/setting/update"):
                data = kwargs.get("data")
                if captured is not None and isinstance(data, bytes):
                    captured.append(cast(dict[str, object], json.loads(data.decode())))
                if not write_ok:
                    return (200, json.dumps({"success": False, "msg": "nope"}))
                return (200, json.dumps({"success": True, "msg": "changed"}))
            return (0, "")

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_panel_settings_returns_document(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, settings={"subPath": "/sub/"})
        assert xui_client.panel_settings(_cfg(), {"XUI_PANEL_PORT": "3579"}, 5) == {
            "subPath": "/sub/"
        }

    def test_panel_settings_none_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, read_ok=False)
        assert xui_client.panel_settings(_cfg(), {"XUI_PANEL_PORT": "3579"}, 5) is None

    def test_update_panel_settings_reports_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch)
        ok, message = xui_client.update_panel_settings(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, {"subPath": "/s/"}, 5
        )
        assert ok is True
        assert message == "changed"

    def test_update_panel_settings_reports_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, write_ok=False)
        ok, message = xui_client.update_panel_settings(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, {"subPath": "/s/"}, 5
        )
        assert ok is False
        assert message == "nope"

    def test_ensure_subscription_paths_writes_when_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[dict[str, object]] = []
        self._mock_request(
            monkeypatch,
            settings={
                "subPath": "/sub/",
                "subJsonPath": "/json/",
                "subClashPath": "/clash/",
                "webPort": 35353,
            },
            captured=captured,
        )
        changed, message = xui_client.ensure_subscription_paths(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, 5
        )
        assert changed is True
        assert "/s/" in message
        assert captured[0]["subPath"] == "/s/"
        assert captured[0]["subJsonPath"] == "/j/"
        assert captured[0]["subClashPath"] == "/c/"
        assert captured[0]["webPort"] == 35353

    def test_ensure_subscription_paths_noop_when_current(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[dict[str, object]] = []
        self._mock_request(
            monkeypatch,
            settings={
                "subPath": "/s/",
                "subJsonPath": "/j/",
                "subClashPath": "/c/",
            },
            captured=captured,
        )
        changed, message = xui_client.ensure_subscription_paths(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, 5
        )
        assert changed is False
        assert message == ""
        assert captured == []

    def test_ensure_subscription_paths_reports_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, read_ok=False)
        changed, message = xui_client.ensure_subscription_paths(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, 5
        )
        assert changed is False
        assert message == "cannot read panel settings"


class TestUpdateInbound:
    """Tests for update_inbound."""

    def test_puts_inbound_id_in_the_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def fake_request(
            opener: object, url: str, **kwargs: object
        ) -> tuple[int, str]:
            del opener, kwargs
            seen.append(url)
            return (200, json.dumps({"success": True, "msg": "inbound updated"}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        ok, message = xui_client.update_inbound(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, {"id": 7, "port": 443}, 5
        )
        assert ok is True
        assert message == "inbound updated"
        assert seen[0].endswith("/panel/api/inbounds/update/7")

    def test_reports_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps({"success": False, "msg": "port busy"}),
            ),
        )
        ok, message = xui_client.update_inbound(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, {"id": 7}, 5
        )
        assert ok is False
        assert message == "port busy"


class TestFindClient:
    """Tests for find_client."""

    def test_returns_the_client_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps(
                    {"success": True, "obj": {"client": {"email": "a-b"}}}
                ),
            ),
        )
        assert xui_client.find_client(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, "a-b", 5
        ) == {"email": "a-b"}

    def test_returns_none_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps({"success": False, "msg": "not found"}),
            ),
        )
        assert (
            xui_client.find_client(
                _cfg(), {"XUI_PANEL_PORT": "3579"}, "missing", 5
            )
            is None
        )


class TestCreateClient:
    """Tests for create_client."""

    def test_sends_credential_email_and_inbound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[dict[str, object]] = []

        def fake_request(
            opener: object, url: str, **kwargs: object
        ) -> tuple[int, str]:
            del opener
            assert url.endswith("/panel/api/clients/add")
            data = kwargs.get("data")
            if isinstance(data, bytes):
                captured.append(cast(dict[str, object], json.loads(data.decode())))
            return (200, json.dumps({"success": True, "msg": "added"}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        ok, message = xui_client.create_client(
            _cfg(),
            {"XUI_PANEL_PORT": "3579"},
            3,
            "hosiz-sanif-fofum-namib",
            "kazoj-nogur",
            "bubumlajahhopal",
            5,
        )
        assert ok is True
        assert message == "added"
        assert captured[0] == {
            "client": {
                "id": "hosiz-sanif-fofum-namib",
                "email": "kazoj-nogur",
                "enable": True,
                "subId": "bubumlajahhopal",
            },
            "inboundIds": [3],
        }

    def test_reports_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request", lambda _opener, _url, **_kwargs: (0, "")
        )
        ok, message = xui_client.create_client(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, 3, "id", "mail", "sub", 5
        )
        assert ok is False
        assert message == "panel unreachable"


class TestClientLinks:
    """Tests for client_links."""

    def test_returns_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": ["vless://x@host:443", 7, ""],
                    }
                ),
            ),
        )
        assert xui_client.client_links(
            _cfg(), {"XUI_PANEL_PORT": "3579"}, "a-b", 5
        ) == ["vless://x@host:443"]

    def test_returns_empty_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request", lambda _opener, _url, **_kwargs: (0, "")
        )
        assert (
            xui_client.client_links(
                _cfg(), {"XUI_PANEL_PORT": "3579"}, "a-b", 5
            )
            == []
        )
