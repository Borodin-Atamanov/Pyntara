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
from pyntara.values import three_x_ui_xray_setup as panel_values


def _payload_template() -> str:
    """The shipped inbound payload template, read from this clone."""

    return (
        Path(__file__).resolve().parents[1]
        / "task_data"
        / "three_x_ui_xray_setup"
        / "vless_reality_inbound.json"
    ).read_text(encoding="utf-8")


class TestParseInstallResultEnv:
    """Tests for parse_install_result_env."""

    def _required_keys(self) -> tuple[str, ...]:
        """The keys the client cannot work without, from the values."""

        return xui_client.panel_required_environment_keys()

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
        result = xui_client.parse_install_result_env(path, self._required_keys())
        assert result["XUI_USERNAME"] == "admin"
        assert result["XUI_PASSWORD"] == "secret123"
        assert result["XUI_PANEL_PORT"] == "3579"
        assert result["XUI_WEB_BASE_PATH"] == "/panel"
        assert result["XUI_API_TOKEN"] == "abc123"
        assert result["XUI_DB_TYPE"] == "sqlite"

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.env"
        with pytest.raises(FileNotFoundError):
            xui_client.parse_install_result_env(path, self._required_keys())

    def test_raises_on_missing_required_key(self, tmp_path: Path) -> None:
        path = tmp_path / "partial.env"
        path.write_text("XUI_USERNAME=admin\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="missing required key"):
            xui_client.parse_install_result_env(path, self._required_keys())

    def test_ignores_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "blank.env"
        path.write_text(
            "\n\nXUI_USERNAME=admin\n\nXUI_PASSWORD=pass\nXUI_PANEL_PORT=3579\n\n",
            encoding="utf-8",
        )
        result = xui_client.parse_install_result_env(path, self._required_keys())
        assert result["XUI_USERNAME"] == "admin"
        assert result["XUI_PASSWORD"] == "pass"
        assert result["XUI_PANEL_PORT"] == "3579"


class TestBuildPanelUrl:
    """Tests for build_panel_url."""

    def test_with_base_path(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "3579", "/panel/", "http")
        assert url == "http://127.0.0.1:3579/panel"

    def test_without_base_path(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "3579", None, "http")
        assert url == "http://127.0.0.1:3579"

    def test_empty_base_path(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "3579", "", "http")
        assert url == "http://127.0.0.1:3579"

    def test_custom_address(self) -> None:
        url = xui_client.build_panel_url("0.0.0.0", "8080", "/xui", "http")
        assert url == "http://0.0.0.0:8080/xui"

    def test_https_scheme(self) -> None:
        url = xui_client.build_panel_url("127.0.0.1", "35353", "/xui", "https")
        assert url == "https://127.0.0.1:35353/xui"


class TestPanelScheme:
    """Tests for panel_cert_value and panel_scheme."""

    def test_cert_configured_is_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A set certificate path means the panel serves TLS.
        monkeypatch.setattr(
            "pyntara.xui.run_command",
            lambda command, **kwargs: _FakeProc(
                0,
                "cert: /root/cert/ip/fullchain.pem\nkey: /root/cert/ip/privkey.pem\n",
            ),
        )
        assert xui_client.panel_cert_value(30) == "/root/cert/ip/fullchain.pem"
        assert xui_client.panel_scheme(30) == "https"

    def test_no_cert_is_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An empty cert value means plain HTTP.
        monkeypatch.setattr(
            "pyntara.xui.run_command",
            lambda command, **kwargs: _FakeProc(0, "cert: \nkey: \n"),
        )
        assert xui_client.panel_cert_value(30) is None
        assert xui_client.panel_scheme(30) == "http"

    def test_the_cert_query_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The argv of the query is a declared value: another template is
        # exactly what runs, with the binary path filling its {binary} slot.
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> _FakeProc:
            calls.append(list(command))
            return _FakeProc(0, "cert: /root/cert/ip/fullchain.pem\n")

        monkeypatch.setattr("pyntara.xui.run_command", fake_run)
        monkeypatch.setattr(
            panel_values, "PANEL_CERT_QUERY_COMMAND", ("mybinary", "ask", "{binary}")
        )
        assert xui_client.panel_cert_value(30) == "/root/cert/ip/fullchain.pem"
        assert calls == [
            [
                "mybinary",
                "ask",
                str(panel_values.INSTALL_DIR / panel_values.BINARY_FILE_NAME),
            ]
        ]


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
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
        }
        assert xui_client.login_and_verify(env, 5) is True

    def test_fails_on_csrf_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, csrf_ok=False)
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
        }
        assert xui_client.login_and_verify(env, 5) is False

    def test_fails_on_login_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, login_ok=False)
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "wrong",
            "XUI_PANEL_PORT": "3579",
        }
        assert xui_client.login_and_verify(env, 5) is False

    def test_fails_on_verify_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, verify_ok=False)
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
        }
        assert xui_client.login_and_verify(env, 5) is False

    def test_uses_web_base_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch)
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
            "XUI_WEB_BASE_PATH": "/xui",
        }
        assert xui_client.login_and_verify(env, 5) is True


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
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        assert xui_client.verify_bearer(env, 5) is True

    def test_fails_on_bad_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=False)
        env = {"XUI_API_TOKEN": "bad", "XUI_PANEL_PORT": "3579"}
        assert xui_client.verify_bearer(env, 5) is False


