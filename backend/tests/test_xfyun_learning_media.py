from __future__ import annotations

import base64
from datetime import UTC, datetime
import json

import httpx

from app.domains.xfyun.auth import formula_headers, websocket_auth_url
from app.domains.xfyun.config import XfyunCredentials
from app.domains.xfyun.formula import XfyunFormulaClient
from app.domains.xfyun.speech import XfyunSpeechClient


CREDENTIALS = XfyunCredentials(app_id="app-test", api_key="key-test", api_secret="secret-test")


class FakeWebsocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.sent: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def __iter__(self):
        return iter(self.messages)


def test_auth_signatures_never_embed_api_secret() -> None:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    websocket_url = websocket_auth_url("wss://iat-api.xfyun.cn/v2/iat", CREDENTIALS, now=now)
    headers = formula_headers(b"{}", CREDENTIALS, now=now)

    assert "secret-test" not in websocket_url
    assert "secret-test" not in json.dumps(headers)
    assert "authorization=" in websocket_url
    assert headers["Digest"].startswith("SHA-256=")


def test_formula_client_preserves_reading_order_and_latex() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["digest"].startswith("SHA-256=")
        return httpx.Response(
            200,
            json={
                "sid": "formula-1",
                "code": 0,
                "data": {
                    "region": [
                        {
                            "type": "text",
                            "recog": {
                                "content": "已知 ifly-latex-begin\\frac{a}{b}ifly-latex-end = 2",
                                "exception": 0,
                                "element": [{"content": "已知", "conf": 0.8}],
                            },
                        },
                        {
                            "type": "text",
                            "recog": {
                                "content": "求 a",
                                "exception": 0,
                                "element": [{"content": "求", "conf": 1}],
                            },
                        },
                    ]
                },
            },
        )

    client = XfyunFormulaClient(
        CREDENTIALS,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.recognize(b"jpeg")

    assert result.request_id == "formula-1"
    assert result.has_formula is True
    assert [line.text for line in result.lines] == ["已知 $\\frac{a}{b}$ = 2", "求 a"]
    assert result.lines[0].confidence == 0.8


def test_mp3_dictation_is_an_editable_text_candidate() -> None:
    websocket = FakeWebsocket(
        [
            json.dumps(
                {
                    "sid": "iat-1",
                    "code": 0,
                    "data": {
                        "status": 2,
                        "result": {"ws": [{"cw": [{"w": "我把条件看反了。"}]}]},
                    },
                }
            )
        ]
    )
    client = XfyunSpeechClient(
        CREDENTIALS,
        connection_factory=lambda _url: websocket,
        frame_delay_seconds=0,
    )

    result = client.transcribe_mp3(b"mp3-bytes")

    assert result.text == "我把条件看反了。"
    assert result.request_id == "iat-1"
    first = json.loads(websocket.sent[0])
    assert first["data"]["encoding"] == "lame"
    assert json.loads(websocket.sent[-1]) == {"data": {"status": 2}}


def test_text_to_speech_returns_mp3_bytes() -> None:
    expected = b"mp3-result"
    websocket = FakeWebsocket(
        [
            json.dumps(
                {
                    "sid": "tts-1",
                    "code": 0,
                    "data": {
                        "status": 2,
                        "audio": base64.b64encode(expected).decode(),
                    },
                }
            )
        ]
    )
    client = XfyunSpeechClient(CREDENTIALS, connection_factory=lambda _url: websocket)

    result = client.synthesize("把条件读慢一点")

    assert result.audio == expected
    assert result.request_id == "tts-1"
    request = json.loads(websocket.sent[0])
    assert request["business"]["aue"] == "lame"
    assert base64.b64decode(request["data"]["text"]).decode() == "把条件读慢一点"
