#!/usr/bin/env python3
"""Select one coherent semantic scene per asset from four crowd-test outputs.

Selection is performed at question level: every image referenced by one
question is taken from the same submission.  Official answers are used only
as a local admission signal and are never emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


HERE = Path(__file__).resolve().parent
CROSS_PATH = HERE / "cross-validate-submissions.py"
SPEC = importlib.util.spec_from_file_location("graphic_reasoning_cross", CROSS_PATH)
CROSS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(CROSS)

MODELS = CROSS.MODELS
SHORT_ANSWER_PROVIDER = CROSS.SHORT_ANSWER_PROVIDER
DEFAULT_RUN_DIR = HERE / "deepseek-benchmark/runs/full-coverage-v1"
DEFAULT_PRIVATE_ROOT = (
    HERE.parent / "crowdtest-package/build/graphic-reasoning-semantic-private-evaluation-v1"
)
DEFAULT_PUBLIC_ROOT = (
    HERE.parent / "crowdtest-package/build/graphic-reasoning-semantic-public-v1"
)
DEFAULT_OUTPUT = HERE / "unified-semantic-assets-v1"
ANSWER_KEYS = {
    "answer", "officialAnswer", "correctAnswer", "answerKey",
    "finalAnswer", "selectedOption",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def contains_answer_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in ANSWER_KEYS or contains_answer_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_answer_key(child) for child in value)
    return False


def load_submission(downloads_root: Path, model: str) -> dict[str, dict[str, Any]]:
    archives = sorted((downloads_root / model).glob("*.zip"))
    if len(archives) != 1:
        raise RuntimeError(f"expected one ZIP for {model}, found {len(archives)}")
    result = {}
    with zipfile.ZipFile(archives[0]) as archive:
        for member in archive.namelist():
            if "/output/assets/" not in member or not member.endswith(".json"):
                continue
            scene = json.loads(archive.read(member))
            result[scene["assetId"]] = scene
    return result


def scene_quality(scene: dict[str, Any], schema_valid: bool) -> tuple[Any, ...]:
    panels = scene.get("panels") or []
    extraction = scene.get("extraction") or {}
    uncertainty_count = (
        len(scene.get("globalUncertainties") or [])
        + len(extraction.get("repairReasons") or [])
        + len((scene.get("layout") or {}).get("layoutIssues") or [])
        + sum(len(panel.get("uncertainties") or []) for panel in panels)
    )
    elements = [
        element
        for panel in panels
        for element in (panel.get("visualFacts") or {}).get("elements") or []
    ]
    relations = sum(
        len((panel.get("visualFacts") or {}).get("relations") or [])
        for panel in panels
    )
    unknown_instances = sum(
        1
        for element in elements
        for instance in element.get("instances") or []
        if "unknown" in {
            instance.get("relativeRegion"), instance.get("orientation"),
            instance.get("lineCharacter"), instance.get("fillState"),
            instance.get("texture"),
        }
    )
    confidences = [element.get("confidence", 0) for element in elements]
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return (
        schema_valid,
        extraction.get("qualityStatus") == "structured_candidate",
        -uncertainty_count,
        -unknown_instances,
        average_confidence,
        min(relations, 64),
        min(len(elements), 256),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--semantic-threshold", type=float, default=0.60)
    parser.add_argument("--single-sufficient-average-threshold", type=float, default=0.65)
    args = parser.parse_args()

    sample = {row["questionId"]: row for row in read_jsonl(args.run_dir / "full-sample.jsonl")}
    answer_key = {
        row["questionId"]: row["officialAnswer"]
        for row in read_jsonl(args.private_root / "question-key-private.jsonl")
    }
    predictions = {
        model: {
            row["questionId"]: row
            for row in read_jsonl(args.run_dir / model / "predictions.jsonl")
        }
        for model in MODELS
    }
    submissions = {model: load_submission(args.downloads_root, model) for model in MODELS}
    all_asset_ids = set.intersection(*(set(submissions[model]) for model in MODELS))
    if len(all_asset_ids) != 3601:
        raise RuntimeError(f"expected 3601 shared assets, got {len(all_asset_ids)}")

    schema = json.loads((args.public_root / "contract/semantic-scene-v2.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    schema_valid = {
        model: {
            asset_id: not any(validator.iter_errors(scene))
            for asset_id, scene in submissions[model].items()
        }
        for model in MODELS
    }

    model_gate_rates = {}
    for model in MODELS:
        rows = [
            row for question_id, row in predictions[model].items()
            if question_id in answer_key and row.get("provider") != SHORT_ANSWER_PROVIDER
        ]
        passed = sum(
            row.get("answer") == answer_key[row["questionId"]]
            and row.get("informationSufficient") is True
            for row in rows
        )
        model_gate_rates[model] = passed / len(rows) if rows else 0.0

    asset_questions: dict[str, set[str]] = defaultdict(set)
    for question_id, row in sample.items():
        for asset_id in row["assetIds"]:
            asset_questions[asset_id].add(question_id)
    if any(len(question_ids) > 1 for question_ids in asset_questions.values()):
        raise RuntimeError("an asset maps to more than one deduplicated question")

    # Cache cross-submission semantic scores per asset.
    cross_evidence = {}
    for asset_id in all_asset_ids:
        scenes = {model: submissions[model][asset_id] for model in MODELS}
        pair_scores = {
            (left, right): CROSS.semantic_similarity(scenes[left], scenes[right])
            for index, left in enumerate(MODELS)
            for right in MODELS[index + 1:]
        }
        layouts = Counter(CROSS.layout_signature(scene) for scene in scenes.values())
        modal_layout, layout_support = layouts.most_common(1)[0]
        cross_evidence[asset_id] = {
            "modalLayout": modal_layout,
            "layoutSupport": layout_support,
            "peerSupport": {
                model: 1 + sum(
                    pair_scores.get((model, other), pair_scores.get((other, model), 0.0))
                    >= args.semantic_threshold
                    for other in MODELS if other != model
                )
                for model in MODELS
            },
            "averageSimilarity": {
                model: sum(
                    pair_scores.get((model, other), pair_scores.get((other, model), 0.0))
                    for other in MODELS if other != model
                ) / 3
                for model in MODELS
            },
        }

    def model_score(model: str, asset_ids: list[str]) -> tuple[Any, ...]:
        all_structured = all(
            submissions[model][asset_id]["extraction"].get("qualityStatus") == "structured_candidate"
            for asset_id in asset_ids
        )
        all_valid = all(schema_valid[model][asset_id] for asset_id in asset_ids)
        modal_layout_matches = sum(
            CROSS.layout_signature(submissions[model][asset_id]) == cross_evidence[asset_id]["modalLayout"]
            for asset_id in asset_ids
        )
        average_peer_support = sum(
            cross_evidence[asset_id]["peerSupport"][model] for asset_id in asset_ids
        ) / len(asset_ids)
        average_similarity = sum(
            cross_evidence[asset_id]["averageSimilarity"][model] for asset_id in asset_ids
        ) / len(asset_ids)
        local_quality = tuple(
            sum(values) / len(values)
            for values in zip(*(
                scene_quality(submissions[model][asset_id], schema_valid[model][asset_id])
                for asset_id in asset_ids
            ))
        )
        return (
            all_valid, all_structured, modal_layout_matches,
            average_peer_support, average_similarity,
            *local_quality, model_gate_rates[model], model,
        )

    question_rows = []
    selections: dict[str, dict[str, Any]] = {}
    for question_id, row in sorted(sample.items()):
        asset_ids = row["assetIds"]
        official = answer_key[question_id]
        correct_models = [
            model for model in MODELS
            if predictions[model][question_id].get("answer") == official
        ]
        sufficient_models = [
            model for model in correct_models
            if predictions[model][question_id].get("informationSufficient") is True
            and predictions[model][question_id].get("provider") != SHORT_ANSWER_PROVIDER
        ]
        correct_support = len(correct_models)
        sufficient_support = len(sufficient_models)

        # JSON-primary admission allows either replicated correctness plus an
        # explicit sufficiency signal, or correctness replicated by 3+ runs.
        replicated_answer_gate = (
            (correct_support >= 2 and sufficient_support >= 1)
            or correct_support >= 3
        )
        if sufficient_models:
            candidate_models = sufficient_models
        elif correct_models:
            candidate_models = correct_models
        else:
            candidate_models = list(MODELS)
        selected_model = max(candidate_models, key=lambda model: model_score(model, asset_ids))
        selected_all_structured = all(
            submissions[selected_model][asset_id]["extraction"].get("qualityStatus") == "structured_candidate"
            for asset_id in asset_ids
        )
        selected_all_valid = all(schema_valid[selected_model][asset_id] for asset_id in asset_ids)
        single_sufficient_consensus_gate = (
            correct_support == 1
            and sufficient_support == 1
            and all(
                cross_evidence[asset_id]["layoutSupport"] >= 3
                and cross_evidence[asset_id]["peerSupport"][selected_model] >= 3
                and cross_evidence[asset_id]["averageSimilarity"][selected_model]
                >= args.single_sufficient_average_threshold
                for asset_id in asset_ids
            )
        )
        json_primary = (
            (replicated_answer_gate or single_sufficient_consensus_gate)
            and selected_all_structured and selected_all_valid
        )
        if not json_primary:
            # For image-assisted use, a wrong answer from one candidate is not
            # a reason to retain an invalid or repair-pending JSON.  Re-select
            # the strongest schema-valid structured scene from all four while
            # preserving the same submission for every asset in the question.
            structured_valid_models = [
                model for model in MODELS
                if all(
                    schema_valid[model][asset_id]
                    and submissions[model][asset_id]["extraction"].get("qualityStatus")
                    == "structured_candidate"
                    for asset_id in asset_ids
                )
            ]
            valid_models = [
                model for model in MODELS
                if all(schema_valid[model][asset_id] for asset_id in asset_ids)
            ]
            selected_model = max(
                structured_valid_models or valid_models or list(MODELS),
                key=lambda model: model_score(model, asset_ids),
            )
        use_mode = "semantic_json_primary" if json_primary else "original_image_plus_semantic_json"

        question_rows.append({
            "questionId": question_id,
            "visualGrammar": row["visualGrammar"],
            "assetIds": asset_ids,
            "selectedSubmission": selected_model,
            "useMode": use_mode,
            "correctSupport": correct_support,
            "longReasoningSufficientSupport": sufficient_support,
            "singleSufficientConsensusGate": single_sufficient_consensus_gate,
            "duplicateQuestionCount": row["duplicateQuestionCount"],
        })
        for asset_id in asset_ids:
            selections[asset_id] = {
                "selectedSubmission": selected_model,
                "useMode": use_mode,
                "questionId": question_id,
                "visualGrammar": row["visualGrammar"],
                "correctSupport": correct_support,
                "longReasoningSufficientSupport": sufficient_support,
                "singleSufficientConsensusGate": single_sufficient_consensus_gate,
            }

    # The 13 non-question image assets still receive the strongest consensus
    # candidate, but keep the original image in the required downstream input.
    for asset_id in sorted(all_asset_ids - set(selections)):
        structured_valid_models = [
            model for model in MODELS
            if schema_valid[model][asset_id]
            and submissions[model][asset_id]["extraction"].get("qualityStatus")
            == "structured_candidate"
        ]
        valid_models = [model for model in MODELS if schema_valid[model][asset_id]]
        selected_model = max(
            structured_valid_models or valid_models or list(MODELS),
            key=lambda model: model_score(model, [asset_id]),
        )
        selections[asset_id] = {
            "selectedSubmission": selected_model,
            "useMode": "original_image_plus_semantic_json",
            "questionId": None,
            "visualGrammar": submissions[selected_model][asset_id]["visualGrammar"].get("primary"),
            "correctSupport": None,
            "longReasoningSufficientSupport": None,
            "singleSufficientConsensusGate": False,
        }

    assets_dir = args.output / "assets"
    images_dir = args.output / "images"
    assets_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    for stale in assets_dir.glob("gr_*.json"):
        stale.unlink()

    manifest_rows = []
    error_rows = []
    for asset_id in sorted(all_asset_ids):
        selection = selections[asset_id]
        model = selection["selectedSubmission"]
        scene = submissions[model][asset_id]
        output_path = assets_dir / f"{asset_id}.json"
        write_json(output_path, scene)

        source_relative = Path(scene["source"]["imagePath"])
        source_path = args.public_root / source_relative
        portable_image_path = images_dir / source_path.name
        if not portable_image_path.is_file() or portable_image_path.stat().st_size != source_path.stat().st_size:
            shutil.copy2(source_path, portable_image_path)
        errors = []
        if not source_path.is_file():
            errors.append("source_image_missing")
        elif sha256_file(source_path) != scene["source"]["sha256"]:
            errors.append("source_sha256_mismatch")
        if not schema_valid[model][asset_id]:
            errors.append("schema_invalid")
        if contains_answer_key(scene):
            errors.append("answer_field_present")
        if scene.get("layout", {}).get("panelCount") != len(scene.get("panels") or []):
            errors.append("panel_count_mismatch")
        for panel in scene.get("panels") or []:
            for element in (panel.get("visualFacts") or {}).get("elements") or []:
                if element.get("count") != len(element.get("instances") or []):
                    errors.append("element_count_mismatch")
                    break
        for code in sorted(set(errors)):
            error_rows.append({"assetId": asset_id, "code": code})

        evidence = cross_evidence[asset_id]
        manifest_rows.append({
            "assetId": asset_id,
            "selectedSubmission": model,
            "useMode": selection["useMode"],
            "semanticJsonPath": f"assets/{asset_id}.json",
            "semanticJsonSha256": digest_json(scene),
            "sourceImagePath": f"images/{portable_image_path.name}",
            "sourceSha256": scene["source"]["sha256"],
            "schemaStatus": "valid" if schema_valid[model][asset_id] else "invalid",
            "qualityStatus": scene["extraction"].get("qualityStatus"),
            "questionId": selection["questionId"],
            "visualGrammar": selection["visualGrammar"],
            "correctSupport": selection["correctSupport"],
            "longReasoningSufficientSupport": selection["longReasoningSufficientSupport"],
            "singleSufficientConsensusGate": selection["singleSufficientConsensusGate"],
            "layoutSupport": evidence["layoutSupport"],
            "semanticPeerSupport": evidence["peerSupport"][model],
            "semanticSimilarity": round(evidence["averageSimilarity"][model], 4),
        })

    use_counts = Counter(row["useMode"] for row in manifest_rows)
    selected_counts = Counter(row["selectedSubmission"] for row in manifest_rows)
    grammar_use_counts: dict[str, Counter] = defaultdict(Counter)
    for row in manifest_rows:
        grammar_use_counts[str(row["visualGrammar"])][row["useMode"]] += 1
    summary = {
        "schemaVersion": "graphic-reasoning-unified-selection-v1",
        "assetCount": len(manifest_rows),
        "questionCount": len(question_rows),
        "benchmarkedAssetCount": len(asset_questions),
        "unbenchmarkedAssetCount": len(all_asset_ids - set(asset_questions)),
        "useModeCounts": dict(sorted(use_counts.items())),
        "selectedSubmissionCounts": dict(sorted(selected_counts.items())),
        "useModeByVisualGrammar": {
            grammar: dict(sorted(counts.items()))
            for grammar, counts in sorted(grammar_use_counts.items())
        },
        "schemaValidCount": sum(row["schemaStatus"] == "valid" for row in manifest_rows),
        "structuredCandidateCount": sum(row["qualityStatus"] == "structured_candidate" for row in manifest_rows),
        "repairPendingCount": sum(row["qualityStatus"] == "repair_pending" for row in manifest_rows),
        "errorCount": len(error_rows),
        "selectionPolicy": {
            "sameSubmissionForAllAssetsInQuestion": True,
            "semanticJsonPrimary": (
                "correctSupport>=2 and longReasoningSufficientSupport>=1, or correctSupport>=3; "
                "or one correct+sufficient result with 3-way semantic support and average similarity gate; "
                "selected assets must be schema-valid structured_candidate"
            ),
            "fallback": "original_image_plus_semantic_json",
            "semanticPeerThreshold": args.semantic_threshold,
            "singleSufficientAverageSimilarityThreshold": args.single_sufficient_average_threshold,
        },
        "answerIsolation": "official answers were used only for selection; no answer is emitted",
    }

    write_jsonl(args.output / "manifest.jsonl", manifest_rows)
    write_jsonl(args.output / "questions.jsonl", question_rows)
    write_jsonl(args.output / "errors.jsonl", error_rows)
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not error_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
