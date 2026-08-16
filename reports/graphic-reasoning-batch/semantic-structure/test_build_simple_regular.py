import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("build_simple_regular", HERE / "build_simple_regular.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SimpleRegularTests(unittest.TestCase):
    def test_exact_right_angle(self):
        points = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}]
        index, error = MODULE.right_angle(points)
        self.assertEqual(0, index)
        self.assertAlmostEqual(0, error)

    def test_cell_uses_detected_grid_boundaries(self):
        lines = [0, 34, 69, 103, 137]
        self.assertEqual(1, MODULE.cell_for(16, lines))
        self.assertEqual(4, MODULE.cell_for(121, lines))

    def test_relative_quadrants(self):
        self.assertEqual("top_left", MODULE.relative_region(200, 200))
        self.assertEqual("bottom_right", MODULE.relative_region(800, 800))
        self.assertEqual("top_right", MODULE.relative_region(581, 183))

    def test_triangle_signature_is_order_independent(self):
        first = [
            {"region": "top_left", "rightAngleAt": "bottom_right", "hypotenuse": "diagonal_up", "fill": "hollow"},
            {"region": "bottom_right", "rightAngleAt": "top_left", "hypotenuse": "diagonal_up", "fill": "hollow"},
        ]
        self.assertEqual(MODULE.triangle_signature(first), MODULE.triangle_signature(list(reversed(first))))

    def test_unsupported_rerun_removes_stale_solver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in MODULE.SEMANTIC_OUTPUTS:
                (root / name).write_text("stale", encoding="utf-8")
            MODULE.clear_stale_semantic_outputs(root)
            self.assertFalse(any((root / name).exists() for name in MODULE.SEMANTIC_OUTPUTS))


if __name__ == "__main__":
    unittest.main()
