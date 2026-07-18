from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Protocol

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
import tos

from app.core.config import Settings
from app.domains.learning_packages.schemas import PacketRenderRequest, PacketRenderResult


@dataclass(frozen=True)
class RenderedArtifact:
    object_key: str
    content_type: str
    payload: bytes
    sha256_hex: str


class PacketArtifactWriter(Protocol):
    def put_private(self, artifact: RenderedArtifact) -> None: ...


class TosPacketArtifactWriter:
    def __init__(self, settings: Settings) -> None:
        required = (
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
            settings.tos_bucket,
        )
        if not all(value.strip() for value in required):
            raise ValueError("TOS packet renderer configuration is incomplete")
        self._bucket = settings.tos_bucket
        self._client = tos.TosClientV2(
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
        )

    def put_private(self, artifact: RenderedArtifact) -> None:
        try:
            existing = self._client.head_object(self._bucket, artifact.object_key)
        except Exception as exc:
            if _tos_status(exc) != 404:
                raise
        else:
            existing_hash = str(getattr(existing, "meta", {}).get("content-sha256", ""))
            if existing_hash != artifact.sha256_hex:
                raise RuntimeError("packet artifact key already contains different bytes")
            return
        try:
            self._client.put_object(
                self._bucket,
                artifact.object_key,
                content_length=len(artifact.payload),
                content_sha256=artifact.sha256_hex,
                content_type=artifact.content_type,
                acl=tos.ACLType.ACL_Private,
                server_side_encryption="AES256",
                meta={"content-sha256": artifact.sha256_hex},
                content=artifact.payload,
                forbid_overwrite=True,
            )
        except Exception as exc:
            if _tos_status(exc) not in {409, 412}:
                raise
            existing = self._client.head_object(self._bucket, artifact.object_key)
            existing_hash = str(getattr(existing, "meta", {}).get("content-sha256", ""))
            if existing_hash != artifact.sha256_hex:
                raise RuntimeError("concurrent packet artifact write conflicted") from exc


class InternalPacketRenderer:
    def __init__(
        self,
        *,
        settings: Settings,
        writer: PacketArtifactWriter | None = None,
    ) -> None:
        self._bucket = settings.tos_bucket
        self._writer = writer or TosPacketArtifactWriter(settings)

    def render(
        self,
        *,
        request: PacketRenderRequest,
        provider_request_id: str,
    ) -> PacketRenderResult:
        extension, content_type, payload = (
            ("pdf", "application/pdf", _render_pdf(request))
            if request.render_format == "pdf"
            else ("md", "text/markdown; charset=utf-8", _render_markdown(request))
        )
        digest = sha256(payload).hexdigest()
        object_key = (
            f"private/packets/{request.owner_subject_ref}/"
            f"{request.packet_version_id}.{extension}"
        )
        self._writer.put_private(
            RenderedArtifact(
                object_key=object_key,
                content_type=content_type,
                payload=payload,
                sha256_hex=digest,
            )
        )
        return PacketRenderResult(
            artifactRef=f"tos://{self._bucket}/{object_key}",
            artifactSha256=digest,
            providerRequestId=provider_request_id,
        )


def _render_pdf(request: PacketRenderRequest) -> bytes:
    font_name = _register_product_font()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="岸岸学习包",
        author="岸岸",
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AnanTitle",
        parent=base["Title"],
        fontName=font_name,
        fontSize=18,
        leading=26,
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "AnanBody",
        parent=base["BodyText"],
        fontName=font_name,
        fontSize=11,
        leading=18,
        spaceAfter=7,
    )
    meta_style = ParagraphStyle(
        "AnanMeta",
        parent=body_style,
        fontSize=8,
        leading=12,
        textColor="#60706D",
    )
    story = [
        Paragraph("岸岸 · 私人学习包", title_style),
        Paragraph(
            f"版本 {escape(str(request.packet_version_id))} · "
            f"内容指纹 {escape(request.manifest_sha256[:16])}",
            meta_style,
        ),
        Spacer(1, 4 * mm),
    ]
    for index, item in enumerate(request.contents):
        if index:
            story.append(PageBreak())
        story.extend(
            [
                Paragraph(f"{item.order_no}. {escape(item.title)}", title_style),
                Paragraph(escape(item.stem).replace("\n", "<br/>"), body_style),
            ]
        )
        if item.material_text:
            story.append(Paragraph("材料", meta_style))
            story.append(
                Paragraph(escape(item.material_text).replace("\n", "<br/>"), body_style)
            )
        for option in item.options:
            label = escape(str(option.get("label", "")))
            value = escape(str(option.get("text", option.get("optionText", ""))))
            story.append(Paragraph(f"{label}. {value}", body_style))
        story.append(Spacer(1, 8 * mm))
        story.append(
            Paragraph(
                f"答题绑定码：{escape(item.answer_binding_code)}",
                meta_style,
            )
        )
    answer_sheet_items = _objective_answer_sheet_items(request)
    if answer_sheet_items:
        story.extend(
            _answer_sheet_story(
                request=request,
                items=answer_sheet_items,
                font_name=font_name,
                title_style=title_style,
                body_style=body_style,
                meta_style=meta_style,
            )
        )
    document.build(story)
    return buffer.getvalue()


