from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from pypdf import PdfReader

from app.core.config import Settings
from app.domains.learning_packages.schemas import PacketRenderContent, PacketRenderRequest
from app.internal_services.packet_renderer import InternalPacketRenderer, RenderedArtifact


class MemoryWriter:
    def __init__(self) -> None:
        self.artifacts: list[RenderedArtifact] = []

    def put_private(self, artifact: RenderedArtifact) -> None:
        self.artifacts.append(artifact)


def test_internal_renderer_produces_private_chinese_pdf_and_note_file() -> None:
    writer = MemoryWriter()
    renderer = InternalPacketRenderer(
        settings=Settings(tos_bucket="private-test-bucket"), writer=writer
    )
    request = PacketRenderRequest(
        packetVersionId=uuid4(),
        ownerSubjectRef="a" * 64,
        renderFormat="pdf",
        manifestSha256="b" * 64,
        expiresAt=(datetime.now(UTC) + timedelta(days=14)).isoformat(),
        contents=(
            PacketRenderContent(
                questionId="question-1",
                contentVersionId="c" * 64,
                orderNo=1,
                answerBindingCode="answer-binding-1",
                track="xingce",
                title="资料分析",
                stem="下列说法正确的是？",
                materialText="2025 年某地区生产总值增长。",
                options=(
                    {"label": "A", "text": "选项甲"},
                    {"label": "B", "text": "选项乙"},
                ),
            ),
        ),
    )

    pdf = renderer.render(request=request, provider_request_id="request-pdf")
    note = renderer.render(
        request=request.model_copy(update={"render_format": "note_app_file"}),
        provider_request_id="request-note",
    )

    assert pdf.artifact_ref.startswith("tos://private-test-bucket/private/packets/")
    assert writer.artifacts[0].payload.startswith(b"%PDF-")
    assert writer.artifacts[0].content_type == "application/pdf"
    pdf_text = "".join(
        page.extract_text() or ""
        for page in PdfReader(BytesIO(writer.artifacts[0].payload)).pages
    )
    assert "行测答题卡" in pdf_text
    assert "○ A" in pdf_text
    assert "○ B" in pdf_text
    assert note.artifact_ref.endswith(".md")
    assert "资料分析" in writer.artifacts[1].payload.decode("utf-8")
    assert writer.artifacts[1].content_type == "text/markdown; charset=utf-8"
