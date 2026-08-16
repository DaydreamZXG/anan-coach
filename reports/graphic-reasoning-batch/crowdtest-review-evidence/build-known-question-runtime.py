#!/usr/bin/env python3
"""Build an answer-isolated runtime package for known graphic-reasoning questions.

The answer-free semantic scenes remain the only visual-description layer.  Official
answers and original source analyses are stored in separate per-question solution
files.  This lets known question-bank items resolve without another model call while
keeping blind semantic evaluation free of answer leakage.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[2]
DEFAULT_PRIVATE_ROOT = (
    HERE.parent / "crowdtest-package/build/graphic-reasoning-semantic-private-evaluation-v1"
)
DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "data/question-bank-export/files/source-question-bank/"
    "fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
)
DEFAULT_SAMPLE = HERE / "deepseek-benchmark/runs/full-coverage-v1/full-sample.jsonl"
DEFAULT_PRIOR_RUN = HERE / "deepseek-benchmark/runs/full-coverage-v1"
DEFAULT_UNIFIED = HERE / "unified-semantic-assets-v1"
DEFAULT_FUSION = HERE / "pure-text-semantic-fusion-v1"
DEFAULT_OUTPUT = HERE / "known-question-runtime-v1"

MODELS = ("summit", "rudder", "quarry", "pelican")
ANSWER_KEYS = {
    "answer",
    "officialAnswer",
    "correctAnswer",
    "answerKey",
    "finalAnswer",
    "selectedOption",
}
VALID_SINGLE_ANSWERS = {"A", "B", "C", "D"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_key(question_id: str) -> str:
    value = "graphic-reasoning-crowdtest-v1:" + question_id
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def signature(row: dict[str, Any]) -> tuple[tuple[str, str, str | None], ...]:
    return tuple(
        (asset["assetId"], asset["role"], asset.get("optionLabel"))
        for asset in row["assets"]
    )


def contains_answer_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in ANSWER_KEYS or contains_answer_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_answer_key(child) for child in value)
    return False


def compact_text(value: Any) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<img\b[^>]*>", " [图] ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def load_source_rows(path: Path, target_ids: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            question_id = row.get("id")
            if question_id not in target_ids:
                continue
            if question_id in result:
                raise RuntimeError(f"duplicate source question id at line {line_number}: {question_id}")
            result[question_id] = row
    missing = sorted(target_ids - set(result))
    if missing:
        raise RuntimeError(f"source question rows missing: {len(missing)}; first={missing[:5]}")
    return result


def selected_analysis(row: dict[str, Any]) -> tuple[str, str, str]:
    text = compact_text(row.get("final_analysis") or row.get("original_analysis"))
    html_value = row.get("original_solution_html") or row.get("original_analysis_html") or ""
    source = row.get("final_analysis_source") or "original_analysis"
    return text, html_value, source


def relative_reference(from_root: Path, target: Path) -> str:
    return str(target.relative_to(from_root.parent)) if target.is_relative_to(from_root.parent) else str(target)


def build_readme(summary: dict[str, Any]) -> str:
    return f"""# 已知图形推理题运行时 v1

本目录把视觉语义与答案严格分层，生成过程未调用任何外部模型或 API。

## 覆盖

- 原始题目引用：{summary['rawQuestionReferences']:,}
- 标准盲测去重题目：{summary['blindBenchmarkCanonicalQuestions']:,}
- 含边界题的运行时解答：{summary['runtimeSolutionRecords']:,}
- 无答案语义资产：{summary['semanticAssets']:,}
- 官方答案覆盖：{summary['officialAnswerCoveragePercent']:.1f}%
- 原解析文本覆盖：{summary['analysisTextCoveragePercent']:.1f}%
- 构建错误：{summary['errorCount']}

## 分层

