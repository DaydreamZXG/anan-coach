from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import httpx
import jwt
import pytest

from app.domains.daily_opening.luckin_mcp import (
    EnvelopeTokenCipher,
    LuckinMcpClient,
    LuckinMcpError,
    OrderConfirmation,
    fingerprint,
)
from app.domains.daily_opening.settings import (
    DailyOpeningConfigurationError,
    DailyOpeningSettings,
)
from app.domains.daily_opening.weatherkit import WeatherKitClient


NOW = datetime(2026, 7, 21, 0, 30, tzinfo=UTC)


def _settings(tmp_path, private_key: str) -> DailyOpeningSettings:
    key_path = tmp_path / "AuthKey_TESTKEY.p8"
    key_path.write_text(private_key, encoding="utf-8")
    return DailyOpeningSettings(
        weatherkit_enabled=True,
        weatherkit_team_id="TEAM123456",
        weatherkit_key_id="KEY1234567",
        weatherkit_service_id="cn.xingguoxian.anan.weather",
        weatherkit_private_key_path=key_path,
        weatherkit_base_url="https://weatherkit.apple.com",
        weatherkit_language="zh-Hans",
        luckin_mcp_enabled=False,
        luckin_mcp_endpoint="https://gwmcp.lkcoffee.com/order/user/mcp",
        luckin_token_key_id="",
        luckin_token_encryption_key="",
    )


def test_weatherkit_signs_exact_server_request_and_preserves_attribution(tmp_path) -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    settings = _settings(tmp_path, private_pem)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "weatherkit.apple.com"
        assert request.url.path == "/api/v1/weather/zh-Hans/28.682/115.858"
        assert request.url.params["dataSets"] == "currentWeather,forecastDaily"
        assert request.url.params["timezone"] == "Asia/Shanghai"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(
            token,
            private.public_key(),
            algorithms=["ES256"],
            options={"verify_exp": False},
        )
        assert header == {
            "alg": "ES256",
            "id": "TEAM123456.cn.xingguoxian.anan.weather",
            "kid": "KEY1234567",
            "typ": "JWT",
        }
        assert claims["iss"] == "TEAM123456"
        assert claims["sub"] == "cn.xingguoxian.anan.weather"
        assert claims["exp"] - claims["iat"] == 300
        return httpx.Response(
            200,
            json={
                "currentWeather": {
                    "conditionCode": "PartlyCloudy",
                    "temperature": 31.4,
                    "metadata": {
                        "attributionURL": "https://weatherkit.apple.com/legal-attribution.html",
                        "providerName": "Apple Weather",
                        "readTime": NOW.isoformat(),
                        "expireTime": (NOW + timedelta(minutes=20)).isoformat(),
                    },
                },
                "forecastDaily": {
                    "days": [
                        {
                            "temperatureMin": 26.2,
                            "temperatureMax": 34.7,
                            "precipitationChance": 0.45,
                        }
                    ]
                },
            },
        )

    snapshot = WeatherKitClient(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    ).fetch(latitude=28.682, longitude=115.858)

    assert snapshot.condition_code == "PartlyCloudy"
    assert snapshot.attribution_url == "https://weatherkit.apple.com/legal-attribution.html"
    assert snapshot.minimum_celsius == 26.2
    assert snapshot.maximum_celsius == 34.7
    assert snapshot.precipitation_chance == 0.45
    assert len(snapshot.fingerprint()) == 64


def test_provider_settings_reject_untrusted_endpoints(tmp_path) -> None:
    settings = _settings(tmp_path, "unused")
    untrusted = DailyOpeningSettings(
        **{
            **settings.__dict__,
            "weatherkit_enabled": False,
            "weatherkit_base_url": "http://weatherkit.apple.com",
        }
    )
    with pytest.raises(DailyOpeningConfigurationError, match="WeatherKit endpoint"):
        untrusted.validate()

    untrusted_luckin = DailyOpeningSettings(
        **{
            **settings.__dict__,
            "weatherkit_enabled": False,
            "weatherkit_base_url": "https://weatherkit.apple.com",
            "luckin_mcp_endpoint": "https://evil.example/order/user/mcp",
        }
    )
    with pytest.raises(DailyOpeningConfigurationError, match="Luckin MCP endpoint"):
        untrusted_luckin.validate()


def test_luckin_token_cipher_is_context_bound_and_never_stores_plaintext() -> None:
    encoded_key = urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    cipher = EnvelopeTokenCipher(key_id="luckin-key-v1", encoded_key=encoded_key)
    encrypted = cipher.encrypt(
        "user-secret-token",
        user_id="user-1",
        connection_id="connection-1",
    )
    assert "user-secret-token" not in encrypted.ciphertext
    assert len(encrypted.fingerprint) == 64
    assert cipher.decrypt(
        encrypted.ciphertext,
        user_id="user-1",
        connection_id="connection-1",
    ) == "user-secret-token"
    with pytest.raises(LuckinMcpError, match="ciphertext or context"):
        cipher.decrypt(
            encrypted.ciphertext,
            user_id="user-2",
            connection_id="connection-1",
        )


def test_luckin_mcp_uses_protocol_session_allowlist_and_single_use_confirmation() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://gwmcp.lkcoffee.com/order/user/mcp"
        assert request.headers["Authorization"] == "Bearer user-secret-token"
        payload = json.loads(request.content)
        method = payload["method"]
        calls.append(method)
        if method == "initialize":
            assert "MCP-Protocol-Version" not in request.headers
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-11-25", "capabilities": {}},
                },
            )
        assert request.headers["MCP-Protocol-Version"] == "2025-11-25"
        assert request.headers["Mcp-Session-Id"] == "session-1"
        if method == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": "ok"}]},
            },
        )

    client = LuckinMcpClient(
        endpoint="https://gwmcp.lkcoffee.com/order/user/mcp",
        bearer_token="user-secret-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )
    with pytest.raises(LuckinMcpError, match="not permitted"):
        client.browse("createOrder", {})

    assert client.browse("queryShopList", {"city": "南昌"})["content"][0]["text"] == "ok"
    assert calls == ["initialize", "notifications/initialized", "tools/call"]

    preview = {"shopId": "shop-1", "skuId": "sku-1", "amount": 12.0}
    confirmation = OrderConfirmation(
        confirmation_id="confirmation-1",
        user_id="user-1",
        preview_fingerprint=fingerprint(preview),
        expires_at=NOW + timedelta(minutes=5),
    )
    assert client.create_order(
        user_id="user-1",
        preview_payload=preview,
        arguments={"previewId": "preview-1"},
        confirmation=confirmation,
    )["content"][0]["text"] == "ok"
    assert confirmation.used_at == NOW
    with pytest.raises(LuckinMcpError, match="already used"):
        client.create_order(
            user_id="user-1",
            preview_payload=preview,
            arguments={"previewId": "preview-1"},
            confirmation=confirmation,
        )
