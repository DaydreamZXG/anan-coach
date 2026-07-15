from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlsplit

from app.domains.task_content.schemas import (
    ContentAnchor,
    RichContentBlock,
    RichFormulaBlock,
    RichGraphBlock,
    RichImageBlock,
    RichParagraphBlock,
    RichTableBlock,
    RichTableCell,
    SafeAsset,
    SafeMaterial,
    SafeOption,
    SafeQuestion,
    SafeQuestionBundle,
)


PUBLIC_QUESTION_ASSET_HOSTS = frozenset({"kg.xingguoxian.cn"})
PUBLIC_QUESTION_ASSET_PATH_PREFIX = "/assets/"
RICH_TEXT_TAGS = frozenset(
    {
        "b", "br", "em", "i", "li", "ol", "p", "span", "strong",
        "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
        "tr", "u", "ul",
    }
)
RICH_TEXT_BLOCKED_TAGS = frozenset(
    {"iframe", "object", "script", "style", "svg", "template"}
)
FORMULA_PATTERN = re.compile(
    r"\\\[(?P<display>.+?)\\\]|\\\((?P<inline>.+?)\\\)|"
    r"\$\$(?P<dollar_display>.+?)\$\$|(?<!\$)\$(?P<dollar_inline>[^$\n]+?)\$(?!\$)",
    re.DOTALL,
)


class TaskContentContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def freeze_learner_bundle(
    source_questions: list[dict[str, Any]],
    *,
    scoring_policy_version: str,
    content_version_id: Callable[[dict[str, Any]], str],
    published_explanation: Callable[
        [dict[str, Any]], tuple[str | None, str | None]
    ],
) -> tuple[SafeQuestionBundle, dict[str, Any]]:
    safe_questions: list[SafeQuestion] = []
    scoring_questions: list[dict[str, Any]] = []
    materials: dict[str, SafeMaterial] = {}
    for source in source_questions:
        version_id = content_version_id(source)
        material_id: str | None = None
        source_assets = [
            {**asset, "public_url": public_url}
            for asset in source["assets"]
            if (public_url := public_question_asset_url(asset.get("image_src")))
            is not None
        ]
        if source.get("visual_required") and not source_assets:
            raise TaskContentContractError(
                "visual_asset_unavailable",
                "The visual question has no published learner-safe image.",
            )
        material_html = safe_rich_html(source.get("material_html"))
        stem_html = safe_rich_html(source.get("stem_html"))
        if source.get("material_text") or material_html or source_assets:
            material_identity = {
                "text": source.get("material_text"),
                "html": material_html,
                "assets": source_assets,
            }
            material_id = f"material-{_hash(material_identity)[:24]}"
            if material_id not in materials:
                material_content = rich_content(
                    scope=f"material-{material_id}",
                    text=source.get("material_text"),
                    html=material_html,
                    assets=source_assets,
                    visual_required=bool(source.get("visual_required")),
                )
                materials[material_id] = SafeMaterial(
                    materialId=material_id,
                    title=None,
                    text=source.get("material_text"),
                    html=material_html,
                    assets=[safe_asset(source, asset) for asset in source_assets],
                    content=material_content,
                    anchors=content_anchors(material_content),
                )
        options = [
            SafeOption(
                optionId=str(option["label"]).strip().upper(),
                label=str(option["label"]),
                text=str(option["option_text"]),
                html=safe_rich_html(option.get("option_html")),
                content=rich_content(
                    scope=(
                        f"question-{source['question_id']}-option-"
                        f"{str(option['label']).strip().upper()}"
                    ),
                    text=option.get("option_text"),
                    html=safe_rich_html(option.get("option_html")),
                    assets=[],
                    visual_required=False,
                ),
            )
            for option in source["options"]
        ]
        correct_options = answer_options(str(source["answer"]))
        option_ids = {option.option_id for option in options}
        if not correct_options or not set(correct_options).issubset(option_ids):
            raise TaskContentContractError(
                "answer_key_invalid",
                "The answer key does not match the frozen option set.",
            )
        safe_questions.append(
            SafeQuestion(
                questionId=str(source["question_id"]),
                contentVersionId=version_id,
                materialId=material_id,
                moduleName=str(source.get("module_name") or "行测"),
                stem=str(source["stem"]),
                stemHtml=stem_html,
                stemContent=rich_content(
                    scope=f"question-{source['question_id']}-stem",
                    text=source.get("stem"),
                    html=stem_html,
                    assets=(
                        source_assets
                        if not source.get("material_text") and not material_html
                        else []
                    ),
                    visual_required=bool(source.get("visual_required")),
                ),
                options=options,
            )
        )
        explanation, explanation_source = published_explanation(source)
        scoring_questions.append(
            {
                "questionId": str(source["question_id"]),
                "contentVersionId": version_id,
                "correctOptionIds": correct_options,
                "explanation": explanation,
                "explanationSource": explanation_source,
            }
        )
    frozen_payload = {
        "materials": [
            item.model_dump(by_alias=True, mode="json") for item in materials.values()
        ],
        "questions": [
            item.model_dump(by_alias=True, mode="json") for item in safe_questions
        ],
        "scoring": scoring_questions,
        "scoringPolicyVersion": scoring_policy_version,
    }
    bundle_version_id = _hash(frozen_payload)
    bundle = SafeQuestionBundle(
        bundleVersionId=bundle_version_id,
        bundleType="shared_material" if materials else "single",
        materials=list(materials.values()),
        questions=safe_questions,
    )
    return bundle, {
        "bundleVersionId": bundle_version_id,
        "scoringPolicyVersion": scoring_policy_version,
        "questions": scoring_questions,
    }