1. `../unified-semantic-assets-v1/assets/*.json` 是无答案语义场景，供 AI 理解图片。
2. `solutions/*.json` 是已知题的官方答案与原解析，只能在题目身份确认后读取。
3. `aliases.jsonl` 将题库中的全部题目引用映射到去重后的 `canonicalQuestionId`。
4. `asset-index.jsonl` 支持按原始图片 SHA-256 精确命中已知题。
5. `manifest.jsonl` 是逐题运行时索引；`summary.json`、`validation-report.json` 与 `errors.jsonl` 是验收证据。

盲测、陌生题识别和语义质量评估时，禁止把 `solutions/` 提供给模型。前端仍可展示原图；语义 JSON 用于检索、解释和辅助推理，不承担像素级重绘。

## 运行策略

- 已知题：由题目 ID 或视觉资产签名命中 `aliases.jsonl`，直接读取官方答案与原解析，零模型调用。
- 未知题：先由视觉模型生成同一语义契约，再交给推理模型；不得用本目录答案做近似猜测。

## 复跑

```bash
python3 ../build-known-question-runtime.py
```
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--prior-run", type=Path, default=DEFAULT_PRIOR_RUN)
    parser.add_argument("--unified", type=Path, default=DEFAULT_UNIFIED)
    parser.add_argument("--fusion", type=Path, default=DEFAULT_FUSION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    errors: list[dict[str, Any]] = []
    question_key = read_jsonl(args.private_root / "question-key-private.jsonl")
    sample = read_jsonl(args.sample)
    unified_manifest = {
        row["assetId"]: row for row in read_jsonl(args.unified / "manifest.jsonl")
    }
    fusion_manifest = {
        row["assetId"]: row for row in read_jsonl(args.fusion / "manifest.jsonl")
    }

    if len(question_key) != 6268 or len({row["questionId"] for row in question_key}) != 6268:
        errors.append({"code": "question_key_count", "actual": len(question_key), "expected": 6268})

    groups: dict[tuple[tuple[str, str, str | None], ...], list[dict[str, Any]]] = defaultdict(list)
    for row in question_key:
        if not str(row.get("officialAnswer") or "").strip():
            errors.append({"code": "missing_private_answer", "questionId": row.get("questionId")})
            continue
        groups[signature(row)].append(row)

    sample_by_question = {row["questionId"]: row for row in sample}
    sample_by_signature: dict[tuple[tuple[str, str, str | None], ...], dict[str, Any]] = {}
    key_by_question = {row["questionId"]: row for row in question_key}
    for sample_row in sample:
        key_row = key_by_question.get(sample_row["questionId"])
        if not key_row:
            errors.append({"code": "sample_question_missing_from_key", "questionId": sample_row["questionId"]})
            continue
        sample_by_signature[signature(key_row)] = sample_row

    if len(groups) != 3417:
        errors.append({"code": "runtime_group_count", "actual": len(groups), "expected": 3417})
    if len(sample_by_signature) != 3413:
        errors.append({
            "code": "sample_signature_coverage",
            "actual": len(sample_by_signature),
            "expected": 3413,
        })

    target_ids = {row["questionId"] for row in question_key}
    source_rows = load_source_rows(args.source, target_ids)

    predictions: dict[str, dict[str, dict[str, Any]]] = {}
    for model in MODELS:
        path = args.prior_run / model / "predictions.jsonl"
        rows = read_jsonl(path) if path.exists() else []
        predictions[model] = {row["questionId"]: row for row in rows}

    semantic_answer_free: dict[str, bool] = {}
    semantic_hash_ok: dict[str, bool] = {}
    for asset_id, manifest_row in unified_manifest.items():
        semantic_path = args.unified / manifest_row["semanticJsonPath"]
        if not semantic_path.exists():
            semantic_answer_free[asset_id] = False
            semantic_hash_ok[asset_id] = False
            errors.append({"code": "semantic_file_missing", "assetId": asset_id})
            continue
        scene = json.loads(semantic_path.read_text(encoding="utf-8"))
        semantic_answer_free[asset_id] = not contains_answer_key(scene)
        semantic_hash_ok[asset_id] = digest_json(scene) == manifest_row["semanticJsonSha256"]
        if not semantic_answer_free[asset_id]:
            errors.append({"code": "answer_leak_in_semantic_scene", "assetId": asset_id})
        if not semantic_hash_ok[asset_id]:
            errors.append({"code": "semantic_hash_mismatch", "assetId": asset_id})

    args.output.mkdir(parents=True, exist_ok=True)
    solutions_dir = args.output / "solutions"
    solutions_dir.mkdir(parents=True, exist_ok=True)

    aliases: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    question_rows: list[dict[str, Any]] = []
    asset_index: dict[str, dict[str, Any]] = {}
    grammar_counts: Counter[str] = Counter()
    duplicate_counts: Counter[int] = Counter()
    analysis_status_counts: Counter[str] = Counter()
    answer_support_counts: Counter[int] = Counter()
    sufficient_support_counts: Counter[int] = Counter()
    benchmark_exclusion_counts: Counter[str] = Counter()
    source_review_flagged = 0

    ordered_groups = sorted(groups.items(), key=lambda item: stable_key(min(row["questionId"] for row in item[1])))
    for group_signature, rows in ordered_groups:
        sample_row = sample_by_signature.get(group_signature)
        benchmark_eligible = sample_row is not None
        canonical_id = (
            sample_row["questionId"]
            if sample_row
            else min((row["questionId"] for row in rows), key=stable_key)
        )
        representative = key_by_question[canonical_id]
        aliases_in_group = sorted(row["questionId"] for row in rows)
        private_answers = {row["officialAnswer"] for row in rows}
        source_answers = {source_rows[qid].get("answer") for qid in aliases_in_group}
        if len(private_answers) != 1 or source_answers != private_answers:
            errors.append({
                "code": "answer_conflict",
                "canonicalQuestionId": canonical_id,
                "privateAnswers": sorted(private_answers),
                "sourceAnswers": sorted(str(value) for value in source_answers),
            })
            continue
        official_answer = next(iter(private_answers))
        benchmark_exclusion_reasons = []
        if official_answer not in VALID_SINGLE_ANSWERS:
            benchmark_exclusion_reasons.append("non_single_option_answer")
        if not any(asset.get("role") == "stem" for asset in representative["assets"]):
            benchmark_exclusion_reasons.append("no_stem_image_asset")
        if benchmark_eligible != (not benchmark_exclusion_reasons):
            errors.append({
                "code": "benchmark_eligibility_mismatch",
                "canonicalQuestionId": canonical_id,
                "sampleEligible": benchmark_eligible,
                "derivedReasons": benchmark_exclusion_reasons,
            })
        source_row = source_rows[canonical_id]
        analysis_text, analysis_html, analysis_source = selected_analysis(source_row)
        if not analysis_text:
            errors.append({"code": "analysis_missing", "canonicalQuestionId": canonical_id})
            continue

        variant_hashes = {
            digest_json({
                "text": selected_analysis(source_rows[qid])[0],
                "html": selected_analysis(source_rows[qid])[1],
            })
            for qid in aliases_in_group
        }
        signature_sha = digest_json(group_signature)
        asset_records = []
        all_semantics_answer_free = True
        all_semantic_hashes_valid = True
        for asset in representative["assets"]:
            asset_id = asset["assetId"]
            unified_row = unified_manifest.get(asset_id)
            fusion_row = fusion_manifest.get(asset_id)
            if not unified_row or not fusion_row:
                errors.append({
                    "code": "semantic_manifest_missing",
                    "canonicalQuestionId": canonical_id,
                    "assetId": asset_id,
                })
                continue
            all_semantics_answer_free &= semantic_answer_free.get(asset_id, False)
            all_semantic_hashes_valid &= semantic_hash_ok.get(asset_id, False)
            asset_records.append({
                "assetId": asset_id,
                "role": asset["role"],
                "field": asset.get("field"),
                "optionLabel": asset.get("optionLabel"),
                "sourceSha256": unified_row["sourceSha256"],
                "sourceImagePath": f"../unified-semantic-assets-v1/{unified_row['sourceImagePath']}",
                "answerFreeScenePath": f"../unified-semantic-assets-v1/{unified_row['semanticJsonPath']}",
                "answerFreeSceneSha256": unified_row["semanticJsonSha256"],
                "fusionAuditPath": f"../pure-text-semantic-fusion-v1/{fusion_row['path']}",
                "fusionAuditSha256": fusion_row["sha256"],
            })

        correct_support = 0
        sufficient_support = 0
        blind_rows = []
        for model in MODELS:
            prediction = predictions[model].get(canonical_id)
            if not prediction:
                continue
            correct = prediction.get("answer") == official_answer
            sufficient = correct and prediction.get("informationSufficient") is True
            correct_support += int(correct)
            sufficient_support += int(sufficient)
            blind_rows.append({
                "submission": model,
                "answered": prediction.get("answer") in VALID_SINGLE_ANSWERS,
                "correct": correct,
                "informationSufficient": prediction.get("informationSufficient"),
                "correctAndSufficient": sufficient,
            })

        question = {
            "stemText": compact_text(source_row.get("stem_text") or source_row.get("stem_html")),
            "materialText": compact_text(source_row.get("material_text") or source_row.get("material_html")),
            "options": [
                {
                    "label": option.get("label"),
                    "text": compact_text(option.get("text") or option.get("html")),
                }
                for option in source_row.get("options") or []
            ],
        }
        knowledge_points = (
            ((source_row.get("graphic_reasoning") or {}).get("knowledge_points") or [])
            or ((source_row.get("primary_taxonomy") or {}).get("knowledge_points") or [])
        )
        solution = {
            "schemaVersion": "graphic-reasoning-known-question-solution-v1",
            "canonicalQuestionId": canonical_id,
            "questionIds": aliases_in_group,
            "duplicateQuestionCount": len(rows),
            "visualGrammar": representative["visualGrammar"],
            "blindBenchmarkEligibility": {
                "eligible": benchmark_eligible,
                "reasons": benchmark_exclusion_reasons,
            },
            "assetSignatureSha256": signature_sha,
            "question": question,
            "semanticAssets": asset_records,
            "solution": {
                "officialAnswer": official_answer,
                "analysisText": analysis_text,
                "analysisHtml": analysis_html,
                "analysisSource": analysis_source,
                "analysisStatus": source_row.get("final_analysis_status"),
                "sourceReviewFlag": bool(source_row.get("final_analysis_needs_review")),
                "knowledgePoints": knowledge_points,
            },
            "provenance": {
                "sourceQuestionId": canonical_id,
                "paperId": source_row.get("paper_id"),
                "paperTitle": source_row.get("paper_title"),
                "source": source_row.get("source"),
                "sourceRowSha256": digest_json(source_row),
                "analysisVariantCountAcrossAliases": len(variant_hashes),
                "analysisVariantSha256": sorted(variant_hashes),
            },
            "validation": {
                "sourceAnswerMatchesPrivateKey": True,
                "analysisPresent": True,
                "semanticAssetsAnswerFree": all_semantics_answer_free,
                "semanticAssetHashesValid": all_semantic_hashes_valid,
                "priorBlindPredictionCount": len(blind_rows),
                "priorBlindCorrectSupport": correct_support,
                "priorBlindCorrectAndSufficientSupport": sufficient_support,
                "priorBlindEvidence": blind_rows,
            },
        }
        solution_path = solutions_dir / f"{canonical_id}.json"
        write_json(solution_path, solution)
        solution_sha = sha256_file(solution_path)

        for question_id in aliases_in_group:
            aliases.append({
                "questionId": question_id,
                "canonicalQuestionId": canonical_id,
                "isCanonical": question_id == canonical_id,
                "assetSignatureSha256": signature_sha,
            })
        manifest_rows.append({
            "canonicalQuestionId": canonical_id,
            "visualGrammar": representative["visualGrammar"],
            "duplicateQuestionCount": len(rows),
            "assetCount": len(asset_records),
            "assetSignatureSha256": signature_sha,
            "solutionPath": f"solutions/{canonical_id}.json",
            "solutionSha256": solution_sha,
            "semanticAssetsAnswerFree": all_semantics_answer_free,
            "sourceAnswerMatchesPrivateKey": True,
            "analysisPresent": True,
            "sourceReviewFlag": bool(source_row.get("final_analysis_needs_review")),
            "blindBenchmarkEligible": benchmark_eligible,
            "blindBenchmarkExclusionReasons": benchmark_exclusion_reasons,
            "priorBlindCorrectSupport": correct_support,
            "priorBlindCorrectAndSufficientSupport": sufficient_support,
        })
        for asset in asset_records:
            indexed = asset_index.setdefault(asset["sourceSha256"], {
                "sourceSha256": asset["sourceSha256"],
                "assetIds": set(),
                "canonicalQuestionIds": set(),
                "references": [],
            })
            indexed["assetIds"].add(asset["assetId"])
            indexed["canonicalQuestionIds"].add(canonical_id)
            indexed["references"].append({
                "canonicalQuestionId": canonical_id,
                "assetId": asset["assetId"],
                "role": asset["role"],
                "optionLabel": asset["optionLabel"],
            })
        question_rows.append({
            "canonicalQuestionId": canonical_id,
            "questionIds": aliases_in_group,
            "visualGrammar": representative["visualGrammar"],
            "question": question,
            "semanticAssetIds": [asset["assetId"] for asset in asset_records],
            "solutionPath": f"solutions/{canonical_id}.json",
        })
        grammar_counts[representative["visualGrammar"]] += 1
        duplicate_counts[len(rows)] += 1
        analysis_status_counts[str(source_row.get("final_analysis_status") or "unknown")] += 1
        answer_support_counts[correct_support] += 1
        sufficient_support_counts[sufficient_support] += 1
        benchmark_exclusion_counts.update(benchmark_exclusion_reasons)
        source_review_flagged += int(bool(source_row.get("final_analysis_needs_review")))

    aliases.sort(key=lambda row: row["questionId"])
    manifest_rows.sort(key=lambda row: row["canonicalQuestionId"])
    question_rows.sort(key=lambda row: row["canonicalQuestionId"])
    asset_index_rows = [
        {
            "sourceSha256": value["sourceSha256"],
            "assetIds": sorted(value["assetIds"]),
            "canonicalQuestionIds": sorted(value["canonicalQuestionIds"]),
            "references": sorted(
                value["references"],
                key=lambda row: (
                    row["canonicalQuestionId"], row["assetId"], row["role"], row["optionLabel"] or ""
                ),
            ),
        }
        for value in asset_index.values()
    ]
    asset_index_rows.sort(key=lambda row: row["sourceSha256"])
    write_jsonl(args.output / "aliases.jsonl", aliases)
    write_jsonl(args.output / "manifest.jsonl", manifest_rows)
    write_jsonl(args.output / "questions.jsonl", question_rows)
    write_jsonl(args.output / "asset-index.jsonl", asset_index_rows)
    write_jsonl(args.output / "errors.jsonl", errors)

    canonical_count = len(manifest_rows)
    raw_count = len(aliases)
    summary = {
        "schemaVersion": "graphic-reasoning-known-question-runtime-summary-v1",
        "buildMode": "local_source_join_no_model_calls",
        "rawQuestionReferences": raw_count,
        "blindBenchmarkCanonicalQuestions": len(sample_by_signature),
        "runtimeSolutionRecords": canonical_count,
        "boundarySolutionRecords": canonical_count - len(sample_by_signature),
        "semanticAssets": len(unified_manifest),
        "indexedSourceImageHashes": len(asset_index_rows),
        "officialAnswerCoveragePercent": round(100 * canonical_count / len(groups), 4) if groups else 0,
        "analysisTextCoveragePercent": round(100 * canonical_count / len(groups), 4) if groups else 0,
        "aliasCoveragePercent": round(100 * raw_count / len(question_key), 4) if question_key else 0,
        "visualGrammarCounts": dict(sorted(grammar_counts.items())),
        "duplicateReferenceCountDistribution": {str(k): v for k, v in sorted(duplicate_counts.items())},
        "analysisStatusCounts": dict(sorted(analysis_status_counts.items())),
        "blindBenchmarkExclusionCounts": dict(sorted(benchmark_exclusion_counts.items())),
        "sourceReviewFlaggedCanonicalQuestions": source_review_flagged,
        "priorBlindCorrectSupportDistribution": {str(k): v for k, v in sorted(answer_support_counts.items())},
        "priorBlindCorrectAndSufficientSupportDistribution": {
            str(k): v for k, v in sorted(sufficient_support_counts.items())
        },
        "errorCount": len(errors),
        "knownQuestionPolicy": "identity_match_then_official_solution_without_model_call",
        "unknownQuestionPolicy": "vision_semantic_extraction_then_reasoning_model_or_review",
        "answerIsolation": "solutions are separate from answer-free semantic scene assets",
    }
    write_json(args.output / "summary.json", summary)
    (args.output / "README.md").write_text(build_readme(summary), encoding="utf-8")

    expected_solution_files = {f"{row['canonicalQuestionId']}.json" for row in manifest_rows}
    actual_solution_files = {path.name for path in solutions_dir.glob("*.json")}
    if actual_solution_files != expected_solution_files:
        mismatch = {
            "code": "solution_file_set_mismatch",
            "missing": sorted(expected_solution_files - actual_solution_files)[:20],
            "extra": sorted(actual_solution_files - expected_solution_files)[:20],
        }
        errors.append(mismatch)
        write_jsonl(args.output / "errors.jsonl", errors)
        summary["errorCount"] = len(errors)
        write_json(args.output / "summary.json", summary)

    solution_hashes_valid = all(
        sha256_file(args.output / row["solutionPath"]) == row["solutionSha256"]
        for row in manifest_rows
    )
    semantic_references_exist = all(
        (args.output / asset_path).exists()
        for row in manifest_rows
        for asset_path in (
            asset["answerFreeScenePath"]
            for asset in json.loads(
                (args.output / row["solutionPath"]).read_text(encoding="utf-8")
            )["semanticAssets"]
        )
    )
    validation_report = {
        "schemaVersion": "graphic-reasoning-known-question-runtime-validation-v1",
        "valid": not errors and solution_hashes_valid and semantic_references_exist,
        "checks": {
            "rawQuestionReferences": {"expected": 6268, "actual": raw_count, "passed": raw_count == 6268},
            "blindBenchmarkCanonicalQuestions": {
                "expected": 3413,
                "actual": len(sample_by_signature),
                "passed": len(sample_by_signature) == 3413,
            },
            "runtimeSolutionRecords": {
                "expected": 3417,
                "actual": canonical_count,
                "passed": canonical_count == 3417,
            },
            "semanticAssets": {
                "expected": 3601,
                "actual": len(unified_manifest),
                "passed": len(unified_manifest) == 3601,
            },
            "solutionFileSetMatchesManifest": actual_solution_files == expected_solution_files,
            "solutionHashesValid": solution_hashes_valid,
            "semanticReferencesExist": semantic_references_exist,
            "semanticAssetsAnswerFree": all(row["semanticAssetsAnswerFree"] for row in manifest_rows),
            "sourceAnswersMatchPrivateKey": all(row["sourceAnswerMatchesPrivateKey"] for row in manifest_rows),
            "allAnalysesPresent": all(row["analysisPresent"] for row in manifest_rows),
            "errorsEmpty": not errors,
        },
        "errorCount": len(errors),
    }
    write_json(args.output / "validation-report.json", validation_report)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if validation_report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
