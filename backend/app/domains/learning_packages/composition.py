from __future__ import annotations

from app.core.config import Settings
from app.domains.content_catalog.licensing.service import ContentLicenseService
from app.domains.learning_packages.renderer import (
    HttpPacketRenderer,
    PacketRendererConfiguration,
)
from app.domains.learning_packages.worker import PacketRenderWorker
from app.domains.learning_packages.service import LearningPackageService
from app.platform.private_object_access import TosPrivateObjectAccessSigner
from sqlalchemy.orm import Session


class TosPacketArtifactAccessSigner(TosPrivateObjectAccessSigner):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, maximum_ttl_seconds=900)


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
