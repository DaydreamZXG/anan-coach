import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_regular_semantics", HERE / "run_regular_semantics.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def candidate():
    return {
        "schema": MODULE.PANEL_SCHEMA, "p": 1, "role": "stem", "opt": None,
        "frame": {"k": "square", "grid": None},
        "lines": [{"ori": "vertical", "reg": "center", "connects": "top to bottom", "feat": []}],
        "curves": [{"k": "arc", "reg": "bottom_left", "connects": "left to bottom", "feat": []}],
        "shapes": [], "dots": [], "symbols": [], "topo": [1, 2, 0, 1], "facts": ["one line and one arc"], "uncertain": [],
    }


class RegularSemanticTests(unittest.TestCase):
    def test_valid_candidate(self):
        self.assertEqual([], MODULE.validate(candidate(), 1, "stem", None))

    def test_grid_requires_exact_dimensions(self):
        value = candidate()
        value["frame"] = {"k": "grid", "grid": None}
        self.assertIn("grid", MODULE.validate(value, 1, "stem", None))

    def test_grouped_shapes_require_each_instance(self):
        value = candidate()
        value["shapes"] = [{"k": "triangle", "n": 4, "reg": ["top_left"], "ori": ["up"], "fill": "hollow", "feat": ["right_angle"]}]
        self.assertIn("shapes[0].instances", MODULE.validate(value, 1, "stem", None))

    def test_coordinates_are_forbidden(self):
        value = candidate()
        value["lines"][0]["bbox"] = [0, 0, 1, 1]
        self.assertIn("forbidden_key:lines[0].bbox", MODULE.validate(value, 1, "stem", None))


if __name__ == "__main__":
    unittest.main()
