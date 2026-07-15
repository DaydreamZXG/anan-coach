from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskContentModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class MaterialPosition(TaskContentModel):
    anchor_id: str = Field(alias="anchorId", min_length=1, max_length=240)
    scroll_top: int = Field(alias="scrollTop", ge=0)
    zoom: float = Field(ge=0.5, le=5)


class RichParagraphBlock(TaskContentModel):
    type: Literal["paragraph"] = "paragraph"
    block_id: str = Field(alias="blockId", min_length=1, max_length=240)
    text: str = Field(min_length=1)
    html: str | None = None


class RichFormulaBlock(TaskContentModel):
    type: Literal["formula"] = "formula"
    block_id: str = Field(alias="blockId", min_length=1, max_length=240)
    expression: str = Field(min_length=1, max_length=4_000)
    format: Literal["latex"] = "latex"
    display: bool = False


class RichImageBlock(TaskContentModel):
    type: Literal["image"] = "image"
    block_id: str = Field(alias="blockId", min_length=1, max_length=240)
    asset_id: str = Field(alias="assetId", min_length=1, max_length=240)
    url: str
    original_ocr_url: str = Field(alias="originalOcrUrl")
    alt_text: str = Field(alias="altText", min_length=1, max_length=500)
    zoomable: Literal[True] = True


class RichGraphBlock(TaskContentModel):
    type: Literal["graph"] = "graph"
    block_id: str = Field(alias="blockId", min_length=1, max_length=240)
    asset_id: str = Field(alias="assetId", min_length=1, max_length=240)
    url: str
    original_ocr_url: str = Field(alias="originalOcrUrl")
    alt_text: str = Field(alias="altText", min_length=1, max_length=500)
    zoomable: Literal[True] = True


class RichTableCell(TaskContentModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, alias="rowSpan", ge=1, le=100)
    column_span: int = Field(default=1, alias="columnSpan", ge=1, le=100)
    header: bool = False
    text: str
    html: str | None = None


class RichTableBlock(TaskContentModel):
    type: Literal["table"] = "table"
    block_id: str = Field(alias="blockId", min_length=1, max_length=240)
    row_count: int = Field(alias="rowCount", ge=1, le=1_000)
    column_count: int = Field(alias="columnCount", ge=1, le=100)
    frozen_header_rows: int = Field(alias="frozenHeaderRows", ge=0, le=100)
    horizontally_scrollable: Literal[True] = Field(
        default=True, alias="horizontallyScrollable"
    )
    cells: list[RichTableCell] = Field(min_length=1)


RichContentBlock = Annotated[
    RichParagraphBlock
    | RichFormulaBlock
    | RichImageBlock
    | RichGraphBlock
    | RichTableBlock,
    Field(discriminator="type"),
]


class ContentAnchor(TaskContentModel):
    anchor_id: str = Field(alias="anchorId", min_length=1, max_length=240)
    block_id: str = Field(alias="blockId", min_length=1, max_length=240)
    label: str = Field(min_length=1, max_length=120)


class SafeOption(TaskContentModel):
    option_id: str = Field(alias="optionId")
    label: str
    text: str
    html: str | None = None
    content: list[RichContentBlock]


class SafeAsset(TaskContentModel):
    asset_id: str = Field(alias="assetId")
    url: str
    alt_text: str = Field(alias="altText")
    original_ocr_url: str = Field(alias="originalOcrUrl")
    content_kind: Literal["image", "graph"] = Field(alias="contentKind")


class SafeMaterial(TaskContentModel):
    material_id: str = Field(alias="materialId")
    title: str | None = None
    text: str | None = None
    html: str | None = None
    assets: list[SafeAsset]
    content: list[RichContentBlock]
    anchors: list[ContentAnchor]


class SafeQuestion(TaskContentModel):
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId")
    material_id: str | None = Field(default=None, alias="materialId")
    module_name: str = Field(alias="moduleName")
    stem: str
    stem_html: str | None = Field(default=None, alias="stemHtml")
    stem_content: list[RichContentBlock] = Field(alias="stemContent")
    options: list[SafeOption]


class SafeQuestionBundle(TaskContentModel):
    bundle_version_id: str = Field(
        alias="bundleVersionId", min_length=64, max_length=64
    )
    bundle_type: Literal["single", "shared_material"] = Field(alias="bundleType")
    materials: list[SafeMaterial]
    questions: list[SafeQuestion] = Field(min_length=1)
