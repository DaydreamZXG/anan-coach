from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SpeechToTextRequest(StrictModel):
    audio_base64: Annotated[str, StringConstraints(min_length=4, max_length=1_000_000)] = Field(
        alias="audioBase64"
    )
    encoding: Literal["lame"] = "lame"
    duration_milliseconds: int = Field(alias="durationMilliseconds", gt=0, le=60_000)


class SpeechToTextResponse(StrictModel):
    transcript: Annotated[str, StringConstraints(max_length=10_000)]
    provider_request_id: str = Field(alias="providerRequestId", min_length=1, max_length=200)


class TextToSpeechRequest(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)]


class TextToSpeechResponse(StrictModel):
    audio_base64: str = Field(alias="audioBase64", min_length=4, max_length=8_000_000)
    mime_type: Literal["audio/mpeg"] = Field(default="audio/mpeg", alias="mimeType")
    provider_request_id: str = Field(alias="providerRequestId", min_length=1, max_length=200)
