import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("audit_deterministic_representatives", HERE / "audit_deterministic_representatives.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class DeterministicRepresentativeAuditTests(unittest.TestCase):
    def test_semantic_family_mapping_keeps_lines_and_curves_distinct(self):
        families = MODULE.render_semantic_families([
            {"type": "line", "from": {"x": 0, "y": 0}, "to": {"x": 1, "y": 1}},
            {"type": "cubicBezier"},
        ])
        self.assertEqual({"straight_stroke", "curved_stroke"}, families)

    def test_failed_panel_blocks_aggregate_gate(self):
        rows = [
            {"panelId": "panel-1", "status": "repair_pending", "gates": {"pixelFit": "pass", "duplicateSuppression": "pass", "solidFillOnly": "pass", "topology": "pass"}},
            {"panelId": "panel-2", "status": "repair_pending", "gates": {"pixelFit": "fail", "duplicateSuppression": "pass", "solidFillOnly": "pass", "topology": "pass"}},
        ]
        aggregate = MODULE.aggregate_panel_gates(rows)
        self.assertEqual("fail", aggregate["pixelFit"]["status"])
        self.assertEqual(["panel-2"], aggregate["pixelFit"]["failedPanels"])


if __name__ == "__main__":
    unittest.main()
