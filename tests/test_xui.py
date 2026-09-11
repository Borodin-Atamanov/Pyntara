"""Unit tests for the shared 3x-ui panel REST API client.

All external resources (urllib.request, filesystem) are mocked via
monkeypatch; the tests only touch temporary fixtures.
"""

from __future__ import annotations

import json
import urllib.parse
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
        "panel_root_path": "/",
        "panel_login_path": "/login",
        "panel_csrf_token_path": "/csrf-token",
        "panel_inbounds_list_path": "/panel/api/inbounds/list",
        "panel_inbounds_add_path": "/panel/api/inbounds/add",
        "panel_inbounds_update_path": "/panel/api/inbounds/update/{inbound_id}",
        "panel_inbounds_delete_path": "/panel/api/inbounds/del/{inbound_id}",
        "panel_client_get_path": "/panel/api/clients/get/{email}",
        "panel_client_add_path": "/panel/api/clients/add",
        "panel_client_links_path": "/panel/api/clients/links/{email}",
        "panel_x25519_cert_path": "/panel/api/server/getNewX25519Cert",
        "panel_setting_all_path": "/panel/api/setting/all",
        "panel_setting_update_path": "/panel/api/setting/update",
        "panel_xray_status_path": "/panel/api/xray/",
        "panel_xray_update_path": "/panel/api/xray/update",
        "panel_xray_geodata_validate_path": "/panel/api/xray/geodata/validate",
        "panel_xray_route_test_path": "/panel/api/xray/routeTest",
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
        "cert_privkey_file_mode": 0o600,
        "cert_fullchain_file_mode": 0o644,
        "self_signed_cert_dir": Path("/root/cert/selfsigned"),
        "self_signed_cert_fullchain": Path(
            "/root/cert/selfsigned/fullchain.pem"
        ),
        "self_signed_cert_privkey": Path("/root/cert/selfsigned/privkey.pem"),
        "server_ip_timeout_seconds": 60,
        "server_ip_services": ("https://api4.ipify.org",),
        "probe_timeout_seconds": 60,
        "probe_port_80_timeout_seconds": 10,
        "probe_listener_start_seconds": 1,
        "upnp_enabled": True,
        "upnp_package": "miniupnpc",
        "upnp_client_command": "upnpc",
        "upnp_mapping_description": "pyntara xray",
        "client_profile_entry_title": "xray_client_profile",
        "local_proxy_tag": "pyntara-local-proxy",
        "local_proxy_listen_address": "127.0.0.1",
        "local_proxy_port": 10800,
        "local_proxy_udp": True,
        "local_proxy_sniffing_protocols": ("http", "tls", "quic"),
        "remote_outbound_tag": "pyntara-remote",
        "tor_outbound_tag": "pyntara-tor",
        "i2p_outbound_tag": "pyntara-i2p",
        "direct_outbound_tag": "direct",
        "blocked_outbound_tag": "blocked",
        "tor_proxy_address": "127.0.0.1:9050",
        "i2p_proxy_address": "127.0.0.1:4444",
        "ad_block_domain_categories": ("geosite:category-ads-all",),
        "direct_domains": (
            "domain:localhost",
            "domain:.local",
            "domain:.home.arpa",
            "domain:.lan",
            "domain:.internal",
        ),
        "direct_ip_categories": ("geoip:private",),
        "direct_ip_networks": ("200::/7", "300::/7"),
        "country_services": (
            "https://ip2c.org/self",
            "https://ifconfig.co/json",
            "https://ipwho.is/",
        ),
        "country_word": "russia",
        "country_query_timeout_seconds": 10,
        "country_command_timeout_seconds": 20,
        "russia_blocked_domain_categories": (
            "ext-site:geosite_RU.dat:ru-blocked-all",
        ),
        "russia_blocked_ip_categories": (
            "ext-ip:geoip_RU.dat:ru-blocked",
            "ext-ip:geoip_RU.dat:ru-blocked-community",
        ),
        "russia_direct_domain_categories": (
            "ext-site:geosite_RU.dat:ru-available-only-inside",
        ),
        "russia_direct_ip_categories": ("ext-ip:geoip_RU.dat:ru-whitelist",),
        "geo_restricted_domain_categories": (
            "geosite:category-ai-!cn",
            "geosite:openai",
            "geosite:xai",
            "geosite:netflix",
            "geosite:spotify",
            "geosite:category-social-media-!cn",
        ),
        "russia_domain_strategy": "IPIfNonMatch",
        "outside_russia_domain_strategy": "AsIs",
        "route_check_ad_domain": "doubleclick.net",
        "route_check_foreign_domain": "example.com",
        "route_check_onion_domain": "pyntara-check.onion",
        "route_check_i2p_domain": "pyntara-check.i2p",
        "route_check_direct_domain": "localhost",
        "route_check_russia_blocked_domain": "instagram.com",
        "proxy_check_url": "https://api4.ipify.org",
        "proxy_check_blocked_url": "https://api.openai.com/v1/models",
        "proxy_check_timeout_seconds": 20,
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


