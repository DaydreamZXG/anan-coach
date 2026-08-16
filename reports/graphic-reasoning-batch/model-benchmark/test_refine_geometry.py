#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from candidate_ingest import CANDIDATE_SCHEMA
from refine_geometry import refine_candidate


STYLE = {"stroke": "#111111", "fill": None, "lineWidth": 10, "dash": [], "opacity": 1}
TRANSFORM = {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1, "rotateDegrees": 0, "originX": 0, "originY": 0}


def primitive(primitive_type: str, **geometry):
    return {"type": primitive_type, **geometry, "style": dict(STYLE), "transform": dict(TRANSFORM)}


def candidate(primitives):
    return {"schemaVersion": CANDIDATE_SCHEMA, "assetId": "gr_test", "panelId": "panel-1", "sourceSha256": "a" * 64, "primitives": primitives}


class RefineGeometryTests(unittest.TestCase):
    def refine(self, image: np.ndarray, primitives):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            cv2.imwrite(str(path), image)
            return refine_candidate(candidate(primitives), path)

    def test_near_horizontal_line_is_fitted_and_snapped(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (10, 50), (90, 50), 0, 2, cv2.LINE_AA)
        result = self.refine(image, [primitive("line", **{"from": {"x": 90, "y": 480}, "to": {"x": 910, "y": 520}})])
        line = result["primitives"][0]
        self.assertEqual(line["from"]["y"], line["to"]["y"])
        self.assertEqual(result["primitiveEvidence"][0]["axisSnap"], "horizontal")

    def test_circle_is_refitted_without_changing_type(self) -> None:
        image = np.full((120, 120), 255, np.uint8)
        cv2.circle(image, (60, 60), 35, 0, 2, cv2.LINE_AA)
        result = self.refine(image, [primitive("circle", center={"x": 510, "y": 490}, radius=300)])
        self.assertEqual(result["primitives"][0]["type"], "circle")
        self.assertEqual(result["primitiveEvidence"][0]["status"], "refined")

    def test_bezier_control_points_use_bounded_pixel_optimization(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        points = []
        for t in np.linspace(0, 1, 120):
            point = (1 - t) ** 3 * np.array([10, 70]) + 3 * (1 - t) ** 2 * t * np.array([30, 20]) + 3 * (1 - t) * t ** 2 * np.array([70, 20]) + t ** 3 * np.array([90, 70])
            points.append(point)
        cv2.polylines(image, [np.rint(points).astype(np.int32)], False, 0, 2, cv2.LINE_AA)
        curve = primitive("cubicBezier", **{
            "from": {"x": 100, "y": 730}, "control1": {"x": 300, "y": 230},
            "control2": {"x": 700, "y": 230}, "to": {"x": 900, "y": 730},
        })
        result = self.refine(image, [curve])
        evidence = result["primitiveEvidence"][0]
        self.assertEqual(result["primitives"][0]["type"], "cubicBezier")
        self.assertLessEqual(evidence["objectiveAfter"], evidence["objectiveBefore"])
        self.assertLessEqual(evidence["maximumSeedDriftPixels"], 6.01)

    def test_unsupported_ellipse_stays_repair_pending(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.ellipse(image, (50, 50), (30, 20), 0, 0, 360, 0, 2)
        ellipse = primitive("ellipse", center={"x": 500, "y": 500}, radiusX=300, radiusY=200, rotationDegrees=0)
        result = self.refine(image, [ellipse])
        self.assertEqual(result["status"], "repair_pending")


if __name__ == "__main__":
    unittest.main()
