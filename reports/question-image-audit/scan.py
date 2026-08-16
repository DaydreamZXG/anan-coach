#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE = (
    REPO
    / "data/question-bank-export/files/source-question-bank/"
    "fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
)
ASSET_ROOT = (
    REPO
    / "data/question-bank-export/files/source-question-bank/"
    "fenbi-final-analysis-v1-20260613-assets"
)

HTML_FIELDS = {
    "material_html": "material",
    "stem_html": "stem",
    "original_analysis_html": "analysis",
    "original_solution_html": "solution",
}
QUESTION_ROLES = {"material", "stem", "option"}


class ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            self.images.append({key.lower(): value or "" for key, value in attrs})


def images(fragment: Any) -> list[dict[str, str]]:
    if not isinstance(fragment, str) or "<img" not in fragment.lower():
        return []
    parser = ImageParser()
    try:
        parser.feed(fragment)
    except Exception:
        return []
    return parser.images


def canonical_track(question: dict[str, Any]) -> str:
    section = str(question.get("section") or "")
    taxonomy = question.get("primary_taxonomy") or {}
    path = taxonomy.get("catalog_path") or []
    root = str(path[0]) if path else ""
    if question.get("is_graphic_reasoning") or section == "图形推理" or "图形推理" in path:
        return "graphic_reasoning"
    if root == "资料分析" or section in {"资料分析", "资科分析", "资料分斩"}:
        return "data_analysis"
    if root == "数量关系" or section in {
        "数量关系",
        "数理能力",
        "数学运算",
        "数字推理",
        "数量关系与资料分析",
        "数量关系（数字推理）",
        "数量关系（数学运算）",
    }:
        return "quantity"
    if root == "言语理解与表达" or any(token in section for token in ("言语", "选词", "填空", "阅读", "段落")):
        return "verbal"
    if root == "判断推理" or section in {
        "判断推理",
        "判断推理能力",
        "逻辑推理",
        "逻辑判断",
        "推理判断",
        "演绎推理",
        "定义判断",
        "类比推理",
        "思维能力测验",
    }:
        return "judgment_non_graphic"
    if root in {"科学素养", "政治理论"} or any(
        token in section
        for token in ("常识", "科学", "政治", "公共基础", "综合知识", "综合分析", "法律")
    ):
        return "knowledge_science"
    if "知觉" in section:
        return "perceptual_speed"
    return "other"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def option_images(question: dict[str, Any]) -> Iterable[tuple[str, dict[str, str]]]:
    for index, option in enumerate(question.get("options") or []):
        if not isinstance(option, dict):
            continue
        for item in images(option.get("html")):
            yield f"options[{index}].html", item
        for raw in option.get("images") or []:
            if isinstance(raw, str):
                yield f"options[{index}].images", {"src": raw}
            elif isinstance(raw, dict):
                source = raw.get("src") or raw.get("url") or raw.get("path")
                if source:
                    yield f"options[{index}].images", {"src": str(source)}


def main() -> None:
    asset_hash_by_path: dict[str, str] = {}
    assets: dict[str, dict[str, Any]] = {}
    track_questions = Counter()
    track_questions_any = Counter()
    track_questions_question = Counter()
    track_questions_explanation = Counter()
    ref_counts: Counter[tuple[str, str, str]] = Counter()
    track_assets: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    unresolved = Counter()
    total_questions = 0

    with SOURCE.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            question = json.loads(line)
            total_questions += 1
            track = canonical_track(question)
            track_questions[track] += 1
            source_path = Path(str(question.get("source_path") or ""))
            source_dir = ASSET_ROOT / source_path.parent
            question_id = str(question.get("id") or "")
            seen_roles: set[str] = set()
            occurrences: list[tuple[str, str, dict[str, str]]] = []
            for field, role in HTML_FIELDS.items():
                occurrences.extend((field, role, item) for item in images(question.get(field)))
            occurrences.extend((field, "option", item) for field, item in option_images(question))

            for field, role, item in occurrences:
                reference = html.unescape(item.get("src", "")).strip()
                if not reference:
                    continue
                is_formula = item.get("flag", "").lower() == "tex"
                kind = "formula_tex" if is_formula else "image"
                local_path = (source_dir / reference).resolve()
                if local_path.is_file():
                    key = str(local_path)
                    digest = asset_hash_by_path.get(key)
                    if digest is None:
                        digest = sha256(local_path)
                        asset_hash_by_path[key] = digest
                    asset_id = f"qi_{digest[:24]}"
                else:
                    digest = hashlib.sha256(
                        f"missing\0{source_path.parent}\0{reference}".encode()
                    ).hexdigest()
                    asset_id = f"qi_{digest[:24]}"
                    kind = "missing"
                    unresolved[(track, role)] += 1
                record = assets.setdefault(
                    asset_id,
                    {
                        "asset_id": asset_id,
                        "sha256": digest,
                        "kind": kind,
                        "local_path": str(local_path),
                        "size_bytes": local_path.stat().st_size if local_path.is_file() else None,
                        "occurrences": [],
                    },
                )
                occurrence = {
                    "question_id": question_id,
                    "track": track,
                    "section": str(question.get("section") or ""),
                    "role": role,
                    "field": field,
                    "source_jsonl_line": line_number,
                    "image_ref": reference,
                    "knowledge_point": str(question.get("raw_knowledge_point") or "")[:240],
                }
                if len(record["occurrences"]) < 40 and occurrence not in record["occurrences"]:
                    record["occurrences"].append(occurrence)
                ref_counts[(track, role, kind)] += 1
                track_assets[track]["any"].add(asset_id)
                track_assets[track][role].add(asset_id)
                if role in QUESTION_ROLES:
                    track_assets[track]["question"].add(asset_id)
                else:
                    track_assets[track]["explanation"].add(asset_id)
                seen_roles.add(role)

            if seen_roles:
                track_questions_any[track] += 1
            if seen_roles & QUESTION_ROLES:
                track_questions_question[track] += 1
            if seen_roles - QUESTION_ROLES:
                track_questions_explanation[track] += 1

            if total_questions % 20000 == 0:
                print(f"scanned={total_questions} unique_assets={len(assets)}", flush=True)

    rows = sorted(assets.values(), key=lambda row: row["asset_id"])
    (ROOT / "assets.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    tracks = []
    for track, count in track_questions.most_common():
        roles = {
            role: {
                "references": sum(
                    value for (candidate, candidate_role, _), value in ref_counts.items()
                    if candidate == track and candidate_role == role
                ),
                "unique_assets": len(track_assets[track][role]),
            }
            for role in ("material", "stem", "option", "analysis", "solution")
        }
        tracks.append(
            {
                "track": track,
                "questions": count,
                "questions_with_any_image": track_questions_any[track],
                "questions_with_question_image": track_questions_question[track],
                "questions_with_explanation_image": track_questions_explanation[track],
                "unique_assets": len(track_assets[track]["any"]),
                "unique_question_assets": len(track_assets[track]["question"]),
                "unique_explanation_assets": len(track_assets[track]["explanation"]),
                "roles": roles,
                "missing_references": sum(
                    value for (candidate, _), value in unresolved.items() if candidate == track
                ),
            }
        )
    summary = {
        "schema_version": "question-image-inventory-v1",
        "source": str(SOURCE),
        "asset_root": str(ASSET_ROOT),
        "total_questions": total_questions,
        "unique_assets": len(rows),
        "resolved_assets": sum(row["kind"] != "missing" for row in rows),
        "missing_assets": sum(row["kind"] == "missing" for row in rows),
        "tracks": tracks,
    }
    (ROOT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
