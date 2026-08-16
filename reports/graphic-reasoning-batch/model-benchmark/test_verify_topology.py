#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from verify_topology import verify_topology


STYLE = {"stroke": "#111111", "fill": None, "lineWidth": 10, "dash": [], "opacity": 1}
TRANSFORM = {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1, "rotateDegrees": 0, "originX": 0, "originY": 0}


def line(start, end):
    return {"type": "line", "from": {"x": start[0], "y": start[1]}, "to": {"x": end[0], "y": end[1]}, "style": dict(STYLE), "transform": dict(TRANSFORM)}


def refinement(primitives):
    return {"schemaVersion": "graphic-vector-deterministic-refinement-v1", "assetId": "gr_test", "panelId": "panel-1", "primitives": primitives}


class VerifyTopologyTests(unittest.TestCase):
    def verify(self, image, primitives):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            cv2.imwrite(str(path), image)
            return verify_topology(refinement(primitives), path)

    def test_connected_right_angle_topology_passes(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (20, 20), (80, 20), 0, 1)
        cv2.line(image, (20, 20), (20, 80), 0, 1)
        result = self.verify(image, [line((200, 200), (800, 200)), line((200, 200), (200, 800))])
        self.assertEqual(result["status"], "topology_verified")
        self.assertTrue(result["rightAngleEvidence"][0]["exact"])

    def test_disconnected_redraw_fails_component_topology(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (20, 20), (80, 20), 0, 1)
        cv2.line(image, (20, 20), (20, 80), 0, 1)
        result = self.verify(image, [line((200, 200), (800, 200)), line((300, 300), (300, 800))])
        self.assertEqual(result["status"], "repair_pending")
        self.assertIn("componentCount", result["gates"]["componentEndpointJunctionLoop"]["mismatches"])

    def test_near_right_angle_is_not_accepted_as_exact(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.line(image, (20, 20), (80, 20), 0, 1)
        cv2.line(image, (20, 20), (22, 80), 0, 1)
        result = self.verify(image, [line((200, 200), (800, 200)), line((200, 200), (220, 800))])
        self.assertEqual(result["gates"]["exactDetectedRightAngles"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
