#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets.jsonl"
DERIVED = ROOT / "derived-summary.json"
MANIFEST = ROOT / "next-wave-manifest.jsonl"

QUESTION_ROLES = {"material", "stem", "option"}
NEXT_WAVE_TRACKS = {
    "verbal",
    "judgment_non_graphic",
    "knowledge_science",
    "quantity",
    "perceptual_speed",
    "other",
}


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> None:
    by_track: dict[str, Counter[str]] = defaultdict(Counter)
    unions: dict[str, set[str]] = defaultdict(set)
    manifest: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    with ASSETS.open(encoding="utf-8") as handle:
        for raw in handle:
            asset = json.loads(raw)
            asset_id = asset["asset_id"]
            kind = asset["kind"]
            question_occurrences = [
                item
                for item in asset.get("occurrences", [])
                if item.get("track") in NEXT_WAVE_TRACKS
                and item.get("role") in QUESTION_ROLES
            ]
            explanation_occurrences = [
                item
                for item in asset.get("occurrences", [])
                if item.get("track") in NEXT_WAVE_TRACKS
                and item.get("role") not in QUESTION_ROLES
            ]
            for group, occurrences in (
                ("question", question_occurrences),
                ("explanation", explanation_occurrences),
            ):
                for track in {str(item["track"]) for item in occurrences}:
                    key = f"{kind}_{group}"
                    by_track[track][key] += 1
                    unions[key].add(asset_id)

            if kind == "missing" and (question_occurrences or explanation_occurrences):
                missing.append(
                    {
                        "assetId": asset_id,
                        "localPath": asset.get("local_path"),
                        "occurrences": question_occurrences + explanation_occurrences,
                    }
                )

            if kind != "image" or not question_occurrences:
                continue
            tracks = sorted({str(item["track"]) for item in question_occurrences})
            manifest.append(
                {
                    "assetId": asset_id,
                    "sha256": asset.get("sha256"),
                    "localPath": asset.get("local_path"),
                    "sizeBytes": asset.get("size_bytes"),
                    "tracks": tracks,
                    "sections": sorted(
                        {str(item.get("section") or "") for item in question_occurrences}
                    ),
                    "roles": sorted({str(item["role"]) for item in question_occurrences}),
                    "questionIds": sorted(
                        {str(item.get("question_id") or "") for item in question_occurrences}
                    ),
                    "knowledgePoints": sorted(
                        {
                            str(item.get("knowledge_point") or "")
                            for item in question_occurrences
                            if item.get("knowledge_point")
                        }
                    ),
                    "routingState": "awaiting_visual_triage",
                    "allowedPolicies": [
                        "semantic_redraw",
                        "deterministic_vector",
                        "annotated_raster",
                        "needs_review",
                    ],
                }
            )

    manifest.sort(key=lambda item: item["assetId"])
    write_jsonl(MANIFEST, manifest)
    write_json(
        DERIVED,
        {
            "scope": "question-critical assets outside data-analysis and graphic-reasoning",
            "nextWaveTracks": sorted(NEXT_WAVE_TRACKS),
            "uniqueCounts": {key: len(value) for key, value in sorted(unions.items())},
            "byTrack": {
                track: dict(sorted(counts.items()))
                for track, counts in sorted(by_track.items())
            },
            "nextWaveRealImages": len(manifest),
            "missingAssets": missing,
            "routingPolicy": {
                "formula_tex": "xfyun_formula_to_latex; preserve source until verified",
                "chart_or_table": "semantic data extraction; deterministic chart renderer",
                "geometry_logic_science_symbol": "CV segmentation plus graphic-primitives-v2",
                "photo_artwork_complex_map": "preserve source plus OCR/regions/semantic metadata",
                "uncertain_or_answer_sensitive": "needs_review; never approximate",
            },
        },
    )
    print(
        json.dumps(
            {
                "derivedSummary": str(DERIVED),
                "manifest": str(MANIFEST),
                "nextWaveRealImages": len(manifest),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