class TestListInbounds:
    """Tests for list_inbounds."""

    def test_the_api_timeout_bounds_one_panel_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The budget of a step is the engine command budget, which is hours
        # long, and the socket timeout of one call is the API timeout of
        # the section: a panel that accepts the connection and never
        # answers must be reported as a failure of that call instead of
        # stopping the run for hours. The smaller of the two values is what
        # the call gets.
        monkeypatch.setattr(panel_values, "PANEL_API_TIMEOUT_SECONDS", 7)
        recorded = _record_requests(
            monkeypatch, (200, json.dumps({"success": True, "obj": []}))
        )
        assert xui_client.list_inbounds(_ENV, 8000.0) == []
        assert recorded[0].kwargs["timeout"] == 7

        recorded.clear()
        assert xui_client.list_inbounds(_ENV, 3.0) == []
        assert recorded[0].kwargs["timeout"] == 3.0

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
                    json.dumps({"success": True, "obj": [{"id": 1, "port": 443}]}),
                )
            return (200, json.dumps({"success": False}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)

    def test_returns_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=True)
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.list_inbounds(env, 5)
        assert len(result) == 1
        assert result[0]["port"] == 443

    def test_returns_empty_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=False)
        env = {"XUI_API_TOKEN": "bad", "XUI_PANEL_PORT": "3579"}
        result = xui_client.list_inbounds(env, 5)
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
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.find_inbound_by_port(env, 443, 5)
        assert result is not None
        assert result["id"] == 1

    def test_returns_none_when_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch)
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.find_inbound_by_port(env, 9999, 5)
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
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        payload = {"remark": "test", "port": 443, "protocol": "vless"}
        ok, msg = xui_client.create_inbound(env, payload, 5)
        assert ok is True
        assert "created" in msg

    def test_rejects_duplicate_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(
            monkeypatch,
            success=False,
            msg="port 443 already used by inbound 'test' (#1)",
        )
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        payload = {"remark": "test", "port": 443, "protocol": "vless"}
        ok, msg = xui_client.create_inbound(env, payload, 5)
        assert ok is False
        assert "already used" in msg

    def test_handles_unreachable_panel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, status=0)
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        payload = {"remark": "test", "port": 443, "protocol": "vless"}
        ok, msg = xui_client.create_inbound(env, payload, 5)
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
        env = {"XUI_API_TOKEN": "tok123", "XUI_PANEL_PORT": "3579"}
        result = xui_client.generate_reality_key(env, 5)
        assert result is not None
        priv, pub = result
        assert priv == "priv123"
        assert pub == "pub123"

    def test_returns_none_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_request(monkeypatch, ok=False)
        env = {"XUI_API_TOKEN": "bad", "XUI_PANEL_PORT": "3579"}
        result = xui_client.generate_reality_key(env, 5)
        assert result is None


