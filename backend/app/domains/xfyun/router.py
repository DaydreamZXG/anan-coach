from __future__ import annotations

import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.xfyun.config import XfyunConfigurationError, credentials_from_environment
from app.domains.xfyun.schemas import (
    SpeechToTextRequest,
    SpeechToTextResponse,
    TextToSpeechRequest,
    TextToSpeechResponse,
)
from app.domains.xfyun.speech import XfyunSpeechClient, XfyunSpeechError
from app.platform.http import COMMON_API_ERROR_RESPONSES, PublicApiEnvelope, public_success_envelope


router = APIRouter(
    prefix="/api/v2/xfyun",
    tags=["xfyun"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_xfyun_speech_client() -> XfyunSpeechClient:
    return XfyunSpeechClient(credentials_from_environment())


@router.post("/speech-to-text", response_model=PublicApiEnvelope[SpeechToTextResponse])
def speech_to_text(
    command: SpeechToTextRequest,
    request: Request,
    response: Response,
    _current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    client: Annotated[XfyunSpeechClient, Depends(get_xfyun_speech_client)],
) -> PublicApiEnvelope[SpeechToTextResponse]:
    try:
        audio = base64.b64decode(command.audio_base64, validate=True)
        result = client.transcribe_mp3(audio)
        return public_success_envelope(
            request=request,
            response=response,
            data=SpeechToTextResponse(
                transcript=result.text,
                providerRequestId=result.request_id,
            ),
        )
    except (binascii.Error, XfyunConfigurationError, XfyunSpeechError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/text-to-speech", response_model=PublicApiEnvelope[TextToSpeechResponse])
def text_to_speech(
    command: TextToSpeechRequest,
    request: Request,
    response: Response,
    _current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    client: Annotated[XfyunSpeechClient, Depends(get_xfyun_speech_client)],
) -> PublicApiEnvelope[TextToSpeechResponse]:
    try:
        result = client.synthesize(command.text)
        return public_success_envelope(
            request=request,
            response=response,
            data=TextToSpeechResponse(
                audioBase64=base64.b64encode(result.audio).decode(),
                providerRequestId=result.request_id,
            ),
        )
    except (XfyunConfigurationError, XfyunSpeechError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
