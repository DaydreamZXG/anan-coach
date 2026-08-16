#!/usr/bin/env python3
from __future__ import annotations

import unittest

from acceptance_gate import REQUIRED_GATES, answer_preservation, evaluate


class AcceptanceGateTests(unittest.TestCase):
    def test_missing_gate_remains_repair_pending(self) -> None:
        result = evaluate({name: "pass" for name in REQUIRED_GATES if name != "text"})
        self.assertEqual(result["status"], "repair_pending")
        self.assertIn("text", result["blockingGates"])

    def test_all_explicit_gates_can_accept(self) -> None:
        result = evaluate({name: "pass" for name in REQUIRED_GATES})
        self.assertEqual(result["status"], "accepted")

    def test_answer_mapping_mismatch_fails(self) -> None:
        result = answer_preservation("C", {"C": "panel-9"}, {"C": "panel-8"})
        self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