class TestBuildVlessRealityPayload:
    """Tests for build_vless_reality_payload."""

    def test_builds_correct_structure(self) -> None:
        # The payload is a nested JSON object, so the test narrows the
        # dict[str, object] return to a checkable shape.
        payload = cast(
            dict[str, Any],
            xui_client.build_vless_reality_payload(
                _payload_template(),
                port=443,
                remark="universal",
                dest="www.google.com:443",
                server_names=("www.google.com",),
                private_key="priv123",
                public_key="pub123",
                short_id="6ba85179e30d4fc2",
                fingerprint="chrome",
                sniffing_protocols=("http", "tls"),
            ),
        )
        assert payload["port"] == 443
        assert payload["protocol"] == "vless"
        assert payload["remark"] == "universal"
        assert payload["enable"] is True
        assert payload["settings"]["decryption"] == "none"
        assert payload["streamSettings"]["security"] == "reality"
        assert (
            payload["streamSettings"]["realitySettings"]["dest"] == "www.google.com:443"
        )
        assert payload["streamSettings"]["realitySettings"]["privateKey"] == "priv123"
        assert payload["streamSettings"]["realitySettings"]["shortIds"] == [
            "6ba85179e30d4fc2"
        ]
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
                _payload_template(),
                port=8443,
                remark="custom",
                dest="bing.com:443",
                server_names=("bing.com", "www.bing.com"),
                private_key="customkey",
                public_key="custompub",
                short_id="abc12345",
                fingerprint="firefox",
                sniffing_protocols=("http", "tls"),
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

    def test_the_document_comes_from_the_template(self) -> None:
        # The payload document is a template like any other: another
        # template with another protocol word and another field name is
        # exactly what the caller receives, and every $placeholder is
        # filled with a JSON value, so a number stays a number and a list
        # stays a list.
        payload = xui_client.build_vless_reality_payload(
            '{"protocol": "vmess", "portNumber": $port, "names": $server_names}',
            port=8443,
            remark="unused",
            dest="unused",
            server_names=("a", "b"),
            private_key="unused",
            public_key="unused",
            short_id="unused",
            fingerprint="unused",
            sniffing_protocols=("http",),
        )
        assert payload == {
            "protocol": "vmess",
            "portNumber": 8443,
            "names": ["a", "b"],
        }

    def test_a_template_that_is_not_valid_json_raises(self) -> None:
        # The filled document is parsed before it can reach the panel, so a
        # broken template fails here and never as an API error on the
        # target machine.
        with pytest.raises(ValueError):
            xui_client.build_vless_reality_payload(
                '{"port": $port,}',
                port=443,
                remark="unused",
                dest="unused",
                server_names=(),
                private_key="unused",
                public_key="unused",
                short_id="unused",
                fingerprint="unused",
                sniffing_protocols=(),
            )


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
        assert xui_client.panel_settings({"XUI_PANEL_PORT": "3579"}, 5) == {
            "subPath": "/sub/"
        }

    def test_panel_settings_none_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, read_ok=False)
        assert xui_client.panel_settings({"XUI_PANEL_PORT": "3579"}, 5) is None

    def test_update_panel_settings_reports_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch)
        ok, message = xui_client.update_panel_settings(
            {"XUI_PANEL_PORT": "3579"}, {"subPath": "/s/"}, 5
        )
        assert ok is True
        assert message == "changed"

    def test_update_panel_settings_reports_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, write_ok=False)
        ok, message = xui_client.update_panel_settings(
            {"XUI_PANEL_PORT": "3579"}, {"subPath": "/s/"}, 5
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
            {"XUI_PANEL_PORT": "3579"}, 5
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
            {"XUI_PANEL_PORT": "3579"}, 5
        )
        assert changed is False
        assert message == ""
        assert captured == []

    def test_ensure_subscription_paths_reports_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, read_ok=False)
        changed, message = xui_client.ensure_subscription_paths(
            {"XUI_PANEL_PORT": "3579"}, 5
        )
        assert changed is False
        assert message == "cannot read panel settings"


