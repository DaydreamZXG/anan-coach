#!/usr/bin/env python3
"""CPU-only rescan and coverage audit for the graphic-reasoning batch.

This script never imports or calls a model client. It independently rereads the
real question-bank JSONL, hashes referenced files, and compares the result with
the existing inventory and file-side processing evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
ASSET_ROOT = REPO / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613-assets"
AUDIT_ROOT = ROOT / "cpu-audit"


class ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            self.images.append({key.lower(): value or "" for key, value in attrs})


def parse_images(fragment: Any) -> list[dict[str, str]]:
    if not isinstance(fragment, str) or "<img" not in fragment.lower():
        return []
    parser = ImageParser()
    try:
        parser.feed(fragment)
    except Exception:
        return []
    return parser.images


def option_images(question: dict[str, Any]) -> Iterator[tuple[str, dict[str, str]]]:
    for index, option in enumerate(question.get("options") or []):
        if not isinstance(option, dict):
            continue
        for item in parse_images(option.get("html")):
            yield f"options[{index}].html", item
        for raw in option.get("images") or []:
            if isinstance(raw, str):
                yield f"options[{index}].images", {"src": raw}
            elif isinstance(raw, dict):
                source = raw.get("src") or raw.get("url") or raw.get("path") or raw.get("local")
                if source:
                    yield f"options[{index}].images", {"src": str(source), "fromImageArray": "true"}


def is_graphic_reasoning(question: dict[str, Any]) -> bool:
    section = str(question.get("section") or "")
    taxonomy = question.get("primary_taxonomy") or {}
    path = taxonomy.get("catalog_path") or []
    return bool(question.get("is_graphic_reasoning") or section == "图形推理" or "图形推理" in path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


RULE_TERMS = {
    "translation": ("平移", "移动"),
    "rotation": ("旋转",),
    "reflection": ("翻转", "镜像"),
    "overlay_boolean": ("叠加", "加减同异", "求同", "求异", "去同存异"),
    "count_change": ("数量规律", "数量类", "元素个数", "点数量", "线数量", "面数量"),
    "add_remove": ("增减", "增加", "减少", "加减"),
    "symmetry": ("对称",),
    "folding_spatial": ("折叠", "展开图", "三视图", "空间", "六面体"),
    "direction": ("方向",),
    "connection_change": ("连接", "一笔画", "笔画"),
}


def rescan_source() -> tuple[dict[str, Any], dict[str, dict[str, Any]], Counter[str], list[dict[str, Any]]]:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    records: dict[str, dict[str, Any]] = {}
    path_hash_cache: dict[str, str] = {}
    total_questions = 0
    graphic_questions = 0
    questions_with_image: set[str] = set()
    references_by_role: Counter[str] = Counter({role: 0 for role in ("material", "stem", "option", "analysis", "solution")})
    references_by_field: Counter[str] = Counter()
    knowledge_points: Counter[str] = Counter()
    rule_evidence: Counter[str] = Counter()
    answer_labels: Counter[str] = Counter()
    question_asset_sets: defaultdict[str, set[str]] = defaultdict(set)
    graphic_question_rows: dict[str, dict[str, Any]] = {}
    missing_references = 0
    formula_reference_occurrences_excluded = 0
    recovered_glyph_tex_occurrences = 0
    physical_paths: set[str] = set()
    html_fields = {
        "material_html": "material",
        "stem_html": "stem",
        "original_analysis_html": "analysis",
        "original_solution_html": "solution",
    }
    for line_number, raw in enumerate(SOURCE.open(encoding="utf-8"), 1):
        question = json.loads(raw)
        total_questions += 1
        if not is_graphic_reasoning(question):
            continue
        graphic_questions += 1
        question_id = str(question.get("id") or "")
        graphic_question_rows[question_id] = {
            "questionId": question_id,
            "sourceJsonlLine": line_number,
            "source": str(question.get("source") or ""),
            "sourcePath": str(question.get("source_path") or ""),
            "section": str(question.get("section") or ""),
            "knowledgePoint": str(question.get("raw_knowledge_point") or ""),
            "answerLabel": str(question.get("answer") or ""),
            "stemText": str(question.get("stem_text") or "")[:500],
            "optionsText": [
                {"label": str(option.get("label") or ""), "text": str(option.get("text") or option.get("html") or "")[:300]}
                for option in (question.get("options") or []) if isinstance(option, dict)
            ],
            "graphicReasoningFieldPresent": question.get("graphic_reasoning") is not None,
        }
        answer = str(question.get("answer") or "")
        if answer:
            answer_labels[answer] += 1
        knowledge = str(question.get("raw_knowledge_point") or "")[:240]
        knowledge_points[knowledge] += 1
        for rule, terms in RULE_TERMS.items():
            if any(term in knowledge for term in terms):
                rule_evidence[rule] += 1
        source_path = Path(str(question.get("source_path") or ""))
        source_dir = ASSET_ROOT / source_path.parent
        graphic_reasons = set((question.get("graphic_reasoning") or {}).get("reasons") or [])
        recover_character_tex = "jiangsu_character_reasoning" in graphic_reasons
        occurrences: list[tuple[str, str, dict[str, str]]] = []
        for field, role in html_fields.items():
            occurrences.extend((field, role, item) for item in parse_images(question.get(field)))
        occurrences.extend((field, "option", item) for field, item in option_images(question))
        seen_occurrences: set[tuple[str, str]] = set()
        for field, role, item in occurrences:
            reference = html.unescape(item.get("src", "")).strip()
            is_tex = item.get("flag", "").lower() == "tex"
            if not reference:
                continue
            if is_tex and not (recover_character_tex and role in {"stem", "option"}):
                formula_reference_occurrences_excluded += 1
                continue
            occurrence_key = (role, reference)
            if item.get("fromImageArray") == "true" and occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(occurrence_key)
            if is_tex and recover_character_tex:
                recovered_glyph_tex_occurrences += 1
            references_by_role[role] += 1
            references_by_field[field] += 1
            local_path = (source_dir / reference).resolve()
            path_key = str(local_path)
            physical_paths.add(path_key)
            if local_path.is_file():
                digest = path_hash_cache.get(path_key)
                if digest is None:
                    digest = sha256_file(local_path)
                    path_hash_cache[path_key] = digest
                kind = "image"
                asset_id = f"gr_{digest[:24]}"
            else:
                missing_references += 1
                digest = hashlib.sha256(f"missing\0{source_path.parent}\0{reference}".encode()).hexdigest()
                kind = "missing"
                asset_id = f"gr_missing_{digest[:24]}"
            question_asset_sets[question_id].add(asset_id)
            questions_with_image.add(question_id)
            record = records.setdefault(asset_id, {
                "assetId": asset_id,
                "sha256": digest,
                "kind": kind,
                "localPaths": set(),
                "occurrences": [],
            })
            record["localPaths"].add(path_key)
            record["occurrences"].append({
                "questionId": question_id,
                "role": role,
                "field": field,
                "sourceJsonlLine": line_number,
                "imageRef": reference,
                "knowledgePoint": knowledge,
                "section": str(question.get("section") or ""),
                "track": "graphic_reasoning",
            })
    duplicate_assets = sum(len(record["localPaths"]) > 1 for record in records.values())
    duplicate_physical_paths = sum(max(0, len(record["localPaths"]) - 1) for record in records.values())
    summary = {
        "schemaVersion": "graphic-reasoning-cpu-rescan-v1",
        "mode": "cpu_only_no_model_or_endpoint_access",
        "source": str(SOURCE),
        "assetRoot": str(ASSET_ROOT),
        "totalQuestionsScanned": total_questions,
        "graphicReasoningQuestions": graphic_questions,
        "questionsWithGraphicImage": len(questions_with_image),
        "questionsWithoutGraphicImage": graphic_questions - len(questions_with_image),
        "uniqueContentSha256Assets": len(records),
        "resolvedImageAssets": sum(record["kind"] == "image" for record in records.values()),
        "missingAssets": sum(record["kind"] == "missing" for record in records.values()),
        "missingReferenceOccurrences": missing_references,
        "formulaReferenceOccurrencesExcluded": formula_reference_occurrences_excluded,
        "recoveredMisflaggedGlyphTexOccurrences": recovered_glyph_tex_occurrences,
        "physicalReferencedPaths": len(physical_paths),
        "assetsReferencedByMultiplePhysicalPaths": duplicate_assets,
        "deduplicatedExtraPhysicalPaths": duplicate_physical_paths,
        "referenceOccurrences": sum(references_by_role.values()),
        "referencesByRole": dict(sorted(references_by_role.items())),
        "referencesByField": dict(sorted(references_by_field.items())),
        "questionAssetCardinality": {
            "oneAsset": sum(len(assets) == 1 for assets in question_asset_sets.values()),
            "multipleAssets": sum(len(assets) > 1 for assets in question_asset_sets.values()),
            "maximumAssetsPerQuestion": max(map(len, question_asset_sets.values()), default=0),
        },
        "answerLabels": dict(sorted(answer_labels.items())),
        "ruleEvidenceByQuestion": dict(sorted(rule_evidence.items())),
        "dedupeKey": "content_sha256",
    }
    questions_without_images = [graphic_question_rows[question_id] for question_id in sorted(set(graphic_question_rows) - questions_with_image)]
    return summary, records, knowledge_points, questions_without_images


def compare_inventory(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    existing_rows = read_jsonl(ROOT / "inventory.jsonl")
    existing = {row["assetId"]: row for row in existing_rows}
    rescanned_ids, existing_ids = set(records), set(existing)
    occurrence_mismatches: list[dict[str, Any]] = []
    path_mismatches: list[dict[str, Any]] = []
    for asset_id in sorted(rescanned_ids & existing_ids):
        rescan = records[asset_id]
        current = existing[asset_id]
        if len(rescan["occurrences"]) != len(current.get("occurrences", [])):
            occurrence_mismatches.append({"assetId": asset_id, "rescanned": len(rescan["occurrences"]), "existing": len(current.get("occurrences", []))})
        if set(rescan["localPaths"]) != set(current.get("localPaths", [])):
            path_mismatches.append({"assetId": asset_id, "rescanned": len(rescan["localPaths"]), "existing": len(current.get("localPaths", []))})
    return {
        "schemaVersion": "graphic-reasoning-inventory-comparison-v1",
        "existingAssets": len(existing),
        "rescannedAssets": len(records),
        "onlyInExisting": sorted(existing_ids - rescanned_ids),
        "onlyInRescan": sorted(rescanned_ids - existing_ids),
        "sha256Mismatches": sorted(asset_id for asset_id in rescanned_ids & existing_ids if records[asset_id]["sha256"] != existing[asset_id].get("sha256")),
        "occurrenceCountMismatches": occurrence_mismatches,
        "localPathSetMismatches": path_mismatches,
        "status": "exact_match" if rescanned_ids == existing_ids and not occurrence_mismatches and not path_mismatches else "legacy_inventory_gap_detected",
    }


def processing_coverage() -> dict[str, Any]:
    inventory = read_jsonl(ROOT / "inventory.jsonl")
    classifications = read_jsonl(ROOT / "classification.jsonl")
    manifest = read_jsonl(ROOT / "manifest.jsonl")
    asset_root = ROOT / "assets"
    output_dirs = sorted(path for path in asset_root.iterdir() if path.is_dir()) if asset_root.is_dir() else []
    required = ("structured.json", "redrawn.svg", "redrawn.png", "comparison.png", "acceptance.json")
    complete_dirs = [path for path in output_dirs if all((path / filename).is_file() for filename in required)]
    completion_counts: Counter[str] = Counter()
    repair_counts: Counter[str] = Counter()
    for directory in complete_dirs:
        value = json.loads((directory / "acceptance.json").read_text(encoding="utf-8"))
        completion_counts[str(value.get("completion") or "missing")] += 1
        repair_counts[str((value.get("repairStatus") or {}).get("status") or "missing")] += 1
    manifest_status = Counter(str(row.get("status") or "missing") for row in manifest)
    manifest_valid = {row["assetId"] for row in manifest if row.get("status") == "valid"}
    accepted = []
    for asset_id in sorted(manifest_valid):
        path = asset_root / asset_id / "acceptance.json"
        if path.is_file():
            evidence = json.loads(path.read_text(encoding="utf-8"))
            if (evidence.get("repairStatus") or {}).get("status") == "clear":
                accepted.append(asset_id)
    inventory_ids = {row["assetId"] for row in inventory}
    output_ids = {path.name for path in output_dirs}
    return {
        "schemaVersion": "graphic-reasoning-processing-coverage-v1",
        "inventoryAssets": len(inventory),
        "classifiedAssets": len(classifications),
        "classificationCoverage": round(len(classifications) / max(1, len(inventory)), 6),
        "outputDirectories": len(output_dirs),
        "completeArtifactSets": len(complete_dirs),
        "generatedButNotFinalAssets": len(complete_dirs),
        "assetsWithoutOutputDirectory": len(inventory_ids - output_ids),
        "outputDirectoriesNotInInventory": sorted(output_ids - inventory_ids),
        "manifestEntries": len(manifest),
        "manifestStatusCounts": dict(sorted(manifest_status.items())),
        "acceptanceCompletionCounts": dict(sorted(completion_counts.items())),
        "acceptanceRepairStatusCounts": dict(sorted(repair_counts.items())),
        "finalAcceptedAssetIds": accepted,
        "finalAcceptedAssets": len(accepted),
        "statusRule": "Only manifest status=valid plus acceptance repairStatus=clear is final. File generation alone is not processed success.",
    }


def type_coverage(knowledge_points: Counter[str]) -> dict[str, Any]:
    rows = read_jsonl(ROOT / "classification.jsonl")
    family_specs = {
        "straight_lines_right_angles": {
            "categories": {"straight_line"}, "subtypes": {"straight_line_network", "grid_partition", "curve_line_composition"}, "grammars": {"regular_geometry"},
            "requiredSemantic": ["line count", "orientation", "shared endpoints", "exact angles"],
        },
        "circles_ellipses_arcs_curves": {
            "categories": {"circle_ellipse_arc", "curve_bezier"}, "subtypes": {"circle_composition", "circle_curve_composition", "irregular_curve_composition", "curve_line_composition"}, "grammars": {"curve_circle_arc"},
            "requiredSemantic": ["circle/ellipse/arc/Bezier type", "center/radius/angle", "open/closed"],
        },
        "glyph_chinese_letters_digits": {
            "categories": {"glyph"}, "subtypes": {"glyph_geometry_composition"}, "grammars": {"glyph_life_object"},
            "requiredSemantic": ["textGlyph content", "font category", "position", "reading direction"],
        },
        "life_objects_and_silhouettes": {
            "categories": {"life_outline"}, "subtypes": {"complex_silhouette_candidate", "semantic_life_object", "tonal_life_or_raster_candidate"}, "grammars": {"glyph_life_object"},
            "requiredSemantic": ["object label", "part graph", "held/accessory objects", "pose and topology", "not outer contour only"],
        },
        "folding_3d_wireframes": {
            "categories": {"3d_fold"}, "subtypes": {"folded_3d_candidate"}, "grammars": {"folded_3d"},
            "requiredSemantic": ["vertices", "edges", "faces", "fold adjacency", "view direction"],
        },
        "filled_shaded_overlay": {
            "categories": {"shaded_polygon"}, "subtypes": {"filled_region_composition", "textured_sector_candidate"}, "grammars": {"textured_geometry", "textured_pie"},
            "requiredSemantic": ["hollow/solid", "overlap order", "Boolean overlay", "texture identity"],
        },
        "dots_grids_matrices": {
            "categories": {"grid_dot"}, "subtypes": {"grid_partition", "dot_marker_composition"}, "grammars": {"textured_geometry", "textured_pie"},
            "requiredSemantic": ["rows/columns", "occupancy", "dot coordinates", "cell movement"],
        },
        "arrows_directional_symbols": {
            "categories": {"arrow_direction"}, "subtypes": {"semantic_symbol_candidate", "directional_symbol_composition"}, "grammars": set(),
            "requiredSemantic": ["semanticSymbol", "head/tail", "direction vector", "rotation/reflection"],
        },
    }
    results: dict[str, Any] = {}
    for name, spec in family_specs.items():
        assets = set()
        evidence = Counter()
        for row in rows:
            categories = set(row.get("categories") or [])
            subtype = str(row.get("regularSubtype") or "")
            grammar = str(row.get("visualGrammar") or "")
            matched = False
            if categories & spec["categories"]:
                evidence["classification_category"] += 1
                matched = True
            if subtype in spec["subtypes"]:
                evidence["regular_subtype"] += 1
                matched = True
            if grammar in spec["grammars"]:
                evidence["visual_grammar"] += 1
                matched = True
            if matched:
                assets.add(row["assetId"])
        results[name] = {
            "matchedAssets": len(assets),
            "evidenceHits": dict(sorted(evidence.items())),
            "requiredSemanticFields": spec["requiredSemantic"],
            "inventoryCoverage": "present" if assets else "missing",
            "finalAcceptedAssets": 0,
            "gap": "classified_or_routed_only; semantic observations and renderer acceptance are not complete",
        }
    rule_hits = {rule: sum(count for knowledge, count in knowledge_points.items() if any(term in knowledge for term in terms)) for rule, terms in RULE_TERMS.items()}
    return {
        "schemaVersion": "graphic-reasoning-type-coverage-v1",
        "families": results,
        "crossPanelRuleEvidenceByQuestion": dict(sorted(rule_hits.items())),
        "scopeDecision": {
            "naturalPhotos": "out_of_batch_only_with_asset-level evidence",
            "lifeSilhouettes": "in_scope; must preserve semantic parts and topology",
            "blanketLifeOrSilhouetteExclusion": "prohibited_by_latest_user_direction",
        },
        "status": "all_required_families_have_inventory_evidence_but_zero_are_final",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=AUDIT_ROOT)
    args = parser.parse_args()
    summary, records, knowledge, questions_without_images = rescan_source()
    comparison = compare_inventory(records)
    processing = processing_coverage()
    types = type_coverage(knowledge)
    write_json(args.output_root / "rescan-summary.json", summary)
    write_json(args.output_root / "inventory-comparison.json", comparison)
    write_json(args.output_root / "processing-coverage.json", processing)
    write_json(args.output_root / "type-coverage.json", types)
    write_jsonl(args.output_root / "top-knowledge-points.jsonl", ({"knowledgePoint": key, "questionCount": value} for key, value in knowledge.most_common(200)))
    write_jsonl(args.output_root / "questions-without-image.jsonl", questions_without_images)
    write_jsonl(args.output_root / "text-glyph-question-inventory.jsonl", ({
        "schemaVersion": "graphic-reasoning-text-glyph-question-v1",
        "questionId": row["questionId"],
        "source": row["source"],
        "sourceJsonlLine": row["sourceJsonlLine"],
        "semanticLayer": {
            "kind": "textGlyphSequence",
            "stemText": row["stemText"],
            "options": row["optionsText"],
            "relations": [],
            "status": "source_text_available_relation_inference_pending"
        },
        "renderLayer": {"schemaVersion": "graphic-primitives-v2", "primitiveType": "textGlyph", "status": "render_pending"},
        "evaluation": {"answerLabel": row["answerLabel"], "solverInputExcludesAnswer": True},
        "acceptance": {"status": "repair_pending", "reason": "textGlyph panel mapping and answer-preservation render gate pending"}
    } for row in questions_without_images))
    write_json(args.output_root / "audit-state.json", {
        "schemaVersion": "graphic-reasoning-cpu-audit-state-v1",
        "status": "complete" if comparison["status"] == "exact_match" else "gap_identified_repair_pending",
        "gpuCalls": 0,
        "endpointCalls": 0,
        "inventoryMatch": comparison["status"],
        "finalAcceptedAssets": processing["finalAcceptedAssets"],
        "outputs": ["rescan-summary.json", "inventory-comparison.json", "processing-coverage.json", "type-coverage.json", "top-knowledge-points.jsonl", "questions-without-image.jsonl", "text-glyph-question-inventory.jsonl"],
    })
    print(json.dumps({"assets": summary["uniqueContentSha256Assets"], "inventoryMatch": comparison["status"], "finalAccepted": processing["finalAcceptedAssets"]}, ensure_ascii=False))
    return 0 if processing["finalAcceptedAssets"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
