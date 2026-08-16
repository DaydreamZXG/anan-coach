#!/usr/bin/env python3
"""Build the public crowdtest attachment and private evaluation package."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PACKAGE_DIR = Path(__file__).resolve().parent
BATCH_DIR = PACKAGE_DIR.parent
QUEUE_PATH = BATCH_DIR / "semantic-structure" / "queue.jsonl"
PUBLIC_TEMPLATE = PACKAGE_DIR / "public-template"
PRIVATE_TEMPLATE = PACKAGE_DIR / "private-template"
SUBMISSION_FIELDS = PACKAGE_DIR / "submission-fields"
BUILD_DIR = PACKAGE_DIR / "build"
DIST_DIR = PACKAGE_DIR / "dist"
PUBLIC_NAME = "graphic-reasoning-semantic-public-v1"
PRIVATE_NAME = "graphic-reasoning-semantic-private-evaluation-v1"
USER_EXCLUDED_ASSET = "gr_03633503ff1443587d48e1ee"

MANUAL_QUOTAS = {
    "regular_geometry": 79,
    "folded_3d": 43,
    "rare_vector": 33,
    "glyph_life_object": 27,
    "textured_geometry": 20,
    "textured_pie": 5,
    "curve_circle_arc": 3,
}
DEEPSEEK_QUOTAS = {
    "regular_geometry": 280,
    "folded_3d": 45,
    "rare_vector": 35,
    "glyph_life_object": 20,
    "textured_geometry": 12,
    "textured_pie": 5,
    "curve_circle_arc": 3,
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(value: str) -> str:
    return hashlib.sha256(("graphic-reasoning-crowdtest-v1:" + value).encode("utf-8")).hexdigest()


def first_existing(paths: list[str]) -> Path:
    for value in paths:
        path = Path(value)
        if path.is_file():
            return path
    raise FileNotFoundError(paths[0] if paths else "missing source path")


def option_label(field: str) -> str | None:
    match = re.fullmatch(r"options\[(\d+)]\.html", field or "")
    return chr(65 + int(match.group(1))) if match else None


def public_references(row: dict[str, Any]) -> list[dict[str, Any]]:
    references = []
    for ref in row["questionReferences"]:
        if ref.get("role") not in {"stem", "option"}:
            continue
        references.append({
            "caseId": "case_" + hashlib.sha256(ref["questionId"].encode("utf-8")).hexdigest()[:20],
            "role": ref["role"],
            "field": ref["field"],
            "optionLabel": option_label(ref["field"]),
        })
    return sorted(references, key=lambda value: (value["caseId"], value["role"], value["field"]))


def select_stratified(rows: list[dict[str, Any]], quotas: dict[str, int], identity_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["visualGrammar"]].append(row)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for grammar, quota in quotas.items():
        candidates = sorted(grouped.get(grammar, []), key=lambda row: stable_key(str(row[identity_key])))
        chosen = candidates[:quota]
        selected.extend(chosen)
        selected_ids.update(str(row[identity_key]) for row in chosen)
    target = sum(quotas.values())
    if len(selected) < target:
        remainder = sorted((row for row in rows if str(row[identity_key]) not in selected_ids), key=lambda row: stable_key(str(row[identity_key])))
        selected.extend(remainder[: target - len(selected)])
    if len(selected) != target:
        raise RuntimeError(f"could not build requested sample of {target}; got {len(selected)}")
    return sorted(selected, key=lambda row: (row["visualGrammar"], stable_key(str(row[identity_key]))))


def build_question_key(eligible_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    asset_grammar = {row["assetId"]: row["visualGrammarCandidate"] for row in eligible_rows}
    for row in eligible_rows:
        for ref in row["questionReferences"]:
            if ref.get("role") not in {"stem", "option"}:
                continue
            question_id = ref["questionId"]
            entry = questions.setdefault(question_id, {"questionId": question_id, "answers": set(), "assets": []})
            entry["answers"].add(ref.get("answerLabel"))
            asset_ref = {
                "assetId": row["assetId"],
                "role": ref["role"],
                "field": ref["field"],
                "optionLabel": option_label(ref["field"]),
            }
            if asset_ref not in entry["assets"]:
                entry["assets"].append(asset_ref)

    result = []
    for question_id, entry in questions.items():
        answers = entry.pop("answers")
        if len(answers) != 1:
            raise RuntimeError(f"answer conflict for {question_id}: {answers}")
        assets = sorted(entry["assets"], key=lambda value: (0 if value["role"] == "stem" else 1, value.get("optionLabel") or "", value["assetId"]))
        stem_grammars = [asset_grammar[value["assetId"]] for value in assets if value["role"] == "stem"]
        grammars = stem_grammars or [asset_grammar[value["assetId"]] for value in assets]
        grammar = Counter(grammars).most_common(1)[0][0]
        result.append({
            "questionId": question_id,
            "officialAnswer": next(iter(answers)),
            "visualGrammar": grammar,
            "assets": assets,
        })
    return sorted(result, key=lambda value: value["questionId"])


def unique_deepseek_candidates(question_key: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signatures: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in question_key:
        if row["officialAnswer"] not in {"A", "B", "C", "D"}:
            continue
        if not any(asset["role"] == "stem" for asset in row["assets"]):
            continue
        signature = tuple((asset["assetId"], asset["role"], asset.get("optionLabel")) for asset in row["assets"])
        signatures[signature].append(row)
    candidates = []
    for rows in signatures.values():
        answers = {row["officialAnswer"] for row in rows}
        if len(answers) != 1:
            continue
        representative = min(rows, key=lambda row: stable_key(row["questionId"]))
        candidates.append(representative)
    return candidates


def zip_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    with zipfile.ZipFile(destination, "w", allowZip64=True) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = Path(source.name) / path.relative_to(source)
            compression = zipfile.ZIP_STORED if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip"} else zipfile.ZIP_DEFLATED
            archive.write(path, relative.as_posix(), compress_type=compression)


def main() -> int:
    queue = read_jsonl(QUEUE_PATH)
    analysis_excluded = []
    user_excluded = []
    eligible = []
    for row in queue:
        roles = {ref.get("role") for ref in row["questionReferences"]}
        if row["assetId"] == USER_EXCLUDED_ASSET:
            user_excluded.append(row)
        elif roles <= {"analysis", "solution"}:
            analysis_excluded.append(row)
        else:
            eligible.append(row)
    if (len(queue), len(eligible), len(analysis_excluded), len(user_excluded)) != (4857, 3601, 1255, 1):
        raise RuntimeError("unexpected scope counts")

    public_root = BUILD_DIR / PUBLIC_NAME
    private_root = BUILD_DIR / PRIVATE_NAME
    for path in [public_root, private_root]:
        if path.exists():
            shutil.rmtree(path)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PUBLIC_TEMPLATE, public_root)
    shutil.copytree(PRIVATE_TEMPLATE, private_root)
    (public_root / "input" / "images").mkdir(parents=True, exist_ok=True)
    (public_root / "output" / "assets").mkdir(parents=True, exist_ok=True)

    inventory_rows: list[dict[str, Any]] = []
    checksum_lines: list[str] = []
    total_image_bytes = 0
    for row in sorted(eligible, key=lambda value: value["assetId"]):
        source = first_existing(row["sourcePaths"])
        extension = source.suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".gif"}:
            raise RuntimeError(f"unsupported image extension: {source}")
        expected_hash = row["sourceSha256"]
        actual_hash = sha256_file(source)
        if actual_hash != expected_hash:
            raise RuntimeError(f"source hash mismatch for {row['assetId']}")
        relative_image = Path("input") / "images" / f'{row["assetId"]}{extension}'
        target = public_root / relative_image
        shutil.copy2(source, target)
        total_image_bytes += target.stat().st_size
        checksum_lines.append(f"{expected_hash}  {relative_image.as_posix()}")
        inventory_rows.append({
            "schemaVersion": "graphic-reasoning-semantic-public-job-v1",
            "assetId": row["assetId"],
            "sourceSha256": expected_hash,
            "imagePath": relative_image.as_posix(),
            "questionReferences": public_references(row),
            "layoutCandidate": row.get("layoutCandidate"),
            "visualGrammarCandidate": row.get("visualGrammarCandidate"),
            "regularSubtypeCandidate": row.get("regularSubtypeCandidate"),
            "priority": row.get("priority", 100),
            "status": "pending",
        })
    write_jsonl(public_root / "input" / "inventory-public.jsonl", inventory_rows)
    (public_root / "input" / "CONTENT_SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    grammar_counts = Counter(row["visualGrammarCandidate"] for row in eligible)
    scope_summary = {
        "schemaVersion": "graphic-reasoning-public-scope-v1",
        "candidateAssetCount": len(queue),
        "publicEligibleAssetCount": len(eligible),
        "analysisOrSolutionOnlyExcludedCount": len(analysis_excluded),
        "userExcludedCount": len(user_excluded),
        "deduplication": "sha256_content_hash",
        "publicAnswerFieldsPresent": False,
        "imageBytes": total_image_bytes,
        "visualGrammarCounts": dict(sorted(grammar_counts.items())),
    }
    (public_root / "input" / "scope-summary.json").write_text(json.dumps(scope_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    question_key = build_question_key(eligible)
    write_jsonl(private_root / "question-key-private.jsonl", question_key)
    asset_key = []
    for row in sorted(eligible, key=lambda value: value["assetId"]):
        answers = sorted({ref["answerLabel"] for ref in row["questionReferences"] if ref.get("role") in {"stem", "option"}})
        asset_key.append({
            "assetId": row["assetId"],
            "sourceSha256": row["sourceSha256"],
            "visualGrammar": row["visualGrammarCandidate"],
            "officialAnswers": answers,
            "questionIds": sorted({ref["questionId"] for ref in row["questionReferences"] if ref.get("role") in {"stem", "option"}}),
        })
    write_jsonl(private_root / "asset-answer-key-private.jsonl", asset_key)

    exclusion_rows = []
    for row in sorted(analysis_excluded, key=lambda value: value["assetId"]):
        exclusion_rows.append({
            "assetId": row["assetId"],
            "sourceSha256": row["sourceSha256"],
            "reason": "analysis_solution_only_possible_answer_annotation",
            "referenceRoles": sorted({ref["role"] for ref in row["questionReferences"]}),
        })
    for row in user_excluded:
        exclusion_rows.append({
            "assetId": row["assetId"],
            "sourceSha256": row["sourceSha256"],
            "reason": "explicitly_excluded_by_user_traditional_figure_painting",
            "referenceRoles": sorted({ref["role"] for ref in row["questionReferences"]}),
        })
    write_jsonl(private_root / "excluded-assets-private.jsonl", exclusion_rows)

    manual_candidates = [{
        "assetId": row["assetId"],
        "imagePath": next(value["imagePath"] for value in inventory_rows if value["assetId"] == row["assetId"]),
        "sourceSha256": row["sourceSha256"],
        "visualGrammar": row["visualGrammarCandidate"],
        "regularSubtype": row.get("regularSubtypeCandidate"),
        "priority": row.get("priority", 100),
    } for row in eligible]
    manual_sample = select_stratified(manual_candidates, MANUAL_QUOTAS, "assetId")
    priority_rows = [row for row in manual_candidates if row["priority"] < 100]
    selected_ids = {row["assetId"] for row in manual_sample}
    for priority_row in priority_rows:
        if priority_row["assetId"] in selected_ids:
            continue
        replacement_index = next(index for index in range(len(manual_sample) - 1, -1, -1) if manual_sample[index]["visualGrammar"] == priority_row["visualGrammar"] and manual_sample[index]["priority"] == 100)
        selected_ids.remove(manual_sample[replacement_index]["assetId"])
        manual_sample[replacement_index] = priority_row
        selected_ids.add(priority_row["assetId"])
    manual_path = private_root / "manual-review-sample-private.csv"
    manual_fields = ["assetId", "imagePath", "sourceSha256", "visualGrammar", "regularSubtype", "priority", "layout_panel_option_order", "line_curve_right_angle", "text_glyph", "fill_texture", "count_direction_position", "topology_parts_3d", "critical_error", "notes"]
    with manual_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=manual_fields)
        writer.writeheader()
        for row in manual_sample:
            writer.writerow({**row, "layout_panel_option_order": "", "line_curve_right_angle": "", "text_glyph": "", "fill_texture": "", "count_direction_position": "", "topology_parts_3d": "", "critical_error": "", "notes": ""})

    deepseek_candidates = unique_deepseek_candidates(question_key)
    deepseek_sample = select_stratified(deepseek_candidates, DEEPSEEK_QUOTAS, "questionId")
    write_jsonl(private_root / "deepseek-sample-private.jsonl", [{
        "questionId": row["questionId"],
        "visualGrammar": row["visualGrammar"],
        "assetIds": [asset["assetId"] for asset in row["assets"]],
    } for row in deepseek_sample])

    shutil.copytree(SUBMISSION_FIELDS, private_root / "submission-fields")
    private_summary = {
        "schemaVersion": "graphic-reasoning-private-evaluation-scope-v1",
        "publicEligibleAssetCount": len(eligible),
        "questionKeyCount": len(question_key),
        "manualReviewCount": len(manual_sample),
        "deepseekBlindQuestionCount": len(deepseek_sample),
        "excludedAssetCount": len(exclusion_rows),
        "manualGrammarCounts": dict(sorted(Counter(row["visualGrammar"] for row in manual_sample).items())),
        "deepseekGrammarCounts": dict(sorted(Counter(row["visualGrammar"] for row in deepseek_sample).items())),
    }
    (private_root / "evaluation-scope-summary.json").write_text(json.dumps(private_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    public_zip = DIST_DIR / f"{PUBLIC_NAME}.zip"
    private_zip = DIST_DIR / f"{PRIVATE_NAME}.zip"
    zip_tree(public_root, public_zip)
    zip_tree(private_root, private_zip)
    dist_rows = []
    for path, exposure in [(public_zip, "upload_to_crowdtest"), (private_zip, "keep_private")]:
        dist_rows.append({"file": path.name, "exposure": exposure, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_jsonl(DIST_DIR / "package-manifest.jsonl", dist_rows)
    report = {"public": scope_summary, "private": private_summary, "archives": dist_rows}
    (PACKAGE_DIR / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