class TestUpdateInbound:
    """Tests for update_inbound."""

    def test_puts_inbound_id_in_the_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
            del opener, kwargs
            seen.append(url)
            return (200, json.dumps({"success": True, "msg": "inbound updated"}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        ok, message = xui_client.update_inbound(
            {"XUI_PANEL_PORT": "3579"}, {"id": 7, "port": 443}, 5
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
            {"XUI_PANEL_PORT": "3579"}, {"id": 7}, 5
        )
        assert ok is False
        assert message == "port busy"


class TestPanelPathsComeFromValues:
    """Every panel call uses the path declared in the values module."""

    def test_login_session_uses_the_configured_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, dict[str, object]]] = []

        def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
            del opener
            headers = kwargs.get("headers", {})
            seen.append((url, headers if isinstance(headers, dict) else {}))
            if url.endswith("/custom-csrf"):
                return (200, json.dumps({"success": True, "obj": "tok123"}))
            return (200, json.dumps({"success": True}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        monkeypatch.setattr(panel_values, "PANEL_ROOT_PATH", "/custom-root")
        monkeypatch.setattr(panel_values, "PANEL_LOGIN_PATH", "/custom-login")
        monkeypatch.setattr(panel_values, "PANEL_CSRF_TOKEN_PATH", "/custom-csrf")
        monkeypatch.setattr(
            panel_values, "PANEL_INBOUNDS_LIST_PATH", "/custom-inbounds"
        )
        env = {
            "XUI_USERNAME": "admin",
            "XUI_PASSWORD": "pass",
            "XUI_PANEL_PORT": "3579",
        }
        assert xui_client.login_and_verify(env, 5) is True
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

        def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
            del opener, kwargs
            seen.append(url)
            return (200, json.dumps({"success": True, "obj": {"links": []}}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        monkeypatch.setattr(
            panel_values,
            "PANEL_INBOUNDS_DELETE_PATH",
            "/custom/del/{inbound_id}",
        )
        monkeypatch.setattr(
            panel_values, "PANEL_CLIENT_LINKS_PATH", "/custom/links/{email}"
        )
        monkeypatch.setattr(panel_values, "PANEL_XRAY_STATUS_PATH", "/custom/xray")
        env = {"XUI_PANEL_PORT": "3579"}
        assert xui_client.delete_inbound(env, 9, 5)[0] is True
        xui_client.client_links(env, "a b", 5)
        xui_client.read_xray_template(env, 5)
        assert seen == [
            "http://127.0.0.1:3579/custom/del/9",
            "http://127.0.0.1:3579/custom/links/a%20b",
            "http://127.0.0.1:3579/custom/xray",
        ]

    def test_subscription_calls_use_the_configured_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
            del opener, kwargs
            seen.append(url)
            return (200, json.dumps({"success": True, "obj": []}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        monkeypatch.setattr(
            panel_values, "PANEL_OUTBOUND_SUBS_PATH", "/custom/subs"
        )
        monkeypatch.setattr(
            panel_values,
            "PANEL_OUTBOUND_SUBS_ITEM_PATH",
            "/custom/subs/{subscription_id}",
        )
        monkeypatch.setattr(
            panel_values,
            "PANEL_OUTBOUND_SUBS_REFRESH_PATH",
            "/custom/subs/{subscription_id}/refresh",
        )
        monkeypatch.setattr(
            panel_values, "PANEL_BALANCER_STATUS_PATH", "/custom/balancers"
        )
        env = {"XUI_PANEL_PORT": "3579"}
        xui_client.list_outbound_subscriptions(env, 5)
        assert (
            xui_client.upsert_outbound_subscription(env, {"remark": "sota-bridge"}, 5)[0]
            is True
        )
        xui_client.refresh_outbound_subscription(env, 4, 5)
        monitored = xui_client.list_balancer_status(env, ("pyntara-fastest",), 5)
        assert seen == [
            "http://127.0.0.1:3579/custom/subs",
            "http://127.0.0.1:3579/custom/subs",
            "http://127.0.0.1:3579/custom/subs",
            "http://127.0.0.1:3579/custom/subs/4/refresh",
            "http://127.0.0.1:3579/custom/balancers",
        ]
        assert monitored == []


class TestFindClient:
    """Tests for find_client."""

    def test_returns_the_client_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps({"success": True, "obj": {"client": {"email": "a-b"}}}),
            ),
        )
        assert xui_client.find_client({"XUI_PANEL_PORT": "3579"}, "a-b", 5) == {
            "email": "a-b"
        }

    def test_returns_none_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request",
            lambda _opener, _url, **_kwargs: (
                200,
                json.dumps({"success": False, "msg": "not found"}),
            ),
        )
        assert (
            xui_client.find_client({"XUI_PANEL_PORT": "3579"}, "missing", 5)
            is None
        )


class TestCreateClient:
    """Tests for create_client."""

    def test_sends_credential_email_and_inbound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[dict[str, object]] = []

        def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
            del opener
            assert url.endswith("/panel/api/clients/add")
            data = kwargs.get("data")
            if isinstance(data, bytes):
                captured.append(cast(dict[str, object], json.loads(data.decode())))
            return (200, json.dumps({"success": True, "msg": "added"}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        ok, message = xui_client.create_client(
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

    def test_reports_unreachable_panel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request", lambda _opener, _url, **_kwargs: (0, "")
        )
        ok, message = xui_client.create_client(
            {"XUI_PANEL_PORT": "3579"}, 3, "id", "mail", "sub", 5
        )
        assert ok is False
        assert message == "panel unreachable"

    def test_the_enabled_flag_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The proof of the value: a client created as a draft carries the
        # declared flag, so staging a client without enabling it is a
        # change of the value and not a code change.
        captured: list[dict[str, object]] = []

        def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
            del opener, url
            data = kwargs.get("data")
            if isinstance(data, bytes):
                captured.append(cast(dict[str, object], json.loads(data.decode())))
            return (200, json.dumps({"success": True, "msg": "added"}))

        monkeypatch.setattr("pyntara.xui._request", fake_request)
        monkeypatch.setattr(panel_values, "CLIENT_ENABLED", False)
        xui_client.create_client(
            {"XUI_PANEL_PORT": "3579"},
            3,
            "id",
            "mail",
            "sub",
            5,
        )
        client = cast(dict[str, object], captured[0]["client"])
        assert client["enable"] is False


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
            {"XUI_PANEL_PORT": "3579"}, "a-b", 5
        ) == ["vless://x@host:443"]

    def test_returns_empty_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "pyntara.xui._request", lambda _opener, _url, **_kwargs: (0, "")
        )
        assert (
            xui_client.client_links({"XUI_PANEL_PORT": "3579"}, "a-b", 5) == []
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

    def fake_request(opener: object, url: str, **kwargs: object) -> tuple[int, str]:
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
        found = xui_client.find_inbound_by_tag(_ENV, "pyntara-local-proxy", 5)
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
            xui_client.find_inbound_by_tag(_ENV, "pyntara local proxy", 5)
            is None
        )

    def test_returns_none_when_the_tag_is_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mock_request(monkeypatch, [{"id": 1, "tag": "in-443-tcp"}])
        assert (
            xui_client.find_inbound_by_tag(_ENV, "pyntara-local-proxy", 5)
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
            _ENV,
            {
                "tag": "pyntara-local-proxy",
                "remark": "pyntara local proxy",
                "port": 10800,
            },
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
        ok, message = xui_client.upsert_inbound(_ENV, {"port": 10800}, 5)
        assert ok is False
        assert "tag" in message
        assert recorded == []

    def test_reports_a_failed_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": []})),
            (200, json.dumps({"success": False, "msg": "port already used"})),
        )
        ok, message = xui_client.upsert_inbound(
            _ENV, {"tag": "pyntara-local-proxy", "port": 10800}, 5
        )
        assert ok is False
        assert message == "port already used"


class TestDeleteInbound:
    """Tests for delete_inbound."""

    def test_deletes_by_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded = _record_requests(
            monkeypatch, (200, json.dumps({"success": True, "msg": "inbound deleted"}))
        )
        ok, message = xui_client.delete_inbound(_ENV, 3, 5)
        assert ok is True
        assert message == "inbound deleted"
        assert recorded[0].url.endswith("/panel/api/inbounds/del/3")
        assert recorded[0].header("Authorization") == "Bearer tok123"

    def test_reports_an_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        ok, message = xui_client.delete_inbound(_ENV, 3, 5)
        assert ok is False
        assert message == "panel unreachable"


class TestOutboundSubscriptions:
    """Tests for the outbound subscription helpers."""

    def test_lists_the_subscriptions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded = _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": [{"id": 4, "remark": "sota-bridge"}],
                    }
                ),
            ),
        )
        subscriptions = xui_client.list_outbound_subscriptions(_ENV, 5)
        assert subscriptions == [{"id": 4, "remark": "sota-bridge"}]
        assert recorded[0].url.endswith("/panel/api/xray/outbound-subs")
        assert recorded[0].header("Authorization") == "Bearer tok123"

    def test_an_unreachable_panel_answers_no_subscriptions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        assert xui_client.list_outbound_subscriptions(_ENV, 5) == []

    def test_finds_the_subscription_by_remark(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": [
                            {"id": 1, "remark": "another"},
                            {"id": 4, "remark": "sota-bridge"},
                        ],
                    }
                ),
            ),
        )
        found = xui_client.find_outbound_subscription_by_remark(
            _ENV, "sota-bridge", 5
        )
        assert found is not None
        assert found["id"] == 4

    def test_finds_nothing_when_the_remark_is_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": [{"id": 1}]})),
        )
        assert (
            xui_client.find_outbound_subscription_by_remark(
                _ENV, "sota-bridge", 5
            )
            is None
        )

    def test_creates_the_subscription_when_the_remark_is_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": []})),
            (200, json.dumps({"success": True, "msg": "subscription added"})),
        )
        ok, message = xui_client.upsert_outbound_subscription(
            _ENV,
            {"remark": "sota-bridge", "tagPrefix": "sota-"},
            5,
        )
        assert ok is True
        assert message == "subscription added"
        assert recorded[1].url.endswith("/panel/api/xray/outbound-subs")
        assert recorded[1].header("Content-Type") == (
            "application/x-www-form-urlencoded"
        )
        assert recorded[1].form()["tagPrefix"] == "sota-"

    def test_writes_the_flags_as_the_words_the_panel_compares(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": []})),
            (200, json.dumps({"success": True, "msg": "subscription added"})),
        )
        ok, _ = xui_client.upsert_outbound_subscription(
            _ENV,
            {
                "remark": "sota-bridge",
                "url": "http://127.0.0.1:25080/sub/sample/raw",
                "enabled": True,
                "allowPrivate": True,
                "allowInsecure": False,
                "prepend": False,
                "updateInterval": 300,
            },
            5,
        )
        assert ok is True
        assert recorded[1].form() == {
            "remark": "sota-bridge",
            "url": "http://127.0.0.1:25080/sub/sample/raw",
            "enabled": "true",
            "allowPrivate": "true",
            "allowInsecure": "false",
            "prepend": "false",
            "updateInterval": "300",
        }

    def test_replaces_the_subscription_that_carries_the_remark(
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
                            {"id": 4, "remark": "sota-bridge", "url": "http://old"}
                        ],
                    }
                ),
            ),
            (200, json.dumps({"success": True, "msg": "subscription updated"})),
        )
        ok, message = xui_client.upsert_outbound_subscription(
            _ENV,
            {"remark": "sota-bridge", "url": "http://new"},
            5,
        )
        assert ok is True
        assert message == "subscription sota-bridge updated: subscription updated"
        assert recorded[1].url.endswith("/panel/api/xray/outbound-subs/4")
        assert recorded[1].header("Content-Type") == (
            "application/x-www-form-urlencoded"
        )
        assert recorded[1].form()["url"] == "http://new"

    def test_refuses_a_payload_without_a_remark(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(monkeypatch, (200, "{}"))
        ok, message = xui_client.upsert_outbound_subscription(
            _ENV, {"url": "http://new"}, 5
        )
        assert ok is False
        assert "remark" in message
        assert recorded == []

    def test_reports_a_failed_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "obj": []})),
            (200, json.dumps({"success": False, "msg": "url not allowed"})),
        )
        ok, message = xui_client.upsert_outbound_subscription(
            _ENV, {"remark": "sota-bridge"}, 5
        )
        assert ok is False
        assert message == "url not allowed"

    def test_refreshes_the_subscription_by_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _record_requests(
            monkeypatch,
            (200, json.dumps({"success": True, "msg": "refreshed"})),
        )
        ok, message = xui_client.refresh_outbound_subscription(_ENV, 4, 5)
        assert ok is True
        assert message == "refreshed"
        assert recorded[0].url.endswith("/panel/api/xray/outbound-subs/4/refresh")
        assert recorded[0].header("Authorization") == "Bearer tok123"