def _objective_answer_sheet_items(
    request: PacketRenderRequest,
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    items: list[tuple[int, tuple[str, ...]]] = []
    for item in request.contents:
        if item.track.value != "xingce":
            continue
        labels = tuple(
            str(option.get("label", "")).strip().upper() for option in item.options
        )
        if (
            len(labels) < 2
            or len(labels) > 8
            or len(labels) != len(set(labels))
            or any(label not in "ABCDEFGH" for label in labels)
        ):
            raise ValueError(
                "xingce packet content requires a complete objective answer layout"
            )
        items.append((item.order_no, labels))
    return tuple(items)


def _answer_sheet_story(
    *,
    request: PacketRenderRequest,
    items: tuple[tuple[int, tuple[str, ...]], ...],
    font_name: str,
    title_style: ParagraphStyle,
    body_style: ParagraphStyle,
    meta_style: ParagraphStyle,
) -> list[object]:
    rows: list[list[Paragraph]] = [
        [
            Paragraph("题号", body_style),
            Paragraph("涂卡区", body_style),
        ]
    ]
    for item_no, labels in items:
        bubbles = "　".join(f"○ {escape(label)}" for label in labels)
        rows.append(
            [
                Paragraph(str(item_no), body_style),
                Paragraph(bubbles, body_style),
            ]
        )
    table = LongTable(
        rows,
        colWidths=(22 * mm, 138 * mm),
        repeatRows=1,
        splitByRow=True,
        hAlign="CENTER",
    )
    table.setStyle(
        TableStyle(
            (
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("BACKGROUND", (0, 0), (-1, 0), "#E6F4F1"),
                ("TEXTCOLOR", (0, 0), (-1, 0), "#143A35"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.45, "#91A5A1"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            )
        )
    )
    return [
        PageBreak(),
        Paragraph("岸岸 · 行测答题卡", title_style),
        Paragraph(
            "姓名：________________　日期：________________　"
            f"题目数：{len(items)}",
            body_style,
        ),
        Paragraph(
            "请使用深色笔完整涂黑所选圆圈；改答案时请擦除干净。拍照上传后，"
            "岸岸会逐题展示识别候选，须由你确认后才写入学习记录。",
            meta_style,
        ),
        Paragraph(
            f"答题卡版本：{escape(str(request.packet_version_id))}　"
            f"内容指纹：{escape(request.manifest_sha256[:16])}",
            meta_style,
        ),
        Spacer(1, 3 * mm),
        table,
    ]


def _register_product_font() -> str:
    font_name = "MapleMonoCN-Regular"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(__file__).with_name("assets") / "MapleMono-CN-Regular.ttf"
        if not font_path.is_file():
            raise RuntimeError("bundled Maple Mono CN PDF font is unavailable")
        if sha256(font_path.read_bytes()).hexdigest() != (
            "0fcc5139bfd5ab14ff989bb7dfd5236d0cafc10e60b9828569e6883613ab4085"
        ):
            raise RuntimeError("bundled Maple Mono CN PDF font digest drifted")
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def _render_markdown(request: PacketRenderRequest) -> bytes:
    lines = [
        "# 岸岸 · 私人学习包",
        "",
        f"- 版本：`{request.packet_version_id}`",
        f"- 内容指纹：`{request.manifest_sha256}`",
        "",
    ]
    for item in request.contents:
        lines.extend([f"## {item.order_no}. {item.title}", "", item.stem, ""])
        if item.material_text:
            lines.extend(["> 材料", ">", *[f"> {line}" for line in item.material_text.splitlines()], ""])
        lines.extend(
            f"- {option.get('label', '')}. {option.get('text', option.get('optionText', ''))}"
            for option in item.options
        )
        lines.extend(["", f"答题绑定码：`{item.answer_binding_code}`", "", "---", ""])
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _tos_status(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
