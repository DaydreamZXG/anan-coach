#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class ContractTests(unittest.TestCase):
    def test_dual_layer_contract_requires_both_layers_and_final_gates(self) -> None:
        contract = json.loads((ROOT / "contracts/dual-layer-contract-v1.json").read_text(encoding="utf-8"))
        self.assertIn("panels", contract["required"])
        panel_required = contract["properties"]["panels"]["items"]["required"]
        self.assertIn("semanticLayer", panel_required)
        self.assertIn("renderLayer", panel_required)
        gate_required = contract["properties"]["acceptance"]["properties"]["gates"]["required"]
        for name in ("sourceHash", "panelCount", "optionCount", "topology", "fillState", "arrow", "text", "direction", "exactAngles", "pixelFit", "answerPreservation", "frontendContract"):
            self.assertIn(name, gate_required)

    def test_exclusion_policy_has_no_blanket_subtype(self) -> None:
        policy = json.loads((ROOT / "exclusion-policies.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["regularSubtypes"], [])


if __name__ == "__main__":
    unittest.main()