class _SafeRichHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized = tag.casefold()
        if self.blocked_depth:
            if normalized in RICH_TEXT_BLOCKED_TAGS:
                self.blocked_depth += 1
            return
        if normalized in RICH_TEXT_BLOCKED_TAGS:
            self.blocked_depth = 1
            return
        if normalized not in RICH_TEXT_TAGS:
            return
        safe_attrs: list[str] = []
        if normalized in {"td", "th"}:
            for name, value in attrs:
                if name.casefold() not in {"colspan", "rowspan"} or value is None:
                    continue
                candidate = value.strip()
                if candidate.isdigit() and 1 <= int(candidate) <= 100:
                    safe_attrs.append(f' {name.casefold()}="{candidate}"')
        self.output.append(f"<{normalized}{''.join(safe_attrs)}>")
        if normalized != "br":
            self.open_tags.append(normalized)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        normalized = tag.casefold()
        if normalized in self.open_tags:
            self.handle_endtag(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if self.blocked_depth:
            if normalized in RICH_TEXT_BLOCKED_TAGS:
                self.blocked_depth -= 1
            return
        if normalized not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.output.append(f"</{current}>")
            if current == normalized:
                break

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.output.append(escape(data, quote=False))

    def finish(self) -> str | None:
        while self.open_tags:
            self.output.append(f"</{self.open_tags.pop()}>")
        value = "".join(self.output).strip()
        return value or None


def safe_rich_html(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _SafeRichHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.finish()


class _ParagraphHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._table_depth = 0
        self._capture_depth = 0
        self._buffer: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized == "table":
            self._table_depth += 1
            return
        if self._table_depth:
            return
        if normalized in {"p", "li"}:
            if self._capture_depth == 0:
                self._buffer = []
            self._capture_depth += 1
        elif normalized == "br" and self._capture_depth:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized == "table":
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth or normalized not in {"p", "li"}:
            return
        if self._capture_depth:
            self._capture_depth -= 1
        if self._capture_depth == 0:
            value = " ".join("".join(self._buffer).split())
            if value:
                self.paragraphs.append(value)

    def handle_data(self, data: str) -> None:
        if not self._table_depth and self._capture_depth:
            self._buffer.append(data)


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[RichTableCell]] = []
        self._table_depth = 0
        self._rows: list[RichTableCell] = []
        self._row = -1
        self._occupied: set[tuple[int, int]] = set()
        self._cell: dict[str, Any] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized = tag.casefold()
        if normalized == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
                self._row = -1
                self._occupied = set()
            return
        if self._table_depth != 1:
            return
        if normalized == "tr":
            self._row += 1
            return
        if normalized not in {"td", "th"} or self._row < 0:
            return
        values = {name.casefold(): value for name, value in attrs if value is not None}
        row_span = _safe_span(values.get("rowspan"))
        column_span = _safe_span(values.get("colspan"))
        column = 0
        while (self._row, column) in self._occupied:
            column += 1
        for row in range(self._row, self._row + row_span):
            for col in range(column, column + column_span):
                self._occupied.add((row, col))
        self._cell = {
            "row": self._row,
            "column": column,
            "rowSpan": row_span,
            "columnSpan": column_span,
            "header": normalized == "th",
            "parts": [],
        }

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized == "table":
            if self._table_depth == 1 and self._rows:
                self.tables.append(self._rows)
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1 or normalized not in {"td", "th"}:
            return
        if self._cell is None:
            return
        value = " ".join("".join(self._cell.pop("parts")).split())
        self._rows.append(RichTableCell(**self._cell, text=value, html=None))
        self._cell = None

    def handle_data(self, data: str) -> None:
        if self._table_depth == 1 and self._cell is not None:
            self._cell["parts"].append(data)


def _safe_span(value: str | None) -> int:
    if value is None or not value.isdigit():
        return 1
    return min(max(int(value), 1), 100)


def rich_content(
    *,
    scope: str,
    text: object,
    html: str | None,
    assets: list[dict[str, Any]],
    visual_required: bool,
) -> list[RichContentBlock]:
    blocks: list[RichContentBlock] = []
    paragraphs: list[str] = []
    if html:
        paragraph_parser = _ParagraphHTMLParser()
        paragraph_parser.feed(html)
        paragraph_parser.close()
        paragraphs = paragraph_parser.paragraphs
    if not paragraphs and isinstance(text, str):
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    for paragraph in paragraphs:
        matches = list(FORMULA_PATTERN.finditer(paragraph))
        if not matches:
            blocks.append(
                RichParagraphBlock(
                    blockId=_content_block_id(scope, len(blocks), "paragraph"),
                    text=paragraph,
                    html=None,
                )
            )
            continue
        cursor = 0
        for match in matches:
            prefix = paragraph[cursor : match.start()].strip()
            if prefix:
                blocks.append(
                    RichParagraphBlock(
                        blockId=_content_block_id(scope, len(blocks), "paragraph"),
                        text=prefix,
                        html=None,
                    )
                )
            expression = next(value for value in match.groupdict().values() if value)
            blocks.append(
                RichFormulaBlock(
                    blockId=_content_block_id(scope, len(blocks), "formula"),
                    expression=expression.strip(),
                    display=bool(
                        match.group("display") or match.group("dollar_display")
                    ),
                )
            )
            cursor = match.end()
        suffix = paragraph[cursor:].strip()
        if suffix:
            blocks.append(
                RichParagraphBlock(
                    blockId=_content_block_id(scope, len(blocks), "paragraph"),
                    text=suffix,
                    html=None,
                )
            )
    if html:
        table_parser = _TableHTMLParser()
        table_parser.feed(html)
        table_parser.close()
        for cells in table_parser.tables:
            row_count = max(cell.row + cell.row_span for cell in cells)
            column_count = max(cell.column + cell.column_span for cell in cells)
            header_rows = 0
            for row in range(row_count):
                row_cells = [cell for cell in cells if cell.row == row]
                if row_cells and all(cell.header for cell in row_cells):
                    header_rows += 1
                else:
                    break
            blocks.append(
                RichTableBlock(
                    blockId=_content_block_id(scope, len(blocks), "table"),
                    rowCount=row_count,
                    columnCount=column_count,
                    frozenHeaderRows=header_rows,
                    cells=cells,
                )
            )
    for asset in assets:
        block_id = _content_block_id(scope, len(blocks), "asset")
        url = str(asset["public_url"])
        asset_id = str(asset["asset_id"])
        if is_graph_asset(asset, visual_required):
            blocks.append(
                RichGraphBlock(
                    blockId=block_id,
                    assetId=asset_id,
                    url=url,
                    originalOcrUrl=url,
                    altText="行测图表原图",
                )
            )
        else:
            blocks.append(
                RichImageBlock(
                    blockId=block_id,
                    assetId=asset_id,
                    url=url,
                    originalOcrUrl=url,
                    altText="行测题目原图",
                )
            )
    return blocks


def safe_asset(source: dict[str, Any], asset: dict[str, Any]) -> SafeAsset:
    url = str(asset["public_url"])
    is_graph = is_graph_asset(asset, bool(source.get("visual_required")))
    return SafeAsset(
        assetId=str(asset["asset_id"]),
        url=url,
        altText=(
            f"{source.get('module_name') or '行测'}图表原图"
            if is_graph
            else f"{source.get('module_name') or '行测'}题目原图"
        ),
        originalOcrUrl=url,
        contentKind="graph" if is_graph else "image",
    )


def is_graph_asset(asset: dict[str, Any], visual_required: bool) -> bool:
    part_kind = str(asset.get("part_kind") or "").casefold()
    return visual_required or any(
        marker in part_kind for marker in ("graph", "chart", "figure", "diagram")
    )


def content_anchors(content: list[RichContentBlock]) -> list[ContentAnchor]:
    anchors: list[ContentAnchor] = []
    for index, block in enumerate(content, start=1):
        if block.type == "table":
            label = f"表格 {index}"
        elif block.type in {"image", "graph"}:
            label = f"图片 {index}"
        else:
            label = f"材料 {index}"
        anchors.append(
            ContentAnchor(
                anchorId=f"anchor-{_hash({'blockId': block.block_id})[:24]}",
                blockId=block.block_id,
                label=label,
            )
        )
    return anchors


def answer_options(answer: str) -> list[str]:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", answer.upper()) if token]
    if len(tokens) == 1 and len(tokens[0]) > 1 and tokens[0].isalpha():
        return list(tokens[0])
    return tokens


def public_question_asset_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.hostname.casefold() not in PUBLIC_QUESTION_ASSET_HOSTS
        or parsed.port not in {None, 443}
        or not parsed.path.startswith(PUBLIC_QUESTION_ASSET_PATH_PREFIX)
    ):
        return None
    return candidate


def _content_block_id(scope: str, index: int, kind: str) -> str:
    return f"block-{_hash({'scope': scope, 'index': index, 'kind': kind})[:24]}"


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
