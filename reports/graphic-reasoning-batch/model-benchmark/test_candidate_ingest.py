#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from candidate_ingest import CandidateError, ingest_svg


class CandidateIngestTests(unittest.TestCase):
    def ingest(self, svg: str, semantics: dict | None = None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.svg"
            path.write_text(svg, encoding="utf-8")
            return ingest_svg(path, asset_id="gr_test", panel_id="panel-1", source_sha256="a" * 64,
                provider="test", model="test-model", semantics=semantics)

    def test_line_curve_and_text_are_typed(self) -> None:
        result = self.ingest(
            '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">'
            '<line x1="10" y1="10" x2="90" y2="10" stroke="black" fill="none"/>'
            '<path d="M 10 50 C 30 20 70 80 90 50" stroke="black" fill="none"/>'
            '<text x="50" y="90" text-anchor="middle">X</text></svg>',
            {"expectedPrimitiveFamilies": ["line", "curve", "text"], "containsText": True, "containsFilledRegions": False},
        )
        self.assertEqual([item["type"] for item in result["primitives"]], ["line", "cubicBezier", "textGlyph"])
        self.assertEqual(result["status"], "candidate_ready_for_refinement")
        self.assertEqual(result["gates"]["finalAcceptance"]["status"], "blocked")

    def test_svg_arc_is_converted_to_typed_arc(self) -> None:
        result = self.ingest(
            '<svg viewBox="0 0 100 100"><path d="M10 50 A40 30 0 0 1 90 50" stroke="#000" fill="none"/></svg>',
            {"expectedPrimitiveFamilies": ["curve", "circle_arc"], "containsText": False, "containsFilledRegions": False},
        )
        self.assertEqual(result["primitives"][0]["type"], "arc")
        self.assertEqual(result["status"], "candidate_ready_for_refinement")

    def test_raster_image_is_rejected(self) -> None:
        result = self.ingest('<svg viewBox="0 0 10 10"><image href="data:image/png;base64,AA=="/></svg>')
        self.assertEqual(result["status"], "candidate_rejected")
        self.assertEqual(result["gates"]["security"]["status"], "fail")
        self.assertIn("image", result["evidence"]["forbiddenElements"])

    def test_text_cannot_be_silently_replaced_by_path(self) -> None:
        result = self.ingest(
            '<svg viewBox="0 0 10 10"><path d="M1 1 L9 9" stroke="black" fill="none"/></svg>',
            {"containsText": True},
        )
        self.assertEqual(result["status"], "candidate_rejected")
        self.assertIn("expected_textGlyph_missing", result["evidence"]["issues"])

    def test_transform_is_not_applied_as_unverified_geometry(self) -> None:
        result = self.ingest('<svg viewBox="0 0 10 10"><g transform="scale(2)"><line x1="1" y1="1" x2="2" y2="2"/></g></svg>')
        self.assertEqual(result["status"], "candidate_rejected")
        self.assertIn("unflattened_transform:g", result["evidence"]["issues"])

    def test_requires_real_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.svg"
            path.write_text('<svg viewBox="0 0 10 10"/>', encoding="utf-8")
            with self.assertRaises(CandidateError):
                ingest_svg(path, asset_id="gr_test", panel_id="panel-1", source_sha256="bad",
                    provider="test", model="test-model")


if __name__ == "__main__":
    unittest.main()