class TestPanelPathsComeFromConfig:
    """Every panel call uses the path of the configuration."""

    def test_login_session_uses_the_configured_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, dict[str, object]]] = []

        def fake_request(
            opener: object, url: str, **kwargs: object
        ) -> tuple[int, str]:
            del opener
            headers = kwargs.get("headers", {})
            seen.append((url, headers if isinstance(headers, dict) else {}))
            if url.endswith("/custom-csrf"):
                return (200, json.dumps({"success": True, "obj": "tok123"}))
            return (200, json.dumps({"success": True}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        cfg = _cfg(
            panel_root_path="/custom-root",
            panel_login_path="/custom-login",
            panel_csrf_token_path="/custom-csrf",
            panel_inbounds_list_path="/custom-inbounds",
        )
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
        }
        assert xui_client.login_and_verify(cfg, env, 5) is True
        assert [url for url, _headers in seen] == [
            "http://127.0.0.1:3579/custom-csrf",
            "http://127.0.0.1:3579/custom-login",
            "http://127.0.0.1:3579/custom-inbounds",
        ]
        assert seen[1][1]["Referer"] == "http://127.0.0.1:3579/custom-root"

    def test_bearer_calls_use_the_configured_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def fake_request(
            opener: object, url: str, **kwargs: object
        ) -> tuple[int, str]:
            del opener, kwargs
            seen.append(url)
            return (200, json.dumps({"success": True, "obj": {"links": []}}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        cfg = _cfg(
            panel_inbounds_delete_path="/custom/del/{inbound_id}",
            panel_client_links_path="/custom/links/{email}",
            panel_xray_status_path="/custom/xray",
        )
        env = {"XUI_PANEL_PORT": "3579"}
        assert xui_client.delete_inbound(cfg, env, 9, 5)[0] is True
        xui_client.client_links(cfg, env, "a b", 5)
        xui_client.read_xray_template(cfg, env, 5)
        assert seen == [
            "http://127.0.0.1:3579/custom/del/9",
            "http://127.0.0.1:3579/custom/links/a%20b",
            "http://127.0.0.1:3579/custom/xray",
        ]


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


class _RecordedRequest:
    """One call the code under test made to the HTTP layer."""

    def __init__(self, url: str, kwargs: dict[str, object]) -> None:
        self.url = url
        self.kwargs = kwargs

    def header(self, name: str) -> str:
        headers = self.kwargs.get("headers")
        if not isinstance(headers, dict):
            return ""
        value = headers.get(name)
        return value if isinstance(value, str) else ""

    def form(self) -> dict[str, str]:
        data = self.kwargs.get("data")
        if not isinstance(data, bytes):
            return {}
        parsed = urllib.parse.parse_qs(data.decode("utf-8"))
        return {key: values[0] for key, values in parsed.items()}

    def json_body(self) -> dict[str, object]:
        data = self.kwargs.get("data")
        if not isinstance(data, bytes):
            return {}
        decoded = json.loads(data.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {}


def _record_requests(
    monkeypatch: pytest.MonkeyPatch,
    *answers: tuple[int, str],
) -> list[_RecordedRequest]:
    """Answer the HTTP layer with the given answers and record the calls.

    The answers are used in order, so a test that expects a read and then
    a write passes two; the last answer repeats when more calls arrive.
    """

    recorded: list[_RecordedRequest] = []

    def fake_request(
        opener: object, url: str, **kwargs: object
    ) -> tuple[int, str]:
        del opener
        recorded.append(_RecordedRequest(url, kwargs))
        index = min(len(recorded), len(answers)) - 1
        return answers[index]

    monkeypatch.setattr("pyntara.xui._request", fake_request)
    return recorded


_ENV = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}


class TestFindInboundByTag:
    """Tests for find_inbound_by_tag."""

    def _mock_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
        inbounds: list[dict[str, object]],
    ) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps({"success": True, "obj": inbounds}),
            ),
        )

    def test_finds_the_inbound_that_carries_the_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(
            monkeypatch,
            [
                {"id": 1, "remark": "universal", "tag": "in-443-tcp", "port": 443},
                {
                    "id": 2,
                    "remark": "pyntara local proxy",
                    "tag": "pyntara-local-proxy",
                    "port": 10800,
                },
            ],
        )
        found = xui_client.find_inbound_by_tag(
            _cfg(), _ENV, "pyntara-local-proxy", 5
        )
        assert found is not None
        assert found["id"] == 2

    def test_the_human_label_is_not_the_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(
            monkeypatch,
            [{"id": 2, "remark": "pyntara local proxy", "tag": "pyntara-local-proxy"}],
        )
        assert (
            xui_client.find_inbound_by_tag(_cfg(), _ENV, "pyntara local proxy", 5)
            is None
        )

    def test_returns_none_when_the_tag_is_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, [{"id": 1, "tag": "in-443-tcp"}])
        assert (
            xui_client.find_inbound_by_tag(_cfg(), _ENV, "pyntara-local-proxy", 5)
            is None
        )


