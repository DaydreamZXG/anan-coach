import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("semantic_run_batch", HERE / "run_batch.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def candidate():
    return {
        "schema_version": "graphic-reasoning-model-semantics-v1",
        "visual_grammar": "regular_geometry",
        "panel_count": 1,
        "option_count": 0,
        "panels": [{
            "position": 1,
            "role": "stem",
            "option_label": None,
            "elements": [{"id": "p1e1", "kind": "line", "count": 1, "relative_region": "center", "line_character": "straight", "fill_state": "none"}],
            "relations": [],
            "topology": {},
            "counts": {"straight_segment_count": 1, "curve_count": 0, "circle_count": 0, "closed_shape_count": 0, "dot_count": 0, "filled_region_count": 0, "symbol_count": 0, "text_count": 0},
            "observations": [],
        }],
        "cross_panel_patterns": [],
        "uncertainties": [],
    }


class SemanticStructureTests(unittest.TestCase):
    def test_valid_candidate(self):
        self.assertEqual([], MODULE.validate(candidate()))

    def test_answer_leak_is_rejected(self):
        value = candidate()
        value["correct_option"] = "A"
        self.assertIn("forbidden_key:correct_option", MODULE.validate(value))

    def test_coordinates_are_rejected(self):
        value = candidate()
        value["panels"][0]["elements"][0]["bbox"] = [0, 0, 1, 1]
        self.assertIn("forbidden_key:panels[0].elements[0].bbox", MODULE.validate(value))

    def test_panel_positions_must_be_ordered(self):
        value = candidate()
        value["panels"][0]["position"] = 2
        self.assertIn("panel_positions", MODULE.validate(value))

    def test_original_image_is_non_vector_presentation_truth(self):
        job = {
            "assetId": "gr_test",
            "sourceSha256": "a" * 64,
            "sourcePaths": ["/tmp/source.png"],
            "questionReferences": [],
            "layoutCandidate": {"layoutType": "sequence", "panelCount": 1, "optionCount": 0, "panelRoles": ["stem"], "source": "test"},
        }
        document = MODULE.solver_document(job, candidate())
        self.assertEqual("original_image", document["presentation"]["mode"])
        self.assertFalse(document["vectorEnhancement"]["blocking"])

    def test_grouped_elements_require_per_instance_positions(self):
        value = candidate()
        value["panels"][0]["elements"][0]["count"] = 2
        self.assertIn("panels[0].elements[0].instances", MODULE.validate(value))

    def test_grid_subtype_cannot_omit_grid(self):
        self.assertIn("panels[0].grid_element_required", MODULE.validate(candidate(), "grid_partition"))


if __name__ == "__main__":
    unittest.main()
