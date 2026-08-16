#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from candidate_ingest import CANDIDATE_SCHEMA
from verify_candidate import verify_candidate


STYLE = {"stroke": "#111111", "fill": None, "lineWidth": 10, "dash": [], "opacity": 1}
TRANSFORM = {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1, "rotateDegrees": 0, "originX": 0, "originY": 0}


def primitive(primitive_type: str, **geometry):
    return {"type": primitive_type, **geometry, "style": dict(STYLE), "transform": dict(TRANSFORM)}


def candidate(primitives):
    return {
        "schemaVersion": CANDIDATE_SCHEMA,
        "assetId": "gr_test",
        "panelId": "panel-1",
        "primitives": primitives,
        "evidence": {"candidateSvgSha256": "b" * 64},
    }


class VerifyCandidateTests(unittest.TestCase):
    def source(self, image: np.ndarray) -> tuple[tempfile.TemporaryDirectory, Path]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "source.png"
        cv2.imwrite(str(path), image)
        return directory, path

    def test_supported_line_is_a_verified_seed_but_not_final(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (10, 50), (90, 50), 0, 1, cv2.LINE_AA)
        directory, path = self.source(image)
        try:
            result = verify_candidate(candidate([primitive("line", **{"from": {"x": 100, "y": 500}, "to": {"x": 900, "y": 500}})]), path)
        finally:
            directory.cleanup()
        self.assertEqual(result["status"], "verified_refinement_seed")
        self.assertEqual(result["gates"]["finalAcceptance"]["status"], "blocked")

    def test_duplicate_lines_fail_nms_gate(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (10, 50), (90, 50), 0, 2)
        lines = [
            primitive("line", **{"from": {"x": 100, "y": 500}, "to": {"x": 900, "y": 500}}),
            primitive("line", **{"from": {"x": 105, "y": 505}, "to": {"x": 895, "y": 505}}),
        ]
        directory, path = self.source(image)
        try:
            result = verify_candidate(candidate(lines), path)
        finally:
            directory.cleanup()
        self.assertEqual(result["status"], "repair_pending")
        self.assertEqual(result["gates"]["duplicateSuppression"]["status"], "fail")

    def test_hollow_circle_cannot_be_promoted_to_solid(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.circle(image, (50, 50), 30, 0, 2, cv2.LINE_AA)
        circle = primitive("circle", center={"x": 500, "y": 500}, radius=300)
        circle["style"]["fill"] = "#111111"
        directory, path = self.source(image)
        try:
            result = verify_candidate(candidate([circle]), path)
        finally:
            directory.cleanup()
        self.assertEqual(result["status"], "repair_pending")
        self.assertEqual(result["gates"]["solidFillOnly"]["status"], "fail")
        self.assertIn("hollow_or_non_solid", result["gates"]["solidFillOnly"]["failures"][0])

    def test_large_curve_offset_fails_pixel_support(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (10, 25), (90, 25), 0, 1)
        wrong = primitive("cubicBezier", **{
            "from": {"x": 100, "y": 800}, "control1": {"x": 300, "y": 900},
            "control2": {"x": 700, "y": 900}, "to": {"x": 900, "y": 800},
        })
        directory, path = self.source(image)
        try:
            result = verify_candidate(candidate([wrong]), path)
        finally:
            directory.cleanup()
        self.assertEqual(result["gates"]["pixelSupport"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
