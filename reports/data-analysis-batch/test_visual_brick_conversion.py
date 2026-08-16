from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "structured-schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(keys(child) for child in value)) if value else set()
    return set()


class VisualBrickConversionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = json.loads((ROOT / "inventory.json").read_text(encoding="utf-8"))
        cls.manifest = rows(ROOT / "manifest.jsonl")
        cls.candidates = [
            json.loads((ROOT / entry["structured_json_path"]).read_text(encoding="utf-8"))
            for entry in cls.manifest
        ]
        cls.visuals = [candidate for candidate in cls.candidates if candidate["chart_type"] == "structured_visual"]

    def test_non_semantic_contours_are_not_claimed_as_success(self) -> None:
        self.assertEqual(self.inventory["asset_count"], 1_486)
        self.assertEqual(
            self.inventory["processing_status_counts"],
            {"success": 1_486},
        )
        for field in ("unsupported_count", "failed_count", "pending_count"):
            self.assertEqual(self.inventory[field], 0, field)
        self.assertEqual(self.inventory["needs_manual_review_count"], 0)
        self.assertEqual(len(rows(ROOT / "errors.jsonl")), 0)

    def test_all_candidates_satisfy_the_schema(self) -> None:
        self.assertEqual(len(self.candidates), 1_486)
        for candidate in self.candidates:
            VALIDATOR.validate(candidate)

    def test_all_former_unsupported_assets_are_vector_bricks(self) -> None:
        self.assertEqual(len(self.visuals), 60)
        for candidate in self.visuals:
            visual = candidate["structured_visual"]
            self.assertIn(visual["schemaVersion"], {"graphic-primitives-v2", "patterned-pie-options-v1", "semantic-pie-options-v1", "semantic-statistical-panels-v1", "semantic-simplex-grids-v1", "semantic-formula-v1", "semantic-microcharts-v1"})
            self.assertIn(visual["fidelityTier"], {"deterministic_vector", "semantic_vector"})
            self.assertNotIn("image", keys(visual))
            self.assertNotIn("imageUrl", keys(visual))
            self.assertNotIn("backgroundImage", keys(visual))
            self.assertNotIn("base64", keys(visual))

    def test_payloads_stay_inside_mini_program_limits(self) -> None:
        for candidate in self.visuals:
            visual = candidate["structured_visual"]
            complexity = visual["complexity"]
            if visual["schemaVersion"] == "patterned-pie-options-v1":
                self.assertLessEqual(complexity["optionCount"], 8)
                self.assertEqual(complexity["sectorCount"], complexity["optionCount"] * 3)
                self.assertEqual(complexity["patternDefinitionCount"], 3)
                for option in visual["options"]:
                    self.assertEqual([sector["pattern"] for sector in option["sectors"]], ["dots", "diagonal", "grid"])
                continue
            if visual["schemaVersion"] == "semantic-microcharts-v1":
                self.assertLessEqual(complexity["panelCount"], 8)
                self.assertLessEqual(complexity["pointCount"], 512)
                self.assertLessEqual(complexity["barCount"], 96)
                self.assertLessEqual(complexity["estimatedDrawCommandCount"], 1_024)
                continue
            if visual["schemaVersion"] == "semantic-pie-options-v1":
                self.assertLessEqual(complexity["optionCount"], 8)
                self.assertEqual(complexity["optionCount"], len(visual["options"]))
                self.assertEqual(complexity["sectorCount"], sum(len(option["sectors"]) for option in visual["options"]))
                self.assertLessEqual(complexity["estimatedDrawCommandCount"], 256)
                continue
            if visual["schemaVersion"] == "semantic-statistical-panels-v1":
                self.assertEqual(complexity["panelCount"], len(visual["panels"]))
                self.assertEqual(complexity["valueCount"], sum(len(series["values"]) for panel in visual["panels"] for series in panel["series"]))
                continue
            if visual["schemaVersion"] == "semantic-simplex-grids-v1":
                self.assertEqual(complexity["vertexCount"], len(visual["vertices"]))
                self.assertEqual(complexity["pointCount"], len(visual["points"]))
                continue
            if visual["schemaVersion"] == "semantic-formula-v1":
                self.assertEqual(complexity["expressionLength"], len(visual["expression"]))
                self.assertEqual(visual["format"], "latex")
                continue
            self.assertLessEqual(complexity["primitiveCount"], 512)
            self.assertLessEqual(complexity["pathCommandCount"], 4_096)
            self.assertLessEqual(complexity["pointCount"], 4_096)
            for panel in visual["panels"]:
                self.assertLessEqual(len(panel["primitives"]), 128)
                for primitive in panel["primitives"]:
                    if primitive["type"] == "pathGroup":
                        self.assertLessEqual(sum(len(path["commands"]) for path in primitive["paths"]), 2_048)

    def test_rerender_evidence_is_real_and_reproducible(self) -> None:
        for candidate in self.visuals:
            visual = candidate["structured_visual"]
            evidence = visual["evidence"]
            if visual["schemaVersion"] == "patterned-pie-options-v1":
                self.assertEqual(evidence["optionCount"], 4)
                self.assertEqual(evidence["sectorsPerOption"], 3)
                self.assertEqual(set(evidence["patternVocabulary"]), {"dots", "grid", "diagonal"})
                self.assertGreaterEqual(evidence["boundaryMeanConfidence"], 0.75)
            elif visual["schemaVersion"] == "graphic-primitives-v2":
                self.assertGreaterEqual(evidence["foregroundIoU"], 0.35)
                self.assertLessEqual(evidence["meanAbsoluteError"], 20)
            elif visual["schemaVersion"] == "semantic-pie-options-v1":
                self.assertEqual(evidence["geometryEngine"], "human_verified_sector_geometry")
                self.assertEqual(evidence["converterVersion"], "semantic-pie-options-v1")
            elif visual["schemaVersion"] in {"semantic-statistical-panels-v1", "semantic-simplex-grids-v1"}:
                self.assertEqual(evidence["converterVersion"], visual["schemaVersion"])
            elif visual["schemaVersion"] == "semantic-formula-v1":
                self.assertEqual(evidence["provider"], "manual_verified")
            else:
                self.assertIn(evidence["converterVersion"], {"semantic-microcharts-v1", "hand-annotated-semantic-v1"})
            source = Path(candidate["source"]["local_path"])
            rendered = ROOT / "previews/structured_visual" / f"{candidate['asset_id']}.png"
            comparison = ROOT / "previews/structured_visual" / f"{candidate['asset_id']}-comparison.png"
            self.assertEqual(sha256(source), evidence["sourceSha256"])
            self.assertEqual(sha256(rendered), evidence["renderSha256"])
            self.assertTrue(comparison.is_file())

    def test_patterned_pie_sample_is_semantic_not_pixel_tracing(self) -> None:
        candidate = next(
            row for row in self.candidates
            if row["asset_id"] == "da_dba634efefa69d74238550ca"
        )
        visual = candidate["structured_visual"]
        self.assertEqual(visual["schemaVersion"], "patterned-pie-options-v1")
        self.assertEqual(candidate["review_status"], "verified_sample")
        self.assertEqual([option["label"] for option in visual["options"]], ["A", "B", "C", "D"])
        expected = [
            [0.0, 324.0, 338.5],
            [0.0, 297.0, 311.0],
            [0.0, 282.5, 297.0],
            [0.0, 247.0, 261.0],
        ]
        for option, boundaries in zip(visual["options"], expected):
            self.assertEqual(option["boundariesDegrees"], boundaries)
            self.assertEqual(
                [sector["pattern"] for sector in option["sectors"]],
                ["dots", "diagonal", "grid"],
            )
        serialized = json.dumps(visual, ensure_ascii=False)
        self.assertNotIn('"primitives"', serialized)
        self.assertNotIn('"paths"', serialized)

    def test_patterned_share_options_keep_fill_regions_separate_from_real_boundaries(self) -> None:
        candidate = next(
            row for row in self.candidates
            if row["asset_id"] == "da_d93edcde06cb57d89aa5728d"
        )
        visual = candidate["structured_visual"]
        self.assertEqual(visual["schemaVersion"], "semantic-pie-options-v1")
        self.assertEqual(
            [option["boundariesDegrees"] for option in visual["options"]],
            [[0, 90], [0, 156], [108, 207], [109, 270, 314]],
        )
        self.assertEqual([len(option["sectors"]) for option in visual["options"]], [1, 1, 1, 1])
        self.assertEqual(
            [(sector["startDegrees"], sector["sweepDegrees"]) for option in visual["options"] for sector in option["sectors"]],
            [(0, 90), (156, 204), (207, 261), (0, 360)],
        )

    def test_school_ternary_grid_uses_the_source_seven_divisions(self) -> None:
        candidate = next(
            row for row in self.candidates
            if row["asset_id"] == "da_a17f9561c67155407e0c6d4f"
        )
        visual = candidate["structured_visual"]
        self.assertEqual(visual["divisions"], 7)
        self.assertEqual(visual["styling"]["pointRadius"], 10)
        self.assertEqual(
            {point["label"]: (point["position"]["x"], point["position"]["y"]) for point in visual["points"]},
            {"A": (237, 469), "B": (348, 410), "C": (455, 343), "D": (310, 343), "E": (273, 407), "F": (346, 282), "G": (199, 409)},
        )

    def test_diamond_grid_preserves_thin_source_strokes(self) -> None:
        candidate = next(
            row for row in self.candidates
            if row["asset_id"] == "da_b1f1d179ade3472c753f8143"
        )
        styling = candidate["structured_visual"]["styling"]
        self.assertEqual((styling["outerLineWidth"], styling["gridLineWidth"], styling["pointRadius"]), (1, 1, 2))


if __name__ == "__main__":
    unittest.main()
