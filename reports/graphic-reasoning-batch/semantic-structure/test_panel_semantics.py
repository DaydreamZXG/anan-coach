import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_panel_semantics", HERE / "run_panel_semantics.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def panel_candidate():
    return {
        "schema": MODULE.PANEL_SCHEMA,
        "p": 1,
        "role": "stem",
        "opt": None,
        "frame": {"k": "none", "grid": None},
        "obj": [{
            "k": "line", "n": 1, "reg": ["center"], "ori": ["vertical"], "lc": "straight",
            "fill": "none", "txt": None, "parts": [], "feat": [],
        }],
        "count": {key: (1 if key == "seg" else 0) for key in MODULE.COUNT_KEYS},
        "topo": [1, 2, 0, 0],
        "facts": ["one vertical line"],
        "uncertain": [],
    }


class PanelSemanticTests(unittest.TestCase):
    def test_valid_compact_panel(self):
        self.assertEqual([], MODULE.validate_panel(panel_candidate(), 1, "stem", None))

    def test_grouped_instances_keep_each_region_and_orientation(self):
        value = panel_candidate()
        value["obj"][0]["n"] = 2
        self.assertIn("objects[0].regions", MODULE.validate_panel(value, 1, "stem", None))
        self.assertIn("objects[0].orientations", MODULE.validate_panel(value, 1, "stem", None))

    def test_coordinates_are_forbidden(self):
        value = panel_candidate()
        value["obj"][0]["bbox"] = [0, 0, 1, 1]
        self.assertIn("forbidden_key:obj[0].bbox", MODULE.validate_panel(value, 1, "stem", None))

    def test_missing_panel_is_schema_valid(self):
        value = MODULE.missing_panel(3)
        self.assertEqual([], MODULE.validate_panel(value, 3, "missing", None))


if __name__ == "__main__":
    unittest.main()
