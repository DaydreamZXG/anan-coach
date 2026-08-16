import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("review_simple_regular", HERE / "review_simple_regular.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ReviewSimpleRegularTests(unittest.TestCase):
    def grid_solver(self):
        panels = []
        for position in range(1, 11):
            if position == 6:
                panels.append({"p": 6, "role": "missing", "symbols": [], "frame": {"kind": "grid", "rows": 4, "columns": 4}})
                continue
            symbols = [
                {"kind": kind, "cell": [row, column], "orientation": orientation, "fill": "hollow"}
                for kind, row, column, orientation in MODULE.GRID_EXPECTED[position]
            ]
            panels.append({"p": position, "role": "stem" if position < 6 else "option", "symbols": symbols, "frame": {"kind": "grid", "rows": 4, "columns": 4}})
        return {"layout": {"layoutType": "matrix", "panelCount": 10, "optionCount": 4}, "panels": panels}

    def grid_geometry(self):
        return {
            "panels": [
                {"p": position, "horizontalLinePixels": [0, 1, 2, 3, 4], "verticalLinePixels": [0, 1, 2, 3, 4], "symbols": [{}, {}]}
                for position in MODULE.GRID_EXPECTED
            ]
        }

    def test_exact_grid_fixture_passes(self):
        checks = MODULE.review_grid(self.grid_solver(), self.grid_geometry())
        self.assertTrue(all(item["status"] == "pass" for item in checks))

    def test_wrong_symbol_kind_fails(self):
        solver = self.grid_solver()
        solver["panels"][9]["symbols"][0]["kind"] = "triangle"
        checks = MODULE.review_grid(solver, self.grid_geometry())
        by_name = {item["name"]: item for item in checks}
        self.assertEqual("fail", by_name["symbol_kind_cell_orientation"]["status"])


if __name__ == "__main__":
    unittest.main()
