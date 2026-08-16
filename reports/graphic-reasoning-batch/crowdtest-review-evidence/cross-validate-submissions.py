#!/usr/bin/env python3
"""Cross-check four semantic-JSON submissions without exposing answer keys.

The script reads the downloaded crowd-test ZIPs and the completed solver
benchmark.  It emits an asset-level admission inventory.  Official answers
are used only as a local gate and are never written to the output.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


MODELS = ("summit", "rudder", "pelican", "quarry")
SHORT_ANSWER_PROVIDER = "OpenCode Go short-answer"
DEFAULT_HERE = Path(__file__).resolve().parent
DEFAULT_RUN_DIR = DEFAULT_HERE / "deepseek-benchmark/runs/full-coverage-v1"
DEFAULT_PRIVATE_KEY = (
    DEFAULT_HERE.parent
    / "crowdtest-package/build/graphic-reasoning-semantic-private-evaluation-v1/question-key-private.jsonl"
)
DEFAULT_OUTPUT = DEFAULT_HERE / "cross-validation-v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def canonical_kind(element: dict[str, Any]) -> str:
    kind = canonical(element.get("kind"))
    attributes = element.get("attributes") or {}
    words = " ".join(
        canonical(value)
        for value in (
            element.get("label"), attributes.get("shape"),
            attributes.get("shapeClass"), attributes.get("type"),
        )
    )
    sides = attributes.get("sideCount") or attributes.get("sides")
    if kind == "polygon" and (sides == 3 or "triangle" in words):
        return "triangle"
    if kind == "rect" or (
        kind == "polygon" and sides == 4 and ("square" in words or "rect" in words)
    ):
        return "rect"
    return kind


def canonical_role(panel: dict[str, Any]) -> str:
    role = panel.get("role")
    if role == "option":
        return f"option:{panel.get('optionLabel')}"
    if role == "missing":
        return "missing"
    # stem / sequence / sample describe prompt-side panels with inconsistent
    # naming among the four submissions.  They are semantically equivalent
    # for panel alignment.
    return "prompt"


def multiset_similarity(left: Iterable[Any], right: Iterable[Any]) -> float | None:
    a, b = Counter(left), Counter(right)
    if not a and not b:
        return None
    return sum((a & b).values()) / sum((a | b).values())


def panel_features(panel: dict[str, Any]) -> tuple[Any, ...]:
    facts = panel.get("visualFacts") or {}
    kinds: list[str] = []
    instances: list[tuple[str, ...]] = []
    texts: list[str] = []
    for element in facts.get("elements") or []:
        kind = canonical_kind(element)
        count = max(1, int(element.get("count") or 1))
        kinds.extend([kind] * count)
        if element.get("textContent") not in (None, ""):
            texts.append(canonical(element["textContent"]))
        for instance in element.get("instances") or []:
            instances.append((
                kind,
                canonical(instance.get("relativeRegion")),
                canonical(instance.get("orientation")),
                canonical(instance.get("lineCharacter")),
                canonical(instance.get("fillState")),
                canonical(instance.get("texture")),
            ))
    counts = {
        key: value
        for key, value in (facts.get("counts") or {}).items()
        if isinstance(value, (int, float))
    }
    relations = [canonical(row.get("type")) for row in facts.get("relations") or []]
    return canonical_role(panel), kinds, instances, texts, counts, relations


def panel_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_role, left_kinds, left_instances, left_texts, left_counts, left_relations = panel_features(left)
    right_role, right_kinds, right_instances, right_texts, right_counts, right_relations = panel_features(right)
    parts: list[tuple[float, float]] = [(0.10, float(left_role == right_role))]
    for weight, a, b in (
        (0.30, left_kinds, right_kinds),
        (0.25, left_instances, right_instances),
        (0.05, left_texts, right_texts),
        (0.10, left_relations, right_relations),
    ):
        score = multiset_similarity(a, b)
        if score is not None:
            parts.append((weight, score))
    count_keys = set(left_counts) | set(right_counts)
    if count_keys:
        count_scores = []
        for key in count_keys:
            if key not in left_counts or key not in right_counts:
                count_scores.append(0.0)
                continue
            a, b = left_counts[key], right_counts[key]
            count_scores.append(
                1.0 if a == b else max(0.0, 1.0 - abs(a - b) / max(1, abs(a), abs(b)))
            )
        parts.append((0.20, sum(count_scores) / len(count_scores)))
    return sum(weight * score for weight, score in parts) / sum(weight for weight, _ in parts)


def layout_signature(scene: dict[str, Any]) -> tuple[Any, Any]:
    layout = scene["layout"]
    return layout.get("panelCount"), layout.get("optionCount")


def semantic_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    if layout_signature(left) != layout_signature(right):
        return 0.0
    left_panels, right_panels = left["panels"], right["panels"]
    if len(left_panels) != len(right_panels):
        return 0.0
    if not left_panels:
        return 1.0
    return sum(
        panel_similarity(a, b) for a, b in zip(left_panels, right_panels)
    ) / len(left_panels)


def locate_zip(downloads_root: Path, model: str) -> Path:
    paths = sorted((downloads_root / model).glob("*.zip"))
    if len(paths) != 1:
        raise RuntimeError(f"expected exactly one ZIP for {model}, found {len(paths)}")
    return paths[0]


def load_submission(downloads_root: Path, model: str) -> dict[str, dict[str, Any]]:
    result = {}
    with zipfile.ZipFile(locate_zip(downloads_root, model)) as archive:
        for member in archive.namelist():
            if "/output/assets/" not in member or not member.endswith(".json"):
                continue
            scene = json.loads(archive.read(member))
            result[scene["assetId"]] = scene
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--private-key", type=Path, default=DEFAULT_PRIVATE_KEY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--semantic-threshold", type=float, default=0.70)
    args = parser.parse_args()

    sample = {row["questionId"]: row for row in read_jsonl(args.run_dir / "full-sample.jsonl")}
    answer_key = {row["questionId"]: row["officialAnswer"] for row in read_jsonl(args.private_key)}
    predictions = {
        model: {
            row["questionId"]: row
            for row in read_jsonl(args.run_dir / model / "predictions.jsonl")
        }
        for model in MODELS
    }
    submissions = {model: load_submission(args.downloads_root, model) for model in MODELS}
    all_asset_ids = set.intersection(*(set(submissions[model]) for model in MODELS))

    asset_questions: dict[str, set[str]] = defaultdict(set)
    for question_id, row in sample.items():
        for asset_id in row["assetIds"]:
            asset_questions[asset_id].add(question_id)

    # Tie-breaking is derived from the completed long-reasoning benchmark,
    # never from a hard-coded model preference.
    model_gate_rates = {}
    for model in MODELS:
        eligible_rows = [
            row for question_id, row in predictions[model].items()
            if question_id in answer_key and row.get("provider") != SHORT_ANSWER_PROVIDER
        ]
        passed = sum(
            row.get("answer") == answer_key[row["questionId"]]
            and row.get("informationSufficient") is True
            for row in eligible_rows
        )
        model_gate_rates[model] = passed / len(eligible_rows) if eligible_rows else 0.0

    rows = []
    for asset_id in sorted(all_asset_ids):
        question_ids = sorted(asset_questions.get(asset_id, set()))
        scenes = {model: submissions[model][asset_id] for model in MODELS}
        modal_layout, layout_support = Counter(
            layout_signature(scene) for scene in scenes.values()
        ).most_common(1)[0]
        pair_scores = {
            (left, right): semantic_similarity(scenes[left], scenes[right])
            for index, left in enumerate(MODELS)
            for right in MODELS[index + 1:]
        }
        peer_support = {
            model: sum(
                pair_scores.get((model, other), pair_scores.get((other, model), 0.0))
                >= args.semantic_threshold
                for other in MODELS if other != model
            )
            for model in MODELS
        }
        average_similarity = {
            model: sum(
                pair_scores.get((model, other), pair_scores.get((other, model), 0.0))
                for other in MODELS if other != model
            ) / 3
            for model in MODELS
        }

        correct_by_model = {}
        long_sufficient_by_model = {}
        for model in MODELS:
            correct_by_model[model] = bool(question_ids) and all(
                question_id in predictions[model]
                and predictions[model][question_id].get("answer") == answer_key[question_id]
                for question_id in question_ids
            )
            long_sufficient_by_model[model] = correct_by_model[model] and all(
                predictions[model][question_id].get("informationSufficient") is True
                and predictions[model][question_id].get("provider") != SHORT_ANSWER_PROVIDER
                for question_id in question_ids
            )

        semantic_candidates = [
            model for model in MODELS
            if scenes[model]["extraction"].get("qualityStatus") == "structured_candidate"
            and layout_signature(scenes[model]) == modal_layout
            and peer_support[model] >= 2
        ]
        selectable = [model for model in semantic_candidates if long_sufficient_by_model[model]]
        selected_model = max(
            selectable,
            key=lambda model: (
                peer_support[model], average_similarity[model], model_gate_rates[model], model,
            ),
            default=None,
        )
        correct_support = sum(correct_by_model.values())
        long_sufficient_support = sum(long_sufficient_by_model.values())

        if not question_ids:
            tier = "unbenchmarked"
        elif (
            layout_support >= 3 and selected_model is not None
            and correct_support >= 3 and long_sufficient_support >= 2
        ):
            tier = "direct_use"
        elif layout_support >= 3 and selected_model is not None and correct_support >= 2:
            tier = "strong_candidate"
        else:
            tier = "review_required"

        grammar = sample[question_ids[0]]["visualGrammar"] if question_ids else None
        rows.append({
            "assetId": asset_id,
            "tier": tier,
            "selectedSubmission": selected_model,
            "visualGrammar": grammar,
            "questionCount": len(question_ids),
            "layoutSupport": layout_support,
            "semanticSupport": peer_support.get(selected_model, 0) + 1 if selected_model else 0,
            "semanticSimilarity": round(average_similarity[selected_model], 4) if selected_model else None,
            "correctSupport": correct_support,
            "longReasoningSufficientSupport": long_sufficient_support,
            "qualityStatus": (
                scenes[selected_model]["extraction"].get("qualityStatus") if selected_model else None
            ),
        })

    tier_counts = Counter(row["tier"] for row in rows)
    direct_rows = [row for row in rows if row["tier"] == "direct_use"]
    summary = {
        "schemaVersion": "graphic-reasoning-cross-validation-v1",
        "assetCount": len(rows),
        "benchmarkedAssetCount": len(asset_questions),
        "semanticThreshold": args.semantic_threshold,
        "tierCounts": dict(sorted(tier_counts.items())),
        "directUseRateOfAllAssets": len(direct_rows) / len(rows) if rows else 0.0,
        "directUseBySubmission": dict(sorted(Counter(
            row["selectedSubmission"] for row in direct_rows
        ).items())),
        "directUseByVisualGrammar": dict(sorted(Counter(
            row["visualGrammar"] for row in direct_rows
        ).items())),
        "modelLongReasoningCorrectAndSufficientRates": model_gate_rates,
        "admissionRule": {
            "layoutSupportAtLeast": 3,
            "semanticSupportAtLeast": 3,
            "semanticSimilarityThreshold": args.semantic_threshold,
            "correctSupportAtLeast": 3,
            "longReasoningCorrectAndSufficientSupportAtLeast": 2,
            "selectedQualityStatus": "structured_candidate",
            "selectedSubmissionMustPassLongReasoningGate": True,
        },
        "answerIsolation": "official answers were used only for local gating and are absent from emitted rows",
    }

    args.output.mkdir(parents=True, exist_ok=True)
    selected_assets_dir = args.output / "assets"
    selected_assets_dir.mkdir(parents=True, exist_ok=True)
    selected_asset_ids = {row["assetId"] for row in direct_rows}
    for stale_path in selected_assets_dir.glob("*.json"):
        if stale_path.stem not in selected_asset_ids:
            stale_path.unlink()
    for row in direct_rows:
        asset_id = row["assetId"]
        selected_model = row["selectedSubmission"]
        write_json(selected_assets_dir / f"{asset_id}.json", submissions[selected_model][asset_id])
    write_json(args.output / "summary.json", summary)
    write_jsonl(args.output / "inventory.jsonl", rows)
    write_jsonl(args.output / "direct-use.jsonl", direct_rows)
    write_jsonl(
        args.output / "strong-candidates.jsonl",
        (row for row in rows if row["tier"] == "strong_candidate"),
    )
    write_jsonl(
        args.output / "review-required.jsonl",
        (row for row in rows if row["tier"] in {"review_required", "unbenchmarked"}),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