class TestUpsertInbound:
    """Tests for upsert_inbound."""

    def test_creates_the_inbound_when_the_tag_is_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": []})),
            (200, json.dumps({"success": True, "msg": "inbound added"})),
        )
        ok, message = xui_client.upsert_inbound(
            _cfg(),
            _ENV,
            {"tag": "pyntara-local-proxy", "port": 10800},
            5,
        )
        assert ok is True
        assert message == "inbound added"
        assert recorded[0].url.endswith("/panel/api/inbounds/list")
        assert recorded[1].url.endswith("/panel/api/inbounds/add")
        assert recorded[1].json_body()["tag"] == "pyntara-local-proxy"

    def test_replaces_the_inbound_that_carries_the_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": [
                            {
                                "id": 2,
                                "remark": "pyntara local proxy",
                                "tag": "pyntara-local-proxy",
                                "port": 10801,
                            }
                        ],
                    }
                ),
            ),
            (200, json.dumps({"success": True, "msg": "inbound updated"})),
        )
        ok, message = xui_client.upsert_inbound(
            _cfg(),
            _ENV,
            {"tag": "pyntara-local-proxy", "remark": "pyntara local proxy", "port": 10800},
            5,
        )
        assert ok is True
        assert message == "inbound pyntara-local-proxy updated: inbound updated"
        assert recorded[1].url.endswith("/panel/api/inbounds/update/2")
        assert recorded[1].json_body()["port"] == 10800

    def test_refuses_a_payload_without_a_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(monkeypatch, (200, "{}"))
        ok, message = xui_client.upsert_inbound(_cfg(), _ENV, {"port": 10800}, 5)
        assert ok is False
        assert "tag" in message
        assert recorded == []

    def test_reports_a_failed_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": []})),
            (200, json.dumps({"success": False, "msg": "port already used"})),
        )
        ok, message = xui_client.upsert_inbound(
            _cfg(), _ENV, {"tag": "pyntara-local-proxy", "port": 10800}, 5
        )
        assert ok is False
        assert message == "port already used"


