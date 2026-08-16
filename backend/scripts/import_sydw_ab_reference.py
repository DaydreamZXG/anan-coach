#!/usr/bin/env python3
"""Import unmatched A/B 类事业单位职测参考题到 staging 题库。

输入是 ``match_reference_papers.py`` 产生的匹配 JSONL。可信命中只补卷面
occurrence；其余记录按题干、选项、答案语义与图片字节指纹去重后建新题。
脚本拒绝写入非 ``*_staging`` 数据库，并且可重放：题目、卷面出现记录、
标签、图片资产和内容版本都使用稳定 ID。

图片会先以 ``upload-assets`` 模式发布到对象存储，再用 ``apply`` 写库。
缺共享材料、缺必需题图或题干本身缺失的题仍会入库，但状态为
``quarantined``，不会进入当前可作答内容头。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import html
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import unicodedata
from urllib.parse import quote
import zipfile

import psycopg
from psycopg.types.json import Jsonb


DATASET_ID = "sydw-ab-zhice-reference-20260811"
OBJECT_PREFIX = f"data/question-bank-export/files/{DATASET_ID}/"
TRUSTED_STATUSES = frozenset({"exact_unique", "exact_disambiguated"})
LABELS = "ABCDEFGH"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[\s\u00a0]+")
_PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_YEAR_RE = re.compile(r"(20\d{2})")
_MISSING_STEM_RE = re.compile(r"(?:题目|题干|选项).{0,8}(?:缺失|不全|搜索中)")
_VISUAL_KNOWLEDGE_RE = re.compile(
    r"图形|空间重构|六面体|截面|拼合|视图|黑白运算|"
    r"对称性|笔画数|一笔画|\b点-|\b线-|\b面-"
)
_VISUAL_STEM_RE = re.compile(
    r"如下图|下图|图中|图形|纸盒|折叠|展开图|阴影|"
    r"柱状图|折线图|饼图|示意图|以下图"
)
_IMAGE_SRC_RE = re.compile(r"(<img\b[^>]*?\bsrc=[\"'])([^\"']+)([\"'][^>]*>)", re.I)


def _normalize(value: object) -> str:
    text = html.unescape(_HTML_TAG_RE.sub("", str(value or "")))
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _SPACE_RE.sub("", text)
    return _PUNCT_RE.sub("", text)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _content_version_id(snapshot: dict[str, object]) -> str:
    fields = (
        "question_id",
        "module_name",
        "question_type",
        "stem",
        "stem_html",
        "answer",
        "material_text",
        "material_html",
        "preferred_analysis",
        "analysis_source",
        "analysis_status",
        "prefer_ai_analysis",
        "analysis_needs_review",
        "options",
        "assets",
    )
    payload = {field: snapshot.get(field) for field in fields}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _open_jsonl(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def load_records(path: Path) -> list[dict[str, object]]:
    with _open_jsonl(path) as source:
        return [json.loads(line) for line in source if line.strip()]


def _safe_image_ref(value: object) -> str:
    candidate = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.casefold() not in IMAGE_SUFFIXES
    ):
        raise ValueError(f"unsafe or unsupported image reference: {value!r}")
    return path.as_posix()


def read_image_payloads(
    archive: Path, records: list[dict[str, object]]
) -> dict[str, bytes]:
    references = {
        _safe_image_ref(value)
        for record in records
        for value in (
            list(record.get("sourceImages") or []) + list(_material_image_refs(record))
        )
    }
    with zipfile.ZipFile(archive) as source:
        names = set(source.namelist())
        missing = sorted(references - names)
        if missing:
            raise ValueError(f"archive is missing {len(missing)} referenced images")
        return {reference: source.read(reference) for reference in sorted(references)}


def _option_rows(record: dict[str, object]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, raw in enumerate(record.get("sourceOptions") or []):
        if isinstance(raw, dict):
            label = str(raw.get("label") or "").strip().upper()
            content = str(raw.get("content") or raw.get("text") or "").strip()
        else:
            label = ""
            content = str(raw or "").strip()
        result.append(
            {
                "label": label or (LABELS[index] if index < len(LABELS) else str(index + 1)),
                "content": content,
            }
        )
    return result


def _answer_content(record: dict[str, object]) -> str:
    answer = str(record.get("sourceAnswer") or "").strip().upper()
    if len(answer) != 1 or answer not in LABELS:
        return _normalize(answer)
    options = _option_rows(record)
    index = ord(answer) - ord("A")
    return _normalize(options[index]["content"]) if index < len(options) else answer


def _year(record: dict[str, object]) -> int | None:
    match = _YEAR_RE.search(str(record.get("sourcePaperName") or ""))
    return int(match.group(1)) if match else None


def native_section(category: str, number: int) -> str:
    if category == "联考A类":
        if number <= 20:
            return "常识判断"
        if number <= 45:
            return "言语理解与表达"
        if number <= 50:
            return "数量关系"
        if number <= 85:
            return "判断推理"
        return "资料分析"
    if category == "联考B类":
        if number <= 20:
            return "常识判断"
        if number <= 40:
            return "言语运用"
        if number <= 45:
            return "数学运算"
        if number <= 55:
            return "资料分析"
        if number <= 90:
            return "判断推理"
        return "综合分析"
    raise ValueError(f"unsupported category {category!r}")


def canonical_module(section: str) -> str:
    return {
        "常识判断": "常识判断",
        "言语理解与表达": "言语理解与表达",
        "言语运用": "言语理解与表达",
        "数量关系": "数量关系",
        "数学运算": "数量关系",
        "资料分析": "资料分析",
        "判断推理": "判断推理",
        "综合分析": "言语理解与表达",
    }[section]


def _material_group(category: str, number: int) -> str | None:
    if category == "联考A类" and 86 <= number <= 100:
        return f"A-{86 + ((number - 86) // 5) * 5}-{90 + ((number - 86) // 5) * 5}"
    if category == "联考B类" and 46 <= number <= 55:
        return "B-46-50" if number <= 50 else "B-51-55"
    if category == "联考B类" and 91 <= number <= 100:
        return "B-91-95" if number <= 95 else "B-96-100"
    return None


def _knowledge_points(record: dict[str, object]) -> tuple[str, ...]:
    raw = record.get("sourceKnowledgePoints")
    if isinstance(raw, list):
        values = [str(value.get("name") if isinstance(value, dict) else value) for value in raw]
    else:
        values = re.split(r"[\n,/]+", str(raw or ""))
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _material_image_refs(record: dict[str, object]) -> tuple[str, ...]:
    material = str(record.get("sourceMaterial") or "")
    return tuple(
        dict.fromkeys(match.group(2).strip() for match in _IMAGE_SRC_RE.finditer(material))
    )


def _needs_visual_without_material(record: dict[str, object]) -> bool:
    joined = "\n".join(_knowledge_points(record))
    stem = str(record.get("sourceStem") or "")
    return bool(_VISUAL_KNOWLEDGE_RE.search(joined) or _VISUAL_STEM_RE.search(stem))


def _needs_visual(record: dict[str, object]) -> bool:
    return bool(_needs_visual_without_material(record) or _material_image_refs(record))


def completeness_issues(record: dict[str, object]) -> tuple[str, ...]:
    issues: list[str] = []
    stem = str(record.get("sourceStem") or "").strip()
    if not stem or _MISSING_STEM_RE.search(stem):
        issues.append("missing_stem")
    if not _option_rows(record):
        issues.append("missing_options")
    if not str(record.get("sourceAnswer") or "").strip():
        issues.append("missing_answer")
    category = str(record.get("category") or "")
    number = int(record.get("sourceNumber") or 0)
    if _material_group(category, number) and not str(record.get("sourceMaterial") or "").strip():
        issues.append("missing_shared_material")
    if _needs_visual(record) and not (
        (record.get("sourceImages") or []) or _material_image_refs(record)
    ):
        issues.append("missing_required_image")
    return tuple(dict.fromkeys(issues))


def question_signature(
    record: dict[str, object], image_payloads: dict[str, bytes]
) -> str:
    option_values = sorted(_normalize(item["content"]) for item in _option_rows(record))
    images = [
        sha256(image_payloads[_safe_image_ref(value)]).hexdigest()
        for value in (record.get("sourceImages") or [])
    ]
    payload: dict[str, object] = {
        "stem": _normalize(record.get("sourceStem")),
        "options": option_values,
        "answerContent": _answer_content(record),
        "images": sorted(images),
    }
    category = str(record.get("category") or "")
    number = int(record.get("sourceNumber") or 0)
    if _material_group(category, number) and not images:
        # 共享材料在这份压缩包中缺失；用解析防止不同材料的通用题干误合并。
        payload["missingMaterialDisambiguator"] = _normalize(
            record.get("sourceExplanation")
        )
    identity_issues = set(completeness_issues(record))
    if _needs_visual_without_material(record) and not (record.get("sourceImages") or []):
        # 保持首次导入时的稳定身份：后续补识别材料内图片不改题 ID。
        identity_issues.add("missing_required_image")
    if identity_issues:
        # 不完整题不做跨卷合并：两条都缺图/缺选项不能证明是同题。
        payload["incompleteOccurrence"] = {
            "paperId": record.get("sourcePaperId"),
            "number": record.get("sourceNumber"),
        }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _source_question_id(record: dict[str, object]) -> str:
    return (
        f"sydw-ab:{record.get('sourcePaperId')}:"
        f"{int(record.get('sourceNumber') or 0)}"
    )


@dataclass(slots=True)
class ImportPlan:
    records: list[dict[str, object]]
    image_payloads: dict[str, bytes]
    target_by_source: dict[str, str]
    new_groups: dict[str, list[dict[str, object]]]
    completeness: Counter[str]

    def summary(self) -> dict[str, object]:
        target_counts = Counter(self.target_by_source.values())
        trusted = sum(
            1 for record in self.records if record.get("matchStatus") in TRUSTED_STATUSES
        )
        active = 0
        quarantined = 0
        for group in self.new_groups.values():
            issues = {issue for record in group for issue in completeness_issues(record)}
            if issues:
                quarantined += 1
            else:
                active += 1
        return {
            "datasetId": DATASET_ID,
            "sourceOccurrences": len(self.records),
            "trustedExistingLinks": trusted,
            "newQuestions": len(self.new_groups),
            "newActiveQuestions": active,
            "newQuarantinedQuestions": quarantined,
            "crossPaperDeduplicatedOccurrences": sum(
                count - 1 for target, count in target_counts.items() if target in self.new_groups
            ),
            "uniqueImages": len(
                {sha256(value).hexdigest() for value in self.image_payloads.values()}
            ),
            "completenessIssues": dict(sorted(self.completeness.items())),
        }


def build_plan(
    records: list[dict[str, object]], image_payloads: dict[str, bytes]
) -> ImportPlan:
    by_signature: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_signature[question_signature(record, image_payloads)].append(record)

    target_by_source: dict[str, str] = {}
    new_groups: dict[str, list[dict[str, object]]] = {}
    completeness: Counter[str] = Counter()
    for signature, group in sorted(by_signature.items()):
        trusted_targets = {
            str(match.get("questionId"))
            for record in group
            if record.get("matchStatus") in TRUSTED_STATUSES
            for match in (record.get("matches") or [])
            if isinstance(match, dict) and match.get("questionId")
        }
        if len(trusted_targets) == 1:
            target = next(iter(trusted_targets))
        else:
            target = _stable_id("pp", DATASET_ID, signature)
            new_groups[target] = group
        for record in group:
            target_by_source[_source_question_id(record)] = target
            completeness.update(completeness_issues(record))
    return ImportPlan(records, image_payloads, target_by_source, new_groups, completeness)


def _image_object(payload: bytes, reference: str) -> tuple[str, str]:
    digest = sha256(payload).hexdigest()
    suffix = PurePosixPath(reference).suffix.casefold()
    key = f"{OBJECT_PREFIX}{digest[:2]}/{digest}{suffix}"
    return key, f"https://img.xingguoxian.cn/{quote(key, safe='/')}"


def upload_assets(plan: ImportPlan) -> dict[str, int]:
    try:
        import tos
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("the official tos package is required") from exc
    required = {
        key: os.environ.get(key, "")
        for key in ("TOS_ACCESS_KEY", "TOS_SECRET_KEY", "TOS_ENDPOINT", "TOS_REGION", "TOS_BUCKET")
    }
    if not all(required.values()):
        raise RuntimeError("TOS configuration is incomplete")
    client = tos.TosClientV2(
        required["TOS_ACCESS_KEY"],
        required["TOS_SECRET_KEY"],
        required["TOS_ENDPOINT"],
        required["TOS_REGION"],
    )
    unique: dict[str, tuple[bytes, str]] = {}
    for reference, payload in plan.image_payloads.items():
        key, _url = _image_object(payload, reference)
        unique.setdefault(key, (payload, reference))
    counts = Counter()
    for key, (payload, reference) in sorted(unique.items()):
        try:
            client.head_object(required["TOS_BUCKET"], key)
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status != 404:
                raise
        else:
            counts["alreadyPresent"] += 1
            continue
        client.put_object(
            required["TOS_BUCKET"],
            key,
            content=payload,
            content_length=len(payload),
            content_type=mimetypes.guess_type(reference)[0] or "application/octet-stream",
            acl=tos.ACLType.ACL_Public_Read,
            forbid_overwrite=True,
            meta={"content-sha256": sha256(payload).hexdigest()},
        )
        client.head_object(required["TOS_BUCKET"], key)
        counts["uploaded"] += 1
    counts["total"] = len(unique)
    return dict(counts)


def _representative(group: list[dict[str, object]]) -> dict[str, object]:
    return max(
        group,
        key=lambda record: (
            not completeness_issues(record),
            len(str(record.get("sourceExplanation") or "")),
            len(str(record.get("sourceStem") or "")),
        ),
    )


def _group_issues(group: list[dict[str, object]]) -> tuple[str, ...]:
    # 同题的任一完整来源可作代表；只有每个出现都缺同一项才隔离。
    issue_sets = [set(completeness_issues(record)) for record in group]
    return tuple(sorted(set.intersection(*issue_sets))) if issue_sets else ()


def _native_material_id(record: dict[str, object]) -> str | None:
    group = _material_group(
        str(record.get("category") or ""), int(record.get("sourceNumber") or 0)
    )
    if group is None:
        return None
    return _stable_id("sydwab_mat", str(record.get("sourcePaperId") or ""), group)


def _question_assets(
    question_id: str,
    record: dict[str, object],
    image_payloads: dict[str, bytes],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    references = [
        ("material_html", value) for value in _material_image_refs(record)
    ] + [("stem_html", value) for value in (record.get("sourceImages") or [])]
    for ordinal, (part_kind, raw_reference) in enumerate(references):
        reference = _safe_image_ref(raw_reference)
        payload = image_payloads[reference]
        key, url = _image_object(payload, reference)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "asset_id": _stable_id("asset", question_id, key),
                "question_id": question_id,
                "source_question_id": _source_question_id(record),
                "is_representative_asset": True,
                "part_kind": part_kind,
                "part_index": 0,
                "image_ordinal": ordinal,
                "image_src": url,
                "object_key": key,
                "source_html": reference,
            }
        )
    return rows


def _render_material(
    record: dict[str, object], image_payloads: dict[str, bytes]
) -> tuple[str | None, str | None]:
    raw = str(record.get("sourceMaterial") or "").strip()
    if not raw:
        return None, None

    def replace_image(match: re.Match[str]) -> str:
        reference = _safe_image_ref(match.group(2))
        payload = image_payloads[reference]
        _key, url = _image_object(payload, reference)
        return f"{match.group(1)}{url}{match.group(3)}"

    rendered_html = _IMAGE_SRC_RE.sub(replace_image, raw)
    plain = re.sub(r"</(?:p|div|li|tr|h[1-6])>", "\n", raw, flags=re.I)
    plain = re.sub(r"</(?:td|th)>", "\t", plain, flags=re.I)
    plain = html.unescape(_HTML_TAG_RE.sub("", plain))
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    return plain or None, rendered_html


def _question_tags(
    question_id: str, group: list[dict[str, object]], issues: tuple[str, ...]
) -> list[dict[str, object]]:
    values: list[tuple[str, str]] = [
        ("exam_system", "事业单位联考"),
        ("rights_status", "user_private"),
    ]
    for record in group:
        category = str(record.get("category") or "")
        section = native_section(category, int(record.get("sourceNumber") or 0))
        values.extend(
            (
                ("exam_category", category.removeprefix("联考")),
                ("native_section", section),
                ("module", canonical_module(section)),
            )
        )
        values.extend(("knowledge_point", value) for value in _knowledge_points(record))
    values.extend(("import_completeness", issue) for issue in issues)
    unique = list(dict.fromkeys(values))
    return [
        {
            "question_tag_id": _stable_id("sydwab_tag", question_id, tag_type, value),
            "question_id": question_id,
            "source_tag_id": None,
            "tag_type": tag_type,
            "tag_value": value,
            "sort_order": index,
            "evidence_link_count": len(group),
            "source_question_count": len(group),
            "evidence_sources": DATASET_ID,
        }
        for index, (tag_type, value) in enumerate(unique)
    ]


def _snapshot(
    question_id: str,
    record: dict[str, object],
    module: str,
    assets: list[dict[str, object]],
    material_text: str | None,
    material_html: str | None,
) -> dict[str, object]:
    explanation = str(record.get("sourceExplanation") or "").strip() or None
    return {
        "question_id": question_id,
        "module_name": module,
        "question_type": str(record.get("sourceQuestionType") or "单选题"),
        "stem": str(record.get("sourceStem") or "").strip(),
        "stem_html": str(record.get("sourceStem") or "").strip(),
        "answer": str(record.get("sourceAnswer") or "").strip(),
        "material_text": material_text,
        "material_html": material_html,
        "preferred_analysis": explanation,
        "analysis_source": "imported_sydw_ab_reference" if explanation else None,
        "analysis_status": "imported" if explanation else None,
        "prefer_ai_analysis": False,
        "analysis_needs_review": True if explanation else None,
        "options": [
            {
                "label": option["label"],
                "option_text": option["content"],
                "option_html": option["content"],
            }
            for option in _option_rows(record)
        ],
        "assets": [
            {
                key: asset[key]
                for key in (
                    "asset_id",
                    "part_kind",
                    "part_index",
                    "image_ordinal",
                    "image_src",
                    "object_key",
                    "is_representative_asset",
                )
            }
            for asset in assets
        ],
    }


def _database_name(connection: psycopg.Connection) -> str:
    return str(connection.execute("select current_database()").fetchone()[0])


def apply_plan(plan: ImportPlan, database_url: str) -> dict[str, int]:
    counts = Counter()
    with psycopg.connect(database_url) as connection:
        database = _database_name(connection)
        if not database.endswith("_staging"):
            raise RuntimeError(f"refusing to import into non-staging database {database!r}")
        with connection.cursor() as cursor:
            now = datetime.now(UTC)
            preexisting_question_ids = {
                str(row[0])
                for row in cursor.execute(
                    """
                    select question_id
                    from product.questions
                    where merge_evidence_source = %s
                    """,
                    (DATASET_ID,),
                ).fetchall()
            }
            for question_id, group in sorted(plan.new_groups.items()):
                representative = _representative(group)
                issues = _group_issues(group)
                status = "quarantined" if issues else "active"
                category = str(representative.get("category") or "")
                section = native_section(
                    category, int(representative.get("sourceNumber") or 0)
                )
                module = canonical_module(section)
                options = _option_rows(representative)
                assets = _question_assets(
                    question_id, representative, plan.image_payloads
                )
                material_text, material_html = _render_material(
                    representative, plan.image_payloads
                )
                source_ids = sorted(_source_question_id(record) for record in group)
                years = [value for record in group if (value := _year(record)) is not None]
                ratio = representative.get("sourceCorrectRate")
                wrong = str(representative.get("sourceWrongOption") or "").strip() or None
                classification = {
                    "examSystem": "事业单位联考",
                    "examCategories": sorted(
                        {
                            str(record.get("category") or "").removeprefix("联考")
                            for record in group
                        }
                    ),
                    "nativeSections": sorted(
                        {
                            native_section(
                                str(record.get("category") or ""),
                                int(record.get("sourceNumber") or 0),
                            )
                            for record in group
                        }
                    ),
                    "rightsStatus": "user_private",
                    "completenessIssues": list(issues),
                }
                cursor.execute(
                    """
                    insert into product.questions (
                      question_id, domain, status, merge_policy, merge_evidence_source,
                      representative_source_question_id, module_name, question_type,
                      display_level1, display_level2, display_level3,
                      stem, stem_html, option_texts, option_htmls, answer,
                      shared_context_required, visual_required, needs_multimodal,
                      material_id, material_text, material_html,
                      representative_correct_ratio, representative_easy_wrong,
                      representative_most_wrong_answer,
                      source_member_count, merged_source_member_count,
                      occurrence_count, occurrence_province_count,
                      occurrence_paper_count, first_year, latest_year,
                      occurrence_provinces, source_question_ids,
                      source_question_keys, representative_classification,
                      representative_asset_refs, imported_at
                    ) values (
                      %s, 'objective', %s, 'source_exact', %s,
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s, 1,
                      %s, %s, %s,
                      '全国联考', %s, %s, %s, %s, %s
                    ) on conflict (question_id) do update
                    set status = excluded.status,
                        module_name = excluded.module_name,
                        question_type = excluded.question_type,
                        display_level1 = excluded.display_level1,
                        display_level2 = excluded.display_level2,
                        display_level3 = excluded.display_level3,
                        stem = excluded.stem,
                        stem_html = excluded.stem_html,
                        option_texts = excluded.option_texts,
                        option_htmls = excluded.option_htmls,
                        answer = excluded.answer,
                        shared_context_required = excluded.shared_context_required,
                        visual_required = excluded.visual_required,
                        needs_multimodal = excluded.needs_multimodal,
                        material_id = excluded.material_id,
                        material_text = excluded.material_text,
                        material_html = excluded.material_html,
                        representative_asset_refs = excluded.representative_asset_refs,
                        representative_classification = excluded.representative_classification
                    where product.questions.merge_evidence_source = excluded.merge_evidence_source
                    """,
                    (
                        question_id,
                        status,
                        DATASET_ID,
                        source_ids[0],
                        module,
                        str(representative.get("sourceQuestionType") or "单选题"),
                        "事业单位联考",
                        category.removeprefix("联考"),
                        section,
                        str(representative.get("sourceStem") or "").strip(),
                        str(representative.get("sourceStem") or "").strip(),
                        [option["content"] for option in options],
                        Jsonb([option["content"] for option in options]),
                        str(representative.get("sourceAnswer") or "").strip(),
                        _native_material_id(representative) is not None,
                        _needs_visual(representative),
                        _needs_visual(representative),
                        _native_material_id(representative),
                        material_text,
                        material_html,
                        float(ratio) if isinstance(ratio, (int, float)) else None,
                        wrong,
                        Jsonb({"answer": wrong}) if wrong else None,
                        len(group),
                        len(group),
                        len(group),
                        len({str(record.get("sourcePaperId") or "") for record in group}),
                        min(years) if years else None,
                        max(years) if years else None,
                        ",".join(source_ids),
                        ",".join(source_ids),
                        Jsonb(classification),
                        Jsonb(
                            [
                                {"assetId": asset["asset_id"], "objectKey": asset["object_key"]}
                                for asset in assets
                            ]
                        ),
                        now,
                    ),
                )
                if question_id in preexisting_question_ids:
                    counts["questionsAlreadyPresent"] += 1
                else:
                    counts["questionsInserted"] += 1
                for index, option in enumerate(options, start=1):
                    cursor.execute(
                        """
                        insert into product.question_options (
                          question_id, option_index, label, option_text, option_html
                        ) values (%s, %s, %s, %s, %s)
                        on conflict (question_id, option_index) do update
                        set label = excluded.label,
                            option_text = excluded.option_text,
                            option_html = excluded.option_html
                        """,
                        (
                            question_id,
                            index,
                            option["label"],
                            option["content"],
                            option["content"],
                        ),
                    )
                explanation = str(representative.get("sourceExplanation") or "").strip()
                if explanation:
                    cursor.execute(
                        """
                        insert into product.question_explanations (
                          question_id, preferred_analysis, original_analysis,
                          stored_analysis, analysis_source, analysis_status,
                          analysis_needs_review, prefer_ai_analysis,
                          preferred_analysis_length, imported_at
                        ) values (%s, %s, %s, %s, %s, 'imported', true, false, %s, %s)
                        on conflict (question_id) do nothing
                        """,
                        (
                            question_id,
                            explanation,
                            explanation,
                            explanation,
                            "imported_sydw_ab_reference",
                            len(explanation),
                            now,
                        ),
                    )
                for asset in assets:
                    cursor.execute(
                        """
                        insert into product.question_assets (
                          asset_id, question_id, source_question_id,
                          is_representative_asset, part_kind, part_index,
                          image_ordinal, image_src, object_key, source_html
                        ) values (
                          %(asset_id)s, %(question_id)s, %(source_question_id)s,
                          %(is_representative_asset)s, %(part_kind)s, %(part_index)s,
                          %(image_ordinal)s, %(image_src)s, %(object_key)s, %(source_html)s
                        ) on conflict (asset_id) do nothing
                        """,
                        asset,
                    )
                for tag in _question_tags(question_id, group, issues):
                    cursor.execute(
                        """
                        insert into product.question_tags (
                          question_tag_id, question_id, source_tag_id, tag_type,
                          tag_value, sort_order, evidence_link_count,
                          source_question_count, evidence_sources
                        ) values (
                          %(question_tag_id)s, %(question_id)s, %(source_tag_id)s,
                          %(tag_type)s, %(tag_value)s, %(sort_order)s,
                          %(evidence_link_count)s, %(source_question_count)s,
                          %(evidence_sources)s
                        ) on conflict (question_tag_id) do nothing
                        """,
                        tag,
                    )
                if status == "active":
                    snapshot = _snapshot(
                        question_id,
                        representative,
                        module,
                        assets,
                        material_text,
                        material_html,
                    )
                    content_version_id = _content_version_id(snapshot)
                    search_text = _normalize(
                        "\n".join(
                            [
                                str(snapshot["stem"] or ""),
                                *[option["content"] for option in options],
                                str(snapshot["material_text"] or ""),
                            ]
                        )
                    )
                    cursor.execute(
                        """
                        insert into product.question_content_versions (
                          content_version_id, question_id, track, snapshot_payload,
                          normalized_search_text, created_at
                        ) values (%s, %s, 'xingce', %s, %s, %s)
                        on conflict (content_version_id) do nothing
                        """,
                        (
                            content_version_id,
                            question_id,
                            Jsonb(snapshot),
                            search_text,
                            now,
                        ),
                    )
                    cursor.execute(
                        """
                        insert into product.question_content_heads (
                          question_id, content_version_id, updated_at
                        ) values (%s, %s, %s)
                        on conflict (question_id) do update
                        set content_version_id = excluded.content_version_id,
                            updated_at = excluded.updated_at
                        where product.question_content_heads.content_version_id
                              is distinct from excluded.content_version_id
                        """,
                        (question_id, content_version_id, now),
                    )
                    counts["activeContentHeadsEnsured"] += 1

            for record in plan.records:
                source_id = _source_question_id(record)
                question_id = plan.target_by_source[source_id]
                paper_id = str(record.get("sourcePaperId") or "")
                category = str(record.get("category") or "")
                number = int(record.get("sourceNumber") or 0)
                section = native_section(category, number)
                ratio = record.get("sourceCorrectRate")
                wrong = str(record.get("sourceWrongOption") or "").strip() or None
                cursor.execute(
                    """
                    insert into product.question_occurrences (
                      occurrence_id, question_id, source_question_id,
                      source_dataset_id, source_paper_row_id, source_paper_key,
                      source_occurrence_question_key, source_occurrence_paper_key,
                      source_path, cls, province, occurrence_paper_title,
                      paper_title, year, number, section, occurrence_answer,
                      occurrence_correct_ratio, occurrence_easy_wrong,
                      occurrence_most_wrong_answer
                    ) values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      '全国联考', %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) on conflict (occurrence_id) do nothing
                    """,
                    (
                        _stable_id("occ", DATASET_ID, paper_id, str(number)),
                        question_id,
                        source_id,
                        DATASET_ID,
                        paper_id,
                        paper_id,
                        source_id,
                        paper_id,
                        str(record.get("sourcePaperFile") or ""),
                        category,
                        str(record.get("sourcePaperName") or ""),
                        str(record.get("sourcePaperName") or ""),
                        _year(record),
                        number,
                        section,
                        str(record.get("sourceAnswer") or "").strip(),
                        float(ratio) if isinstance(ratio, (int, float)) else None,
                        wrong,
                        Jsonb({"answer": wrong}) if wrong else None,
                    ),
                )
                counts[
                    "occurrencesInserted" if cursor.rowcount else "occurrencesAlreadyPresent"
                ] += 1

            # 为命中现有题补考试域标签，不改题干、选项或答案。
            existing_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
            for record in plan.records:
                source_id = _source_question_id(record)
                target = plan.target_by_source[source_id]
                if target not in plan.new_groups:
                    existing_groups[target].append(record)
            for question_id, group in existing_groups.items():
                for tag in _question_tags(question_id, group, ()):
                    cursor.execute(
                        """
                        insert into product.question_tags (
                          question_tag_id, question_id, source_tag_id, tag_type,
                          tag_value, sort_order, evidence_link_count,
                          source_question_count, evidence_sources
                        ) values (
                          %(question_tag_id)s, %(question_id)s, %(source_tag_id)s,
                          %(tag_type)s, %(tag_value)s, %(sort_order)s,
                          %(evidence_link_count)s, %(source_question_count)s,
                          %(evidence_sources)s
                        ) on conflict (question_tag_id) do nothing
                        """,
                        tag,
                    )
        connection.commit()
    return dict(counts)


def verify_import(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        database = _database_name(connection)
        if not database.endswith("_staging"):
            raise RuntimeError(f"refusing to verify non-staging database {database!r}")
        row = connection.execute(
            """
            select
              count(*) as occurrences,
              count(distinct question_id) as linked_questions,
              count(distinct question_id) filter (
                where question_id in (
                  select question_id from product.questions
                  where merge_evidence_source = %s
                )
              ) as imported_questions
            from product.question_occurrences
            where source_dataset_id = %s
            """,
            (DATASET_ID, DATASET_ID),
        ).fetchone()
        states = dict(
            connection.execute(
                """
                select status, count(*)
                from product.questions
                where merge_evidence_source = %s
                group by status
                """,
                (DATASET_ID,),
            ).fetchall()
        )
        assets = connection.execute(
            """
            select count(*)
            from product.question_assets asset
            join product.questions question using (question_id)
            where question.merge_evidence_source = %s
            """,
            (DATASET_ID,),
        ).fetchone()[0]
        heads = connection.execute(
            """
            select count(*)
            from product.question_content_heads head
            join product.questions question using (question_id)
            where question.merge_evidence_source = %s
            """,
            (DATASET_ID,),
        ).fetchone()[0]
    return {
        "occurrences": int(row[0]),
        "linkedQuestions": int(row[1]),
        "importedQuestions": int(row[2]),
        "activeQuestions": int(states.get("active", 0)),
        "quarantinedQuestions": int(states.get("quarantined", 0)),
        "assets": int(assets),
        "contentHeads": int(heads),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "upload-assets", "apply", "verify"))
    parser.add_argument("--matches", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--database-url",
        default="postgresql:///gongkao_dazi_staging?host=/var/run/postgresql",
    )
    parser.add_argument("--report", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.mode == "verify":
        result: dict[str, object] = verify_import(args.database_url)
    else:
        if args.matches is None or args.archive is None:
            raise SystemExit("--matches and --archive are required")
        records = load_records(args.matches)
        image_payloads = read_image_payloads(args.archive, records)
        plan = build_plan(records, image_payloads)
        result = plan.summary()
        if args.mode == "upload-assets":
            result["assetPublication"] = upload_assets(plan)
        elif args.mode == "apply":
            result["databaseApply"] = apply_plan(plan, args.database_url)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
