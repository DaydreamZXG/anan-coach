from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import time
from typing import Callable

from websockets.sync.client import ClientConnection, connect

from app.domains.xfyun.auth import websocket_auth_url
from app.domains.xfyun.config import XfyunCredentials


IAT_ENDPOINT = "wss://iat-api.xfyun.cn/v2/iat"
TTS_ENDPOINT = "wss://tts-api.xfyun.cn/v2/tts"


class XfyunSpeechError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpeechTranscript:
    text: str
    request_id: str


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    audio: bytes
    request_id: str


class XfyunSpeechClient:
    def __init__(
        self,
        credentials: XfyunCredentials,
        *,
        connection_factory: Callable[[str], ClientConnection] | None = None,
        frame_delay_seconds: float = 0.04,
    ) -> None:
        self._credentials = credentials
        self._connection_factory = connection_factory or (
            lambda url: connect(url, open_timeout=10, close_timeout=5, max_size=8_000_000)
        )
        self._frame_delay_seconds = frame_delay_seconds

    def transcribe_mp3(self, audio: bytes) -> SpeechTranscript:
        if not audio or len(audio) > 750_000:
            raise XfyunSpeechError("录音为空或超过 60 秒听写容量。")
        url = websocket_auth_url(IAT_ENDPOINT, self._credentials)
        try:
            with self._connection_factory(url) as websocket:
                chunks = tuple(audio[index : index + 1280] for index in range(0, len(audio), 1280))
                for index, chunk in enumerate(chunks):
                    status = 0 if index == 0 else 1
                    request: dict[str, object] = {
                        "data": {
                            "status": status,
                            "format": "audio/L16;rate=16000",
                            "encoding": "lame",
                            "audio": base64.b64encode(chunk).decode(),
                        }
                    }
                    if index == 0:
                        request["common"] = {"app_id": self._credentials.app_id}
                        request["business"] = {
                            "language": "zh_cn",
                            "domain": "iat",
                            "accent": "mandarin",
                            "ptt": 1,
                        }
                    websocket.send(json.dumps(request, ensure_ascii=False))
                    if self._frame_delay_seconds:
                        time.sleep(self._frame_delay_seconds)
                websocket.send(json.dumps({"data": {"status": 2}}))
                return _receive_transcript(websocket)
        except XfyunSpeechError:
            raise
        except Exception as exc:
            raise XfyunSpeechError("语音听写服务暂时不可用。") from exc

    def synthesize(self, text: str) -> SynthesizedSpeech:
        encoded_text = text.strip().encode("utf-8")
        if not encoded_text or len(encoded_text) >= 8_000:
            raise XfyunSpeechError("朗读文字为空或过长。")
        url = websocket_auth_url(TTS_ENDPOINT, self._credentials)
        try:
            with self._connection_factory(url) as websocket:
                websocket.send(
                    json.dumps(
                        {
                            "common": {"app_id": self._credentials.app_id},
                            "business": {
                                "aue": "lame",
                                "sfl": 1,
                                "auf": "audio/L16;rate=16000",
                                "vcn": "xiaoyan",
                                "speed": 50,
                                "volume": 50,
                                "pitch": 50,
                                "tte": "UTF8",
                            },
                            "data": {
                                "status": 2,
                                "text": base64.b64encode(encoded_text).decode(),
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                return _receive_audio(websocket)
        except XfyunSpeechError:
            raise
        except Exception as exc:
            raise XfyunSpeechError("语音合成服务暂时不可用。") from exc


def _receive_transcript(websocket: ClientConnection) -> SpeechTranscript:
    pieces: list[str] = []
    request_id = "unknown"
    for raw_message in websocket:
        message = json.loads(raw_message)
        request_id = str(message.get("sid") or request_id)
        if int(message.get("code", -1)) != 0:
            raise XfyunSpeechError(str(message.get("message") or "语音听写失败。"))
        data = message.get("data") or {}
        result = data.get("result") or {}
        piece = "".join(
            str(candidate.get("w") or "")
            for word in result.get("ws", [])
            for candidate in word.get("cw", [])[:1]
            if isinstance(candidate, dict)
        )
        if piece:
            pieces.append(piece)
        if int(data.get("status", 0)) == 2:
            break
    return SpeechTranscript(text="".join(pieces).strip(), request_id=request_id)


def _receive_audio(websocket: ClientConnection) -> SynthesizedSpeech:
    chunks: list[bytes] = []
    request_id = "unknown"
    for raw_message in websocket:
        if isinstance(raw_message, bytes):
            chunks.append(raw_message)
            continue
        message = json.loads(raw_message)
        request_id = str(message.get("sid") or request_id)
        if int(message.get("code", -1)) != 0:
            raise XfyunSpeechError(str(message.get("message") or "语音合成失败。"))
        data = message.get("data")
        if not isinstance(data, dict):
            continue
        audio = str(data.get("audio") or "")
        if audio:
            chunks.append(base64.b64decode(audio, validate=True))
        if int(data.get("status", 0)) == 2:
            break
    result = b"".join(chunks)
    if not result:
        raise XfyunSpeechError("语音合成没有返回可播放音频。")
    return SynthesizedSpeech(audio=result, request_id=request_id)