class TestBalancerStatus:
    """Tests for list_balancer_status."""

    def test_asks_every_tag_in_one_form_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel answers this endpoint only to a form call, and its obj
        # is an object keyed by the balancer tag, not a list.
        recorded = _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": {
                            "pyntara-fastest": {
                                "tag": "pyntara-fastest",
                                "running": True,
                                "override": "",
                                "selected": ["sota-node-1"],
                            }
                        },
                    }
                ),
            ),
        )
        entries = xui_client.list_balancer_status(_ENV, ("pyntara-fastest",), 5)
        assert entries == [
            {
                "tag": "pyntara-fastest",
                "running": True,
                "override": "",
                "selected": ["sota-node-1"],
            }
        ]
        assert recorded[0].url.endswith("/panel/api/xray/balancerStatus")
        assert recorded[0].form() == {"tags": "pyntara-fastest"}
        assert recorded[0].header("Content-Type") == "application/x-www-form-urlencoded"

    def test_accepts_a_list_of_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": [{"tag": "pyntara-fastest", "selected": "sota-node-1"}],
                    }
                ),
            ),
        )
        assert xui_client.list_balancer_status(
            _ENV, ("pyntara-fastest",), 5
        ) == [{"tag": "pyntara-fastest", "selected": "sota-node-1"}]

    def test_an_unreachable_panel_answers_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        assert (
            xui_client.list_balancer_status(_ENV, ("pyntara-fastest",), 5) == []
        )

    def test_drops_entries_that_are_not_objects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": {"good": {"tag": "good"}, "bad": "nonsense"},
                    }
                ),
            ),
        )
        assert xui_client.list_balancer_status(_ENV, ("x",), 5) == [
            {"tag": "good"}
        ]


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
        template = xui_client.read_xray_template(_ENV, 5)
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
        template = xui_client.read_xray_template(_ENV, 5)
        assert template is not None
        assert template.settings == {"outbounds": [{"tag": "direct"}]}

    def test_reports_nothing_when_the_panel_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        assert xui_client.read_xray_template(_ENV, 5) is None

    def test_reports_nothing_on_an_unreadable_blob(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch, (200, json.dumps({"success": True, "obj": "not json"}))
        )
        assert xui_client.read_xray_template(_ENV, 5) is None

    def test_reports_nothing_when_the_document_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {"success": True, "obj": json.dumps({"outboundTestUrl": ""})}
                ),
            ),
        )
        assert xui_client.read_xray_template(_ENV, 5) is None


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
        ok, message = xui_client.write_xray_template(_ENV, template, 5)
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
        ok, message = xui_client.write_xray_template(_ENV, template, 5)
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
            _ENV,
            panel_values.PANEL_GEODATA_DOMAIN_KIND,
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
            _ENV,
            panel_values.PANEL_GEODATA_IP_KIND,
            ["geoip:private", "200::/7"],
            5,
        )
        assert set(rejected) == {"geoip:private", "200::/7"}
        assert rejected["geoip:private"] == "panel unreachable"

    def test_asks_nothing_without_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded = _record_requests(monkeypatch, (200, "{}"))
        assert (
            xui_client.validate_geodata_tokens(
                _ENV, panel_values.PANEL_GEODATA_IP_KIND, [], 5
            )
            == {}
        )
        assert recorded == []

    def test_rejects_an_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="unknown geodata kind"):
            xui_client.validate_geodata_tokens(_ENV, "hostname", ["x"], 5)


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
                    {
                        "success": True,
                        "obj": {"matched": True, "outboundTag": "pyntara-tor"},
                    }
                ),
            ),
        )
        matched, answer = xui_client.route_test(
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
            _ENV,
            inbound_tag="pyntara-local-proxy",
            network=panel_values.ROUTE_TEST_NETWORK,
            protocol=panel_values.ROUTE_TEST_PROTOCOL,
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
            (
                200,
                json.dumps(
                    {"success": True, "obj": {"matched": False, "outboundTag": ""}}
                ),
            ),
        )
        matched, answer = xui_client.route_test(
            _ENV,
            inbound_tag="pyntara-local-proxy",
            network=panel_values.ROUTE_TEST_NETWORK,
            protocol=panel_values.ROUTE_TEST_PROTOCOL,
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
            _ENV,
            inbound_tag="pyntara-local-proxy",
            network=panel_values.ROUTE_TEST_NETWORK,
            protocol=panel_values.ROUTE_TEST_PROTOCOL,
            domain="example.com",
            timeout=5,
        )
        # An answer of the panel that carries no decision is None and not
        # False: the caller must be able to tell a refused question from a
        # core that has not answered yet.
        assert matched is None
        assert answer == "invalid inbound tag"

    def test_reports_an_unreachable_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _record_requests(monkeypatch, (0, ""))
        matched, answer = xui_client.route_test(
            _ENV,
            inbound_tag="pyntara-local-proxy",
            network=panel_values.ROUTE_TEST_NETWORK,
            protocol=panel_values.ROUTE_TEST_PROTOCOL,
            domain="example.com",
            timeout=5,
        )
        assert matched is None
        assert answer == "panel unreachable"

    def test_refuses_a_request_without_a_destination(self) -> None:
        with pytest.raises(ValueError, match="domain or an address"):
            xui_client.route_test(
                _ENV,
                inbound_tag="pyntara-local-proxy",
                network=panel_values.ROUTE_TEST_NETWORK,
                protocol=panel_values.ROUTE_TEST_PROTOCOL,
                timeout=5,
            )


