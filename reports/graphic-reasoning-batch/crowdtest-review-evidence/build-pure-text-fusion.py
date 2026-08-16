#!/usr/bin/env python3
"""Build answer-free semantic evidence for a text-only reasoning model.

The four crowd-test scenes are treated as independent observations.  One
schema-valid scene remains the complete base scene, while a compact sidecar
records facts supported by multiple submissions and explicit disagreements.
No official answer or solution is emitted.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import html
import importlib.util
import json
import re
import statistics
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
DEFAULT_DOWNLOADS = Path("/Users/zxg/Desktop/未命名文件夹")
DEFAULT_RUN_DIR = HERE / "deepseek-benchmark/runs/full-coverage-v1"
DEFAULT_UNIFIED = HERE / "unified-semantic-assets-v1"
DEFAULT_PUBLIC = HERE.parent / "crowdtest-package/build/graphic-reasoning-semantic-public-v1"
DEFAULT_PRIVATE = HERE.parent / "crowdtest-package/build/graphic-reasoning-semantic-private-evaluation-v1"
DEFAULT_QUESTION_SOURCE = (
    HERE.parents[2]
    / "data/question-bank-export/files/source-question-bank/fenbi-final-analysis-v1-20260613/paper_questions.jsonl"
)
DEFAULT_OUTPUT = HERE / "pure-text-semantic-fusion-v1"
ANSWER_KEYS = {
    "answer", "officialAnswer", "correctAnswer", "answerKey",
    "finalAnswer", "selectedOption",
}
COUNT_FIELDS = (
    "straightSegmentCount", "curveCount", "circleCount", "closedShapeCount",
    "dotCount", "filledRegionCount", "symbolCount", "textCount",
)
TOPOLOGY_FIELDS = ("componentCount", "endpointCount", "junctionCount", "loopCount")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(path)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")


def contains_answer_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in ANSWER_KEYS or contains_answer_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_answer_key(child) for child in value)
    return False


def visible_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<img\b[^>]*>", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|tr|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    return text.strip()


@functools.lru_cache(maxsize=1)
def load_question_contexts(path: Path) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            qid = row.get("id")
            if not qid:
                continue
            options = []
            for index, option in enumerate(row.get("options") or []):
                options.append({
                    "label": option.get("label") or chr(65 + index),
                    "text": visible_text(option.get("text")) or visible_text(option.get("html")),
                })
            contexts[qid] = {
                "stemText": visible_text(row.get("stem_text")) or visible_text(row.get("stem_html")),
                "materialText": visible_text(row.get("material_text")) or visible_text(row.get("material_html")),
                "options": options,
            }
    return contexts


def modal(values: list[Any], *, prefer: Any = None) -> tuple[Any, dict[str, int]]:
    encoded = [stable_json(value) for value in values]
    counts = Counter(encoded)
    best_count = max(counts.values())
    tied = {key for key, count in counts.items() if count == best_count}
    preferred = stable_json(prefer)
    selected = preferred if preferred in tied else min(tied)
    return json.loads(selected), {key: counts[key] for key in sorted(counts)}


def compact_distribution(values_by_model: dict[str, Any], *, prefer: Any = None) -> dict[str, Any]:
    non_null = {model: value for model, value in values_by_model.items() if value is not None}
    if not non_null:
        return {"value": None, "support": 0, "observedBy": 0}
    chosen, encoded_counts = modal(list(non_null.values()), prefer=prefer)
    chosen_key = stable_json(chosen)
    result = {
        "value": chosen,
        "support": encoded_counts[chosen_key],
        "observedBy": len(non_null),
    }
    if len(encoded_counts) > 1:
        result["alternatives"] = [
            {"value": json.loads(key), "support": count}
            for key, count in sorted(encoded_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    return result


def panel_slots(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    slots: dict[str, dict[str, Any]] = {}
    prompt_index = 0
    missing_index = 0
    option_index = 0
    for panel in sorted(scene.get("panels") or [], key=lambda value: value.get("position", 999)):
        role = panel.get("role")
        if role == "option" and panel.get("optionLabel"):
            key = f"option:{panel['optionLabel']}"
        elif role == "option":
            option_index += 1
            key = f"option_index:{option_index}"
        elif role == "missing":
            missing_index += 1
            key = f"missing:{missing_index}"
        else:
            prompt_index += 1
            key = f"prompt:{prompt_index}"
        slots[key] = panel
    return slots


def align_non_option_panels(
    base_panels: list[dict[str, Any]], other_panels: list[dict[str, Any]],
) -> list[tuple[int | None, int | None]]:
    """Order-preserving semantic alignment with explicit gaps."""
    left_size, right_size = len(base_panels), len(other_panels)
    gap = -0.34
    scores = [[float("-inf")] * (right_size + 1) for _ in range(left_size + 1)]
    back: list[list[tuple[int, int, str] | None]] = [
        [None] * (right_size + 1) for _ in range(left_size + 1)
    ]
    scores[0][0] = 0.0
    for left in range(left_size + 1):
        for right in range(right_size + 1):
            current = scores[left][right]
            if current == float("-inf"):
                continue
            if left < left_size and current + gap > scores[left + 1][right]:
                scores[left + 1][right] = current + gap
                back[left + 1][right] = (left, right, "left_gap")
            if right < right_size and current + gap > scores[left][right + 1]:
                scores[left][right + 1] = current + gap
                back[left][right + 1] = (left, right, "right_gap")
            if left < left_size and right < right_size:
                a, b = base_panels[left], other_panels[right]
                similarity = CROSS.panel_similarity(a, b)
                if a.get("role") == "missing" and b.get("role") == "missing":
                    similarity += 0.45
                elif (a.get("role") == "missing") != (b.get("role") == "missing"):
                    similarity -= 0.55
                candidate = current + similarity
                if candidate > scores[left + 1][right + 1]:
                    scores[left + 1][right + 1] = candidate
                    back[left + 1][right + 1] = (left, right, "match")
    result: list[tuple[int | None, int | None]] = []
    left, right = left_size, right_size
    while left or right:
        old_left, old_right, action = back[left][right] or (0, 0, "")
        if not action:
            raise RuntimeError("panel alignment backtrace failed")
        if action == "match":
            result.append((left - 1, right - 1))
        elif action == "left_gap":
            result.append((left - 1, None))
        else:
            result.append((None, right - 1))
        left, right = old_left, old_right
    return list(reversed(result))


def aligned_panel_slots(
    scenes: dict[str, dict[str, Any]], base_model: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    base_ordered = sorted(scenes[base_model].get("panels") or [], key=lambda panel: panel.get("position", 999))
    base_non_options = [panel for panel in base_ordered if panel.get("role") != "option"]
    base_options = [panel for panel in base_ordered if panel.get("role") == "option"]
    base_non_option_keys = [f"prompt:{index}" for index in range(1, len(base_non_options) + 1)]
    result: dict[str, dict[str, dict[str, Any]]] = {
        key: {base_model: panel} for key, panel in zip(base_non_option_keys, base_non_options)
    }
    for index, panel in enumerate(base_options, 1):
        key = f"option:{panel.get('optionLabel')}" if panel.get("optionLabel") else f"option_index:{index}"
        result[key] = {base_model: panel}

    for model, scene in scenes.items():
        if model == base_model:
            continue
        ordered = sorted(scene.get("panels") or [], key=lambda panel: panel.get("position", 999))
        non_options = [panel for panel in ordered if panel.get("role") != "option"]
        options = [panel for panel in ordered if panel.get("role") == "option"]
        extra = 0
        for base_index, other_index in align_non_option_panels(base_non_options, non_options):
            if base_index is not None and other_index is not None:
                result[base_non_option_keys[base_index]][model] = non_options[other_index]
            elif other_index is not None:
                extra += 1
                result[f"extra:{model}:{extra}"] = {model: non_options[other_index]}
        for index, panel in enumerate(options, 1):
            key = f"option:{panel.get('optionLabel')}" if panel.get("optionLabel") else f"option_index:{index}"
            if key in result:
                result[key][model] = panel
            else:
                result.setdefault(f"extra:{model}:option:{index}", {})[model] = panel
    return result


def slot_sort_key(key: str, panels: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    positions = [panel.get("position", 999) for panel in panels.values()]
    prefix, _, tail = key.partition(":")
    rank = {"prompt": 0, "missing": 1, "option": 2, "option_index": 3}.get(prefix, 4)
    return statistics.median(positions), rank, tail


def canonical_kind(element: dict[str, Any]) -> str:
    kind = CROSS.canonical_kind(element)
    label = canonical(element.get("label"))
    if kind == "circle" and any(token in label for token in ("dot", "point", "圆点", "黑点")):
        return "dot"
    return kind


def instance_cluster(element: dict[str, Any], instance: dict[str, Any]) -> tuple[str, ...]:
    text = canonical(element.get("textContent"))
    return (
        canonical_kind(element), text,
        canonical(instance.get("relativeRegion")),
        canonical(instance.get("fillState")),
        canonical(instance.get("texture")),
    )


def element_instance_assertions(panels: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    per_model: dict[str, Counter] = {}
    labels: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    confidences: dict[tuple[str, ...], list[float]] = defaultdict(list)
    orientations: dict[tuple[str, ...], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    line_characters: dict[tuple[str, ...], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for model, panel in panels.items():
        counts: Counter = Counter()
        for element in (panel.get("visualFacts") or {}).get("elements") or []:
            for instance in element.get("instances") or []:
                signature = instance_cluster(element, instance)
                counts[signature] += 1
                orientations[signature][model].append(canonical(instance.get("orientation")))
                line_characters[signature][model].append(canonical(instance.get("lineCharacter")))
                if element.get("label"):
                    labels[signature][str(element["label"])] += 1
                if isinstance(element.get("confidence"), (int, float)):
                    confidences[signature].append(float(element["confidence"]))
        per_model[model] = counts

    consensus = []
    conflicts = []
    signatures = sorted(set().union(*(set(counter) for counter in per_model.values())))
    for signature in signatures:
        positive_counts = Counter(
            counter.get(signature, 0)
            for counter in per_model.values()
            if counter.get(signature, 0) > 0
        )
        positive_support = sum(positive_counts.values())
        selected_count, count_support = sorted(
            positive_counts.items(), key=lambda item: (-item[1], -item[0])
        )[0]
        kind, text, region, fill_state, texture = signature
        orientation_votes = {
            model: Counter(values).most_common(1)[0][0]
            for model, values in orientations[signature].items() if values
        }
        line_votes = {
            model: Counter(values).most_common(1)[0][0]
            for model, values in line_characters[signature].items() if values
        }
        row = {
            "kind": kind,
            "textContent": text or None,
            "count": selected_count,
            "relativeRegion": region,
            "orientation": compact_distribution(orientation_votes),
            "lineCharacter": compact_distribution(line_votes),
            "fillState": fill_state,
            "texture": texture,
            "modelSupport": positive_support,
            "countSupport": count_support,
            "averageConfidence": round(sum(confidences[signature]) / len(confidences[signature]), 3)
            if confidences[signature] else None,
        }
        if labels[signature]:
            row["labels"] = [label for label, _ in labels[signature].most_common(3)]
        if positive_support >= 2 and selected_count > 0:
            consensus.append(row)
        else:
            conflicts.append({
                **row,
                "countBySubmission": {
                    model: per_model.get(model, Counter()).get(signature, 0)
                    for model in MODELS
                },
            })
    return consensus, conflicts


def relation_signature(panel: dict[str, Any], relation: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    subject_lookup = {}
    for element in (panel.get("visualFacts") or {}).get("elements") or []:
        subject_lookup[str(element.get("id"))] = canonical_kind(element)
    subjects = tuple(sorted(subject_lookup.get(str(subject), "unknown") for subject in relation.get("subjects") or []))
    return canonical(relation.get("type")), subjects


def relation_assertions(panels: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_signature: dict[tuple[str, tuple[str, ...]], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for model, panel in panels.items():
        for relation in (panel.get("visualFacts") or {}).get("relations") or []:
            by_signature[relation_signature(panel, relation)][model].append(relation)
    consensus, conflicts = [], []
    for (relation_type, subjects), values in sorted(by_signature.items()):
        descriptions = []
        confidences = []
        for rows in values.values():
            for row in rows:
                if row.get("description") and row["description"] not in descriptions:
                    descriptions.append(row["description"])
                if isinstance(row.get("confidence"), (int, float)):
                    confidences.append(float(row["confidence"]))
        item = {
            "type": relation_type,
            "subjectKinds": list(subjects),
            "modelSupport": len(values),
            "descriptions": descriptions[:4],
            "averageConfidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        }
        (consensus if len(values) >= 2 else conflicts).append(item)
    return consensus, conflicts


def compact_panel(slot: str, panels: dict[str, dict[str, Any]], base_panel: dict[str, Any] | None) -> dict[str, Any]:
    instance_consensus, instance_conflicts = element_instance_assertions(panels)
    relation_consensus, relation_conflicts = relation_assertions(panels)
    facts_by_model = {model: panel.get("visualFacts") or {} for model, panel in panels.items()}
    roles = compact_distribution(
        {model: panel.get("role") for model, panel in panels.items()},
        prefer=(base_panel or {}).get("role"),
    )
    frames = compact_distribution(
        {model: panel.get("frame") for model, panel in panels.items()},
        prefer=(base_panel or {}).get("frame"),
    )
    counts = {
        field: compact_distribution(
            {model: (facts.get("counts") or {}).get(field) for model, facts in facts_by_model.items()},
            prefer=((base_panel or {}).get("visualFacts") or {}).get("counts", {}).get(field),
        )
        for field in COUNT_FIELDS
    }
    topology = {
        field: compact_distribution(
            {model: (facts.get("topology") or {}).get(field) for model, facts in facts_by_model.items()},
            prefer=((base_panel or {}).get("visualFacts") or {}).get("topology", {}).get(field),
        )
        for field in TOPOLOGY_FIELDS
    }
    observations = []
    for model in MODELS:
        panel = panels.get(model)
        if not panel:
            continue
        text_rows = list((panel.get("visualFacts") or {}).get("observations") or [])
        text_rows += list(panel.get("uncertainties") or [])
        for text in text_rows:
            value = str(text).strip()
            if value and value not in observations:
                observations.append(value)
    return {
        "slot": slot,
        "submissionCoverage": len(panels),
        "position": compact_distribution(
            {model: panel.get("position") for model, panel in panels.items()},
            prefer=(base_panel or {}).get("position"),
        ),
        "role": roles,
        "optionLabel": (base_panel or {}).get("optionLabel") or next(
            (panel.get("optionLabel") for panel in panels.values() if panel.get("optionLabel")), None
        ),
        "frame": frames,
        "consensusInstances": instance_consensus,
        "singleSubmissionInstances": instance_conflicts,
        "consensusRelations": relation_consensus,
        "singleSubmissionRelations": relation_conflicts,
        "counts": counts,
        "topology": topology,
        "modelObservations": observations[:12],
    }


def compact_reasoning(scenes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for model, scene in scenes.items():
        for candidate in scene.get("reasoningCandidates") or []:
            grouped[canonical(candidate.get("type"))][model].append(candidate)
    result = []
    for kind, by_model in sorted(grouped.items()):
        descriptions = []
        confidences = []
        statuses = Counter()
        for rows in by_model.values():
            for row in rows:
                if row.get("description") and row["description"] not in descriptions:
                    descriptions.append(row["description"])
                if isinstance(row.get("confidence"), (int, float)):
                    confidences.append(float(row["confidence"]))
                statuses[str(row.get("status"))] += 1
        result.append({
            "type": kind,
            "modelSupport": len(by_model),
            "statusVotes": dict(sorted(statuses.items())),
            "averageConfidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
            "descriptions": descriptions[:6],
        })
    return result


def build_fusion(asset_id: str, scenes: dict[str, dict[str, Any]], base_model: str) -> dict[str, Any]:
    base_scene = scenes[base_model]
    aligned_slots = aligned_panel_slots(scenes, base_model)
    compact_panels = []
    for slot in sorted(
        aligned_slots,
        key=lambda key: slot_sort_key(key, aligned_slots[key]),
    ):
        panels = aligned_slots[slot]
        compact_panels.append(compact_panel(slot, panels, panels.get(base_model)))

    layout = {
        field: compact_distribution(
            {model: scene.get("layout", {}).get(field) for model, scene in scenes.items()},
            prefer=base_scene.get("layout", {}).get(field),
        )
        for field in ("layoutType", "panelCount", "optionCount")
    }
    primary_grammar = compact_distribution(
        {model: scene.get("visualGrammar", {}).get("primary") for model, scene in scenes.items()},
        prefer=base_scene.get("visualGrammar", {}).get("primary"),
    )
    pair_scores = [
        CROSS.semantic_similarity(scenes[left], scenes[right])
        for index, left in enumerate(MODELS)
        for right in MODELS[index + 1:]
    ]
    return {
        "schemaVersion": "graphic-reasoning-pure-text-asset-v1",
        "assetId": asset_id,
        "source": base_scene["source"],
        "baseSubmission": base_model,
        "baseScene": base_scene,
        "consensusEvidence": {
            "submissionCount": len(scenes),
            "layout": layout,
            "primaryVisualGrammar": primary_grammar,
            "panels": compact_panels,
            "reasoningCandidates": compact_reasoning(scenes),
            "meanPairwiseSemanticSimilarity": round(sum(pair_scores) / len(pair_scores), 4),
            "interpretationRule": (
                "modelSupport>=2 is corroborated; single-submission facts are alternatives, not established truth"
            ),
        },
        "answerIsolation": "no official answer or solution is included",
    }


def compact_selected_panel(panel: dict[str, Any] | None) -> dict[str, Any] | None:
    if panel is None:
        return None
    facts = panel.get("visualFacts") or {}
    return {
        "elements": [
            {
                "id": element.get("id"),
                "kind": element.get("kind"),
                "label": element.get("label"),
                "textContent": element.get("textContent"),
                "count": element.get("count"),
                "instances": element.get("instances") or [],
                "parts": element.get("parts") or [],
                "attributes": element.get("attributes") or {},
                "confidence": element.get("confidence"),
            }
            for element in facts.get("elements") or []
            if not (
                element.get("kind") == "textGlyph"
                and canonical(element.get("label")) in {"option_letter", "option_label"}
            )
        ],
        "relations": facts.get("relations") or [],
        "topology": facts.get("topology") or {},
        "counts": facts.get("counts") or {},
        "observations": facts.get("observations") or [],
        "uncertainties": panel.get("uncertainties") or [],
    }


def solver_view(fused: dict[str, Any]) -> dict[str, Any]:
    """Emit one compact scene instead of four competing descriptions."""
    base = fused["baseScene"]
    selected_slots = panel_slots(base)
    panels = []
    for panel in fused["consensusEvidence"]["panels"]:
        base_specific = sorted(
            (
                {key: value for key, value in row.items() if key != "countBySubmission"}
                for row in panel["singleSubmissionInstances"]
                if row.get("countBySubmission", {}).get(fused["baseSubmission"], 0) > 0
            ),
            key=lambda row: (
                -(row.get("averageConfidence") or 0),
                row.get("kind") or "",
                row.get("relativeRegion") or "",
            ),
        )[:12]
        selected_panel = next(
            (
                candidate for candidate in base.get("panels") or []
                if candidate.get("position") == panel["position"].get("value")
            ),
            selected_slots.get(panel["slot"]),
        )
        panels.append({
            "slot": panel["slot"],
            "submissionCoverage": panel["submissionCoverage"],
            "position": panel["position"]["value"],
            "role": panel["role"]["value"],
            "roleSupport": panel["role"]["support"],
            "optionLabel": panel["optionLabel"],
            "frame": panel["frame"]["value"],
            "frameSupport": panel["frame"]["support"],
            "elements": panel["consensusInstances"],
            "baseSpecificElements": base_specific,
            "relations": panel["consensusRelations"],
            "baseSpecificRelations": [
                row for row in panel["singleSubmissionRelations"]
                if row.get("modelSupport") == 1
            ][:8],
            "counts": {
                key: {
                    "value": value["value"],
                    "support": value["support"],
                    **({"alternatives": value["alternatives"]} if "alternatives" in value else {}),
                }
                for key, value in panel["counts"].items()
            },
            "topology": {
                key: {
                    "value": value["value"],
                    "support": value["support"],
                    **({"alternatives": value["alternatives"]} if "alternatives" in value else {}),
                }
                for key, value in panel["topology"].items()
            },
            "selectedObservations": ((selected_panel or {}).get("visualFacts") or {}).get("observations", [])[:3],
            "uncertainties": (selected_panel or {}).get("uncertainties", [])[:3],
        })
    return {
        "schemaVersion": "graphic-reasoning-pure-text-solver-view-v1",
        "assetId": fused["assetId"],
        "sourceSha256": fused["source"]["sha256"],
        "baseSubmission": fused["baseSubmission"],
        "layout": {
            key: {
                "value": value["value"], "support": value["support"],
                **({"alternatives": value["alternatives"]} if "alternatives" in value else {}),
            }
            for key, value in fused["consensusEvidence"]["layout"].items()
        },
        "primaryVisualGrammar": fused["consensusEvidence"]["primaryVisualGrammar"]["value"],
        "primaryVisualGrammarSupport": fused["consensusEvidence"]["primaryVisualGrammar"]["support"],
        "panels": panels,
        "reasoningCandidates": fused["consensusEvidence"]["reasoningCandidates"],
        "meanPairwiseSemanticSimilarity": fused["consensusEvidence"]["meanPairwiseSemanticSimilarity"],
        "evidenceRule": "elements/relations have modelSupport>=2; baseSpecific* are uncorroborated fallback facts",
    }


def load_unified(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    questions = {row["questionId"]: row for row in read_jsonl(path / "questions.jsonl")}
    manifest = {row["assetId"]: row for row in read_jsonl(path / "manifest.jsonl")}
    return questions, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--unified-root", type=Path, default=DEFAULT_UNIFIED)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--question-source", type=Path, default=DEFAULT_QUESTION_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    questions, unified_manifest = load_unified(args.unified_root)
    submissions = {model: CROSS.load_submission(args.downloads_root, model) for model in MODELS}
    asset_ids = sorted(set.intersection(*(set(rows) for rows in submissions.values())))
    schema = json.loads((args.public_root / "contract/semantic-scene-v2.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    question_keys = {row["questionId"]: row for row in read_jsonl(args.private_root / "question-key-private.jsonl")}
    contexts = load_question_contexts(args.question_source.resolve())

    assets_dir = args.output / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for stale in assets_dir.glob("gr_*.json"):
        stale.unlink()

    asset_rows = []
    errors = []
    fused_by_asset: dict[str, dict[str, Any]] = {}
    for asset_id in asset_ids:
        scenes = {model: submissions[model][asset_id] for model in MODELS}
        manifest = unified_manifest[asset_id]
        base_model = manifest["selectedSubmission"]
        fused = build_fusion(asset_id, scenes, base_model)
        fused_by_asset[asset_id] = fused
        path = assets_dir / f"{asset_id}.json"
        write_json(path, fused)

        asset_errors = []
        if contains_answer_key(fused):
            asset_errors.append("answer_field_present")
        if list(validator.iter_errors(fused["baseScene"])):
            asset_errors.append("base_scene_schema_invalid")
        if fused["baseScene"]["source"]["sha256"] != manifest["sourceSha256"]:
            asset_errors.append("source_sha256_mismatch")
        if not fused["consensusEvidence"]["panels"]:
            asset_errors.append("no_fused_panels")
        for code in asset_errors:
            errors.append({"assetId": asset_id, "code": code})
        asset_rows.append({
            "assetId": asset_id,
            "questionId": manifest.get("questionId"),
            "priorUseMode": manifest["useMode"],
            "baseSubmission": base_model,
            "path": f"assets/{asset_id}.json",
            "sha256": digest(fused),
            "sourceSha256": manifest["sourceSha256"],
            "panelCount": len(fused["consensusEvidence"]["panels"]),
            "meanPairwiseSemanticSimilarity": fused["consensusEvidence"]["meanPairwiseSemanticSimilarity"],
            "errorCount": len(asset_errors),
        })

    cases = []
    question_rows = []
    for question_id, question in sorted(questions.items()):
        key = question_keys[question_id]
        assets = []
        for ref in key["assets"]:
            assets.append({
                "assetId": ref["assetId"],
                "role": ref["role"],
                "field": ref["field"],
                "optionLabel": ref.get("optionLabel"),
                "semanticEvidence": solver_view(fused_by_asset[ref["assetId"]]),
            })
        prior_primary = question["useMode"] == "semantic_json_primary"
        case = {
            "questionId": question_id,
            "visualGrammar": question["visualGrammar"],
            "question": contexts[question_id],
            "assets": assets,
            "admission": {
                "status": "validated_existing" if prior_primary else "fusion_revalidation_required",
                "reason": (
                    "passed prior answer-isolated text-only gate"
                    if prior_primary else
                    "new consensus sidecar has not yet passed a text-only solver gate"
                ),
            },
        }
        if contains_answer_key(case):
            errors.append({"questionId": question_id, "code": "answer_field_present"})
        cases.append(case)
        question_rows.append({
            "questionId": question_id,
            "visualGrammar": question["visualGrammar"],
            "assetIds": question["assetIds"],
            "priorUseMode": question["useMode"],
            "fusionStatus": case["admission"]["status"],
            "caseSha256": digest(case),
        })

    status_counts = Counter(row["fusionStatus"] for row in question_rows)
    grammar_status: dict[str, Counter] = defaultdict(Counter)
    for row in question_rows:
        grammar_status[row["visualGrammar"]][row["fusionStatus"]] += 1
    summary = {
        "schemaVersion": "graphic-reasoning-pure-text-fusion-summary-v1",
        "assetCount": len(asset_rows),
        "questionCount": len(question_rows),
        "statusCounts": dict(sorted(status_counts.items())),
        "statusByVisualGrammar": {
            grammar: dict(sorted(counts.items())) for grammar, counts in sorted(grammar_status.items())
        },
        "baseSceneSchemaValidCount": sum(row["errorCount"] == 0 for row in asset_rows),
        "errorCount": len(errors),
        "answerIsolation": "official answers were not loaded into emitted cases",
        "admissionPolicy": (
            "existing validated questions remain admitted; all newly fused questions require a fresh "
            "answer-isolated text-only solver result before promotion"
        ),
    }
    write_jsonl(args.output / "manifest.jsonl", asset_rows)
    write_jsonl(args.output / "questions.jsonl", question_rows)
    write_jsonl(args.output / "cases-answer-free.jsonl", cases)
    write_jsonl(
        args.output / "pending-cases-answer-free.jsonl",
        (case for case in cases if case["admission"]["status"] == "fusion_revalidation_required"),
    )
    write_jsonl(args.output / "errors.jsonl", errors)
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
