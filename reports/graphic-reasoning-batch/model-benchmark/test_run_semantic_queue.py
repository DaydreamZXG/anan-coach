import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_semantic_queue", HERE / "run_semantic_queue.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def valid_candidate():
    return {
        "schema_version": "graphic-semantic-candidate-v1",
        "authority": "semantic_candidate",
        "visual_grammar": "regular_geometry",
        "elements": [{"id": "e1", "kind": "line", "count": 2, "relative_region": "center", "line_character": "straight", "fill_state": "none"}],
        "relations": [],
        "topology_candidates": {},
        "uncertainties": [],
    }


class SemanticQueueTests(unittest.TestCase):
    def test_valid_semantic_candidate_passes(self):
        self.assertEqual([], MODULE.validate_candidate(valid_candidate()))

    def test_coordinates_and_svg_are_rejected_recursively(self):
        candidate = valid_candidate()
        candidate["elements"][0]["geometry"] = {"x": 10, "path": "M0 0"}
        errors = MODULE.validate_candidate(candidate)
        self.assertIn("forbidden_key:elements[0].geometry.x", errors)
        self.assertIn("forbidden_key:elements[0].geometry.path", errors)

    def test_unknown_primitive_kind_is_rejected(self):
        candidate = valid_candidate()
        candidate["elements"][0]["kind"] = "arbitraryPixelContour"
        self.assertIn("elements[0].kind", MODULE.validate_candidate(candidate))

    def test_subtype_incompatible_grammar_is_rejected(self):
        candidate = valid_candidate()
        candidate["visual_grammar"] = "glyph_life_object"
        self.assertIn("visual_grammar_subtype_conflict", MODULE.validate_candidate(candidate, "closed_polygon_composition"))

    def test_non_contract_relative_region_is_rejected(self):
        candidate = valid_candidate()
        candidate["elements"][0]["relative_region"] = "top_center"
        self.assertIn("elements[0].relative_region", MODULE.validate_candidate(candidate))

    def test_relative_region_alias_is_normalized_with_evidence(self):
        candidate = valid_candidate()
        candidate["elements"][0]["relative_region"] = "top_center"
        normalized, changes = MODULE.normalize_candidate(candidate)
        self.assertEqual("top", normalized["elements"][0]["relative_region"])
        self.assertEqual("top_center", changes[0]["from"])

    def test_curve_kind_cannot_claim_straight_line_character(self):
        candidate = valid_candidate()
        candidate["elements"][0].update({"kind": "circle", "line_character": "straight", "fill_state": "hollow"})
        self.assertIn("elements[0].line_character_kind_conflict", MODULE.validate_candidate(candidate))

    def test_open_polyline_cannot_claim_hollow_fill(self):
        candidate = valid_candidate()
        candidate["elements"][0].update({"kind": "polyline", "line_character": "straight", "fill_state": "hollow"})
        self.assertIn("elements[0].fill_state_kind_conflict", MODULE.validate_candidate(candidate))


if __name__ == "__main__":
    unittest.main()
