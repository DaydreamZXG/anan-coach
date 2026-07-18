from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from app.core.config import get_settings
from app.domains.learning_packages.schemas import PacketRenderRequest, PacketRenderResult
from app.internal_services.packet_renderer import InternalPacketRenderer
from app.internal_services.scanner import InternalObjectScanner, ScanRequest


router = APIRouter(prefix="/internal-runtime", include_in_schema=False)


@router.post("/v1/private-packets/render", response_model=PacketRenderResult)
def render_private_packet(
    command: PacketRenderRequest,
    request: Request,
    response: Response,
) -> PacketRenderResult:
    settings = get_settings()
    _authorize(
        request,
        expected_token=settings.packet_renderer_api_key,
        release_header="X-Renderer-Release",
        expected_release=settings.packet_renderer_release_id,
    )
    request_id = str(request.state.request_id)
    response.headers[settings.packet_renderer_request_id_header] = request_id
    try:
        return InternalPacketRenderer(settings=settings).render(
            request=command,
            provider_request_id=request_id,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/private-object-scans")
def scan_private_object(command: ScanRequest, request: Request) -> dict[str, object]:
    settings = get_settings()
    _authorize(
        request,
        expected_token=settings.upload_scanner_api_key,
        release_header="X-Scanner-Release",
        expected_release=settings.upload_scanner_release_id,
    )
    try:
        return InternalObjectScanner(settings).scan(
            command,
            scan_id=str(request.state.request_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _authorize(
    request: Request,
    *,
    expected_token: str,
    release_header: str,
    expected_release: str,
) -> None:
    supplied = request.headers.get("Authorization", "")
    token = supplied.removeprefix("Bearer ") if supplied.startswith("Bearer ") else ""
    if not expected_token or not hmac.compare_digest(token, expected_token):
        raise HTTPException(status_code=401, detail="internal service credential rejected")
    if not expected_release or not hmac.compare_digest(
        request.headers.get(release_header, ""), expected_release
    ):
        raise HTTPException(status_code=409, detail="internal service release drifted")