class TestDeleteInbound:
    """Tests for delete_inbound."""

    def test_deletes_by_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded = _record_requests(
            monkeypatch, (200, json.dumps({"success": True, "msg": "inbound deleted"}))
        )
        ok, message = xui_client.delete_inbound(_cfg(), _ENV, 3, 5)
        assert ok is True
        assert message == "inbound deleted"
        assert recorded[0].url.endswith("/panel/api/inbounds/del/3")
        assert recorded[0].header("Authorization") == "Bearer tok123"

    def test_reports_an_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        ok, message = xui_client.delete_inbound(_cfg(), _ENV, 3, 5)
        assert ok is False
        assert message == "panel unreachable"


class TestReadXrayTemplate:
    """Tests for read_xray_template."""

    def _template_body(self, xray_setting: object) -> tuple[int, str]:
        blob = json.dumps(
            {
                "xraySetting": xray_setting,
                "inboundTags": ["in-443-tcp"],
                "clientReverseTags": [],
                "outboundTestUrl": "https://www.google.com/generate_204",
            }
        )
        return (200, json.dumps({"success": True, "obj": blob}))

    def test_reads_the_blob_the_panel_stores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            self._template_body(json.dumps({"outbounds": [{"tag": "direct"}]})),
        )
        template = xui_client.read_xray_template(_cfg(), _ENV, 5)
        assert template is not None
        assert template.settings == {"outbounds": [{"tag": "direct"}]}
        assert template.outbound_test_url == "https://www.google.com/generate_204"
        assert recorded[0].url.endswith("/panel/api/xray/")
        assert recorded[0].kwargs.get("method") == "POST"

    def test_accepts_an_already_parsed_document(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch, self._template_body({"outbounds": [{"tag": "direct"}]})
        )
        template = xui_client.read_xray_template(_cfg(), _ENV, 5)
        assert template is not None
        assert template.settings == {"outbounds": [{"tag": "direct"}]}

    def test_reports_nothing_when_the_panel_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        assert xui_client.read_xray_template(_cfg(), _ENV, 5) is None

    def test_reports_nothing_on_an_unreadable_blob(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (200, json.dumps({"success": True, "obj": "not json"})))
        assert xui_client.read_xray_template(_cfg(), _ENV, 5) is None

    def test_reports_nothing_when_the_document_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": json.dumps({"outboundTestUrl": ""})})),
        )
        assert xui_client.read_xray_template(_cfg(), _ENV, 5) is None


class TestWriteXrayTemplate:
    """Tests for write_xray_template."""

    def test_sends_the_document_as_a_form_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "msg": "xray template updated"})),
        )
        template = xui_client.XrayTemplate(
            settings={"outbounds": [{"tag": "pyntara-remote"}]},
            outbound_test_url="https://www.google.com/generate_204",
        )
        ok, message = xui_client.write_xray_template(_cfg(), _ENV, template, 5)
        assert ok is True
        assert message == "xray template updated"
        request = recorded[0]
        assert request.url.endswith("/panel/api/xray/update")
        assert request.kwargs.get("method") == "POST"
        assert request.header("Content-Type") == "application/x-www-form-urlencoded"
        form = request.form()
        assert json.loads(form["xraySetting"]) == template.settings
        assert form["outboundTestUrl"] == template.outbound_test_url

    def test_reports_a_rejected_apply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": False, "msg": "invalid xray config: line 3"})),
        )
        template = xui_client.XrayTemplate(settings={}, outbound_test_url="")
        ok, message = xui_client.write_xray_template(_cfg(), _ENV, template, 5)
        assert ok is False
        assert message == "invalid xray config: line 3"