class TestCoreDiagnostics:
    """Tests for core_diagnostics."""

    def test_names_the_core_state_and_the_last_core_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A warning about a core that never answered quotes the panel
        # itself: the state it reports about the core and the last line
        # the core printed.
        _record_requests(
            monkeypatch,
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": {
                            "xray": {
                                "state": "stopped",
                                "errorMsg": "process exited",
                            }
                        },
                    }
                ),
            ),
            (
                200,
                json.dumps(
                    {
                        "success": True,
                        "obj": "starting\nfailed to load geodata\n",
                    }
                ),
            ),
        )
        text = xui_client.core_diagnostics(_ENV, 5)
        assert "the panel reports its core stopped" in text
        assert "with the error process exited" in text
        assert "'failed to load geodata'" in text

    def test_reports_a_panel_that_does_not_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A panel that does not answer is named as such and the call never
        # raises: this runs while a warning is composed.
        _record_requests(monkeypatch, (0, ""), (0, ""))
        assert (
            xui_client.core_diagnostics(_ENV, 5) == "the panel did not answer"
        )


def test_the_panel_vocabulary_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The sniffing protocols of the universal inbound and the two kinds of
    # the geodata check are declared values: another set of them is the
    # payload the panel receives and the kind it is asked about.
    monkeypatch.setattr(
        panel_values, "INBOUND_SNIFFING_PROTOCOLS", ("my-http", "my-tls")
    )
    monkeypatch.setattr(panel_values, "PANEL_GEODATA_DOMAIN_KIND", "my-domain")
    monkeypatch.setattr(panel_values, "PANEL_GEODATA_IP_KIND", "my-ip")
    payload = cast(
        dict[str, Any],
        xui_client.build_vless_reality_payload(
            _payload_template(),
            port=443,
            remark="universal",
            dest="www.google.com:443",
            server_names=("www.google.com",),
            private_key="priv123",
            public_key="pub123",
            short_id="6ba85179e30d4fc2",
            fingerprint="chrome",
            sniffing_protocols=panel_values.INBOUND_SNIFFING_PROTOCOLS,
        ),
    )
    assert payload["sniffing"]["destOverride"] == ["my-http", "my-tls"]
    recorded = _record_requests(
        monkeypatch, (200, json.dumps({"success": True, "obj": []}))
    )
    assert (
        xui_client.validate_geodata_tokens(
            _ENV,
            panel_values.PANEL_GEODATA_DOMAIN_KIND,
            ["geosite:openai"],
            5,
        )
        == {}
    )
    assert recorded[0].form()["kind"] == "my-domain"
    with pytest.raises(ValueError, match="unknown geodata kind"):
        xui_client.validate_geodata_tokens(_ENV, "domain", ["x"], 5)


