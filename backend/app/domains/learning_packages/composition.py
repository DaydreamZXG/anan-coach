from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

import tos
from app.core.config import Settings
from app.domains.content_catalog.licensing.service import ContentLicenseService
from app.domains.learning_packages.renderer import (
    HttpPacketRenderer,
    PacketRendererConfiguration,
)
from app.domains.learning_packages.worker import PacketRenderWorker
from app.domains.learning_packages.service import LearningPackageService
from sqlalchemy.orm import Session


class TosPacketArtifactAccessSigner:
    def __init__(self, settings: Settings) -> None:
        if not all(
            value.strip()
            for value in (
                settings.tos_access_key,
                settings.tos_secret_key,
                settings.tos_endpoint,
                settings.tos_region,
                settings.tos_bucket,
            )
        ):
            raise ValueError("TOS packet access configuration is incomplete")
        self._bucket = settings.tos_bucket
        self._client = tos.TosClientV2(
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
        )

    def sign_download(self, *, artifact_ref: str, expires_at: datetime) -> str:
        parsed = urlsplit(artifact_ref)
        if (
            parsed.scheme != "tos"
            or parsed.netloc != self._bucket
            or not parsed.path.startswith("/private/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("packet artifact is not in the approved private TOS bucket")
        seconds = int((expires_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
        if not 1 <= seconds <= 900:
            raise ValueError("packet access authorization must expire within 15 minutes")
        output = self._client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get,
            self._bucket,
            parsed.path.removeprefix("/"),
            expires=seconds,
        )
        url = str(output.signed_url)
        if not url.startswith("https://"):
            raise ValueError("packet download authorization must use HTTPS")
        return url


def build_learning_package_service(
    *, session: Session, settings: Settings
) -> LearningPackageService:
    return LearningPackageService(
        session=session,
        license_evaluator=ContentLicenseService(session=session),
        access_signer=TosPacketArtifactAccessSigner(settings),
    )


def build_packet_render_worker(
    *, session: Session, settings: Settings
) -> PacketRenderWorker:
    renderer = HttpPacketRenderer(
        PacketRendererConfiguration(
            base_url=settings.packet_renderer_base_url,
            api_key=settings.packet_renderer_api_key,
            release_id=settings.packet_renderer_release_id,
            https_host_allowlist=tuple(
                item.strip()
                for item in settings.packet_renderer_https_host_allowlist.split(",")
                if item.strip()
            ),
            dpa_confirmed=settings.packet_renderer_dpa_confirmed,
            idempotency_header=settings.packet_renderer_idempotency_header,
            request_id_header=settings.packet_renderer_request_id_header,
        )
    )
    return PacketRenderWorker(
        renderer=renderer,
        license_evaluator=ContentLicenseService(session=session),
    )