class TestValidateGeodataTokens:
    """Tests for validate_geodata_tokens."""

    def test_returns_only_the_rejected_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": [
                            {
                                "token": "geosite:nosuchcat",
                                "reason": "categoryMissing",
                                "file": "geosite.dat",
                                "code": "nosuchcat",
                            }
                        ],
                    }
                ),
            ),
        )
        rejected = xui_client.validate_geodata_tokens(
            _cfg(),
            _ENV,
            xui_client.GEODATA_DOMAIN_KIND,
            ["geosite:openai", "geosite:nosuchcat"],
            5,
        )
        assert rejected == {"geosite:nosuchcat": "categoryMissing"}
        request = recorded[0]
        assert request.url.endswith("/panel/api/xray/geodata/validate")
        assert request.form() == {
            "kind": "domain",
            "tokens": "geosite:openai,geosite:nosuchcat",
        }

    def test_reports_every_token_when_the_panel_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        rejected = xui_client.validate_geodata_tokens(
            _cfg(), _ENV, xui_client.GEODATA_IP_KIND, ["geoip:private", "200::/7"], 5
        )
        assert set(rejected) == {"geoip:private", "200::/7"}
        assert rejected["geoip:private"] == "panel unreachable"

    def test_asks_nothing_without_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(monkeypatch, (200, "{}"))
        assert (
            xui_client.validate_geodata_tokens(
                _cfg(), _ENV, xui_client.GEODATA_IP_KIND, [], 5
            )
            == {}
        )
        assert recorded == []

    def test_rejects_an_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="unknown geodata kind"):
            xui_client.validate_geodata_tokens(_cfg(), _ENV, "hostname", ["x"], 5)


class TestRouteTest:
    """Tests for route_test."""

    def test_reports_the_outbound_the_core_chose(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {"success": True, "obj": {"matched": True, "outboundTag": "pyntara-tor"}}
                ),
            ),
        )
        matched, answer = xui_client.route_test(
            _cfg(),
            _ENV,
            inbound_tag="pyntara-local-proxy",
            domain="abcdef.onion",
            port=80,
            network="tcp",
            protocol="http",
            timeout=5,
        )
        assert matched is True
        assert answer == "pyntara-tor"
        request = recorded[0]
        assert request.url.endswith("/panel/api/xray/routeTest")
        assert request.form() == {
            "port": "80",
            "network": "tcp",
            "protocol": "http",
            "inboundTag": "pyntara-local-proxy",
            "domain": "abcdef.onion",
        }

    def test_asks_with_an_address_when_given_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {"success": True, "obj": {"matched": True, "outboundTag": "direct"}}
                ),
            ),
        )
        matched, answer = xui_client.route_test(
            _cfg(),
            _ENV,
            inbound_tag="pyntara-local-proxy",
            address="10.10.0.1",
            port=443,
            timeout=5,
        )
        assert (matched, answer) == (True, "direct")
        assert recorded[0].form()["ip"] == "10.10.0.1"
        assert "domain" not in recorded[0].form()

    def test_reports_a_destination_no_rule_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": {"matched": False, "outboundTag": ""}})),
        )
        matched, answer = xui_client.route_test(
            _cfg(),
            _ENV,
            inbound_tag="pyntara-local-proxy",
            domain="example.com",
            timeout=5,
        )
        assert matched is False
        assert "no routing rule" in answer

    def test_reports_the_panel_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": False, "msg": "invalid inbound tag"})),
        )
        matched, answer = xui_client.route_test(
            _cfg(),
            _ENV,
            inbound_tag="pyntara-local-proxy",
            domain="example.com",
            timeout=5,
        )
        assert matched is False
        assert answer == "invalid inbound tag"

    def test_reports_an_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        matched, answer = xui_client.route_test(
            _cfg(),
            _ENV,
            inbound_tag="pyntara-local-proxy",
            domain="example.com",
            timeout=5,
        )
        assert matched is False
        assert answer == "panel unreachable"

    def test_refuses_a_request_without_a_destination(self) -> None:
        with pytest.raises(ValueError, match="domain or an address"):
            xui_client.route_test(
                _cfg(), _ENV, inbound_tag="pyntara-local-proxy", timeout=5
            )