def test_the_http_vocabulary_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The proof of the value: another header name, another header value,
    # another success field and another payload field of the panel table
    # are the request the client sends and the answer it reads, so a panel
    # version that renames a field is answered in the values module.
    monkeypatch.setattr(
        panel_values,
        "PANEL_HTTP_HEADERS",
        {
            "content_type": "X-Content",
            "csrf_token": "X-CSRF",
            "requested_with": "X-Wanted",
            "referer": "X-Referer",
            "authorization": "X-Auth",
        },
    )
    monkeypatch.setattr(
        panel_values,
        "PANEL_HTTP_HEADER_VALUES",
        {
            "json": "my/json",
            "form": "my/form",
            "xml_http_request": "my-wanted",
            "bearer_prefix": "Token ",
        },
    )
    monkeypatch.setattr(
        panel_values, "PANEL_HTTP_METHODS", {"post": "PUT", "get": "GET"}
    )
    monkeypatch.setattr(
        panel_values,
        "PANEL_ANSWER_KEYS",
        {
            "success": "ok",
            "payload": "data",
            "message": "note",
            "token": "token",
            "reason": "reason",
        },
    )
    monkeypatch.setattr(
        panel_values,
        "PANEL_FIELD_KEYS",
        {**panel_values.PANEL_FIELD_KEYS, "port": "listenPort"},
    )
    recorded = _record_requests(
        monkeypatch, (200, json.dumps({"ok": True, "data": [{"listenPort": 443}]}))
    )
    inbounds = xui_client.list_inbounds(_ENV, 5)
    assert inbounds == [{"listenPort": 443}]
    assert recorded[0].header("X-Wanted") == "my-wanted"
    found = xui_client.find_inbound_by_port(_ENV, 443, 5)
    assert found == {"listenPort": 443}
    assert xui_client._message_result(
        200, json.dumps({"ok": True, "note": "fine"}), "x"
    ) == (True, "fine")
