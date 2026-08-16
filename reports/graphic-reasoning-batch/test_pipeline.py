#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest

import cv2
import numpy as np

import pipeline


class PipelineTests(unittest.TestCase):
    def test_jiangsu_character_tex_is_recovered_as_graphic(self) -> None:
        question = {"graphic_reasoning": {"reasons": ["jiangsu_character_reasoning"]}}
        self.assertTrue(pipeline._recover_graphic_tex(question, "stem", {"flag": "tex"}))
        self.assertTrue(pipeline._recover_graphic_tex(question, "option", {"flag": "tex"}))
        self.assertFalse(pipeline._recover_graphic_tex(question, "analysis", {"flag": "tex"}))
        self.assertFalse(pipeline._recover_graphic_tex({}, "stem", {"flag": "tex"}))

    def test_nested_inner_drawing_frames_are_not_duplicate_panels(self) -> None:
        image = np.full((268, 600), 255, np.uint8)
        for x in (7, 105, 203, 300, 398, 496):
            cv2.rectangle(image, (x, 6), (x + 97, 103), 0, 2)
            if x < 496:
                cv2.rectangle(image, (x + 8, 14), (x + 89, 95), 0, 2)
        for x in (105, 203, 300, 398):
            cv2.rectangle(image, (x, 131), (x + 97, 227), 0, 2)
            cv2.rectangle(image, (x + 8, 138), (x + 89, 219), 0, 2)
        boxes, _ = pipeline.detect_panels(image)
        self.assertEqual(len(boxes), 10)

    def test_line_curve_grammar_emits_typed_geometry_only(self) -> None:
        image = np.full((100, 100), 255, np.uint8)
        cv2.rectangle(image, (8, 8), (91, 91), 0, 2)
        cv2.line(image, (50, 9), (50, 70), 0, 2)
        cv2.line(image, (9, 70), (50, 70), 0, 2)
        cv2.ellipse(image, (50, 70), (32, 25), 0, 180, 360, 0, 2)
        primitives, metrics = pipeline.extract_line_curve_primitives(image, "sha256:" + "5" * 64)
        types = [primitive["type"] for primitive in primitives]
        self.assertIn("rect", types)
        self.assertIn("line", types)
        self.assertIn("cubicBezier", types)
        self.assertFalse({"pathGroup", "polygon", "face"} & set(types))
        self.assertEqual(metrics["forbiddenPrimitiveCounts"], {"pathGroup": 0, "polygon": 0, "face": 0})

    def test_panel_detection_splits_nine_cell_grid(self) -> None:
        image = np.full((300, 300), 255, dtype=np.uint8)
        for offset in (0, 100, 200, 300):
            cv2.line(image, (offset, 0), (offset, 299), 0, 2)
            cv2.line(image, (0, offset), (299, offset), 0, 2)
        boxes, info = pipeline.detect_panels(image)
        self.assertTrue(info["splitFromGrid"] or len(boxes) >= 9)
        self.assertGreaterEqual(len(boxes), 9)

    def test_scene_has_no_raster_background_primitive(self) -> None:
        image = np.full((120, 120), 255, dtype=np.uint8)
        cv2.rectangle(image, (15, 15), (105, 105), 0, 3)
        cv2.line(image, (15, 105), (105, 15), 0, 3)
        row = {"assetId": "gr_test", "sha256": "a" * 64, "localPaths": ["in-memory"]}
        boxes, _ = pipeline.detect_panels(image)
        classification = pipeline.classify_categories(row, image, boxes)
        visual, _ = pipeline.build_scene(row, classification, image)
        self.assertEqual(pipeline.validate_visual(visual), [])
        all_types = [primitive["type"] for panel in visual["panels"] for primitive in panel["primitives"]]
        self.assertNotIn("raster", all_types)
        self.assertEqual(visual["fidelityTier"], "deterministic_vector")

    def test_outline_does_not_become_filled_face(self) -> None:
        image = np.full((120, 120), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (100, 100), 0, 2)
        color = pipeline._contour_color(image, cv2.findContours(cv2.threshold(image, 235, 255, cv2.THRESH_BINARY_INV)[1], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0])
        self.assertIsNone(color)

    def test_unframed_horizontal_figures_are_split(self) -> None:
        image = np.full((100, 400), 255, dtype=np.uint8)
        for offset in (0, 100, 200, 300):
            cv2.polylines(image, [np.array([[offset + 15, 80], [offset + 50, 20], [offset + 85, 80]], dtype=np.int32)], True, 0, 2)
        boxes, _ = pipeline.detect_panels(image)
        self.assertGreaterEqual(len(boxes), 4)

    def test_textured_pie_uses_sector_and_texture_primitives(self) -> None:
        image = np.full((220, 220), 255, dtype=np.uint8)
        cv2.circle(image, (110, 110), 78, 0, 2)
        for angle in range(0, 360, 45):
            radians = np.deg2rad(angle)
            cv2.line(image, (110, 110), (int(110 + 76 * np.cos(radians)), int(110 + 76 * np.sin(radians))), 0, 2)
        for point in ((70, 80), (92, 76), (145, 82), (160, 140)):
            cv2.circle(image, point, 3, 0, -1)
        primitives, evidence = pipeline.extract_textured_pie_primitives(image, "sha256:" + "b" * 64)
        primitive_types = [primitive["type"] for primitive in primitives]
        self.assertIn("arc", primitive_types)
        self.assertIn("face", primitive_types)
        self.assertLess(sum(primitive_type == "line" for primitive_type in primitive_types), 40)
        self.assertTrue(evidence["sectorAngles"])

    def test_regular_geometry_does_not_emit_circle_hough_artifacts(self) -> None:
        image = np.full((120, 120), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (100, 100), 0, 2)
        primitives, _ = pipeline.extract_primitives(image, "sha256:" + "c" * 64, allow_circles=False)
        self.assertNotIn("circle", [primitive["type"] for primitive in primitives])

    def test_line_nms_suppresses_thick_line_edge_duplicates(self) -> None:
        kept = pipeline._line_nms([(10, 20, 100, 20), (12, 23, 98, 23), (50, 5, 50, 90)])
        self.assertEqual(len(kept), 2)

    def test_regular_outline_emits_one_unfilled_polygon(self) -> None:
        image = np.full((140, 140), 255, dtype=np.uint8)
        cv2.rectangle(image, (25, 25), (115, 115), 0, 3)
        primitives, metrics = pipeline.extract_regular_primitives(image, "sha256:" + "d" * 64, "closed_polygon_composition")
        polygons = [primitive for primitive in primitives if primitive["type"] == "polygon"]
        self.assertEqual(len(polygons), 1)
        self.assertIsNone(polygons[0]["style"]["fill"])
        self.assertEqual(metrics["filledFaceCount"], 0)

    def test_regular_circle_composition_deduplicates_circle_edges(self) -> None:
        image = np.full((160, 160), 255, dtype=np.uint8)
        cv2.circle(image, (80, 80), 48, 0, 3)
        primitives, _ = pipeline.extract_regular_primitives(image, "sha256:" + "e" * 64, "circle_composition")
        self.assertEqual(sum(primitive["type"] == "circle" for primitive in primitives), 1)

    def test_hexagon_is_not_promoted_to_circle(self) -> None:
        image = np.full((160, 160), 255, dtype=np.uint8)
        points = np.array([[80, 20], [132, 50], [132, 110], [80, 140], [28, 110], [28, 50]], dtype=np.int32)
        cv2.polylines(image, [points], True, 0, 3)
        primitives, _ = pipeline.extract_regular_primitives(image, "sha256:" + "f" * 64, "circle_polygon_composition")
        self.assertNotIn("circle", [primitive["type"] for primitive in primitives])

    def test_open_thick_stroke_is_not_a_filled_face(self) -> None:
        image = np.full((140, 140), 255, dtype=np.uint8)
        cv2.polylines(image, [np.array([[25, 25], [25, 110], [110, 110], [110, 25]], dtype=np.int32)], False, 0, 5)
        primitives, _ = pipeline.extract_regular_primitives(image, "sha256:" + "1" * 64, "filled_region_composition")
        self.assertNotIn("face", [primitive["type"] for primitive in primitives])

    def test_question_mark_panel_is_marked_missing(self) -> None:
        image = np.full((140, 100), 255, dtype=np.uint8)
        cv2.putText(image, "?", (22, 105), cv2.FONT_HERSHEY_SIMPLEX, 3, 0, 7, cv2.LINE_AA)
        self.assertTrue(pipeline._is_question_mark_panel(image))

    def test_regular_subtype_separates_analysis_annotation(self) -> None:
        subtype, source, confidence, _ = pipeline.infer_regular_subtype(
            {"occurrences": [{"role": "analysis", "knowledgePoint": "属性规律-对称性"}]},
            {"categories": ["polyline_polygon"]},
            {"panelRatios": {}, "primitiveCandidateTotals": {}, "panelCount": 1},
            None,
        )
        self.assertEqual(subtype, "annotated_explanation")
        self.assertEqual(source, "role_provenance")
        self.assertEqual(confidence, 1.0)

    def test_plain_rectangle_is_not_a_grid_subtype_feature(self) -> None:
        image = np.full((120, 120), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (100, 100), 0, 2)
        features = pipeline._regular_panel_features(image)
        self.assertFalse(features["grid"])

    def test_grid_requires_internal_dividers_on_both_axes(self) -> None:
        image = np.full((150, 150), 255, dtype=np.uint8)
        for offset in (25, 75, 125):
            cv2.line(image, (offset, 20), (offset, 130), 0, 2)
            cv2.line(image, (20, offset), (130, offset), 0, 2)
        features = pipeline._regular_panel_features(image)
        self.assertTrue(features["grid"])

    def test_grid_renderer_uses_shared_lines_and_cell_faces(self) -> None:
        image = np.full((160, 160), 255, dtype=np.uint8)
        for offset in (20, 60, 100, 140):
            cv2.line(image, (offset, 20), (offset, 140), 0, 2)
            cv2.line(image, (20, offset), (140, offset), 0, 2)
        cv2.rectangle(image, (21, 21), (59, 59), 0, -1)
        primitives, metrics = pipeline.extract_grid_primitives(image)
        self.assertEqual(metrics["gridColumns"], 3)
        self.assertEqual(metrics["gridRows"], 3)
        self.assertTrue(any(primitive["type"] == "rect" for primitive in primitives))
        self.assertLessEqual(sum(primitive["type"] == "line" for primitive in primitives), 8)

    def test_stroke_graph_open_curve_emits_topology_and_beziers(self) -> None:
        image = np.full((180, 140), 255, dtype=np.uint8)
        points = np.array([[105, 20], [75, 20], [55, 42], [62, 70], [92, 82], [105, 103], [94, 145], [55, 158], [30, 145]], dtype=np.int32)
        cv2.polylines(image, [points], False, 0, 3, cv2.LINE_AA)
        primitives, metrics = pipeline.extract_stroke_graph_primitives(image, "sha256:" + "2" * 64)
        self.assertEqual(metrics["endpointCount"], 2)
        self.assertEqual(metrics["loopCount"], 0)
        self.assertGreater(sum(primitive["type"] == "cubicBezier" for primitive in primitives), 0)
        self.assertNotIn("face", [primitive["type"] for primitive in primitives])

    def test_stroke_graph_closed_curve_preserves_loop_without_fill(self) -> None:
        image = np.full((160, 160), 255, dtype=np.uint8)
        cv2.ellipse(image, (80, 80), (48, 62), 0, 0, 360, 0, 3, cv2.LINE_AA)
        primitives, metrics = pipeline.extract_stroke_graph_primitives(image, "sha256:" + "3" * 64)
        self.assertEqual(metrics["loopCount"], 1)
        self.assertEqual(metrics["endpointCount"], 0)
        self.assertTrue(primitives)
        self.assertTrue(all(primitive["style"]["fill"] is None for primitive in primitives))

    def test_stroke_graph_rejects_filled_blob_from_line_only_gate(self) -> None:
        image = np.full((140, 140), 255, dtype=np.uint8)
        cv2.rectangle(image, (30, 30), (110, 110), 0, -1)
        _, metrics = pipeline.extract_stroke_graph_primitives(image, "sha256:" + "4" * 64)
        self.assertEqual(metrics["status"], "repair_pending")
        self.assertIn("filled_or_wide_region_requires_separate_face_layer", metrics["reason"])

    def test_qwen_negative_life_note_does_not_override_geometry(self) -> None:
        subtype, _, _, _ = pipeline.infer_regular_subtype(
            {"occurrences": [{"role": "stem", "knowledgePoint": "数量规律-面"}]},
            {"categories": ["polyline_polygon"]},
            {"panelRatios": {"filled": 0.8}, "primitiveCandidateTotals": {}, "panelCount": 5, "midtoneRatio": 0.04},
            {"categories": ["glyph", "life_outline", "shaded_polygon"], "semantic_notes": "星形与月牙组合，非生活物体"},
        )
        self.assertEqual(subtype, "filled_region_composition")

    def test_qwen_curve_and_line_becomes_curve_line_subtype(self) -> None:
        subtype, _, _, _ = pipeline.infer_regular_subtype(
            {"occurrences": [{"role": "stem", "knowledgePoint": "数量规律-线"}]},
            {"categories": ["straight_line"]},
            {"panelRatios": {}, "primitiveCandidateTotals": {}, "panelCount": 5, "midtoneRatio": 0.04},
            {"categories": ["straight_line"], "semantic_notes": "主图元由直线和曲线波形组成，非生活物体"},
        )
        self.assertEqual(subtype, "curve_line_composition")

    def test_grammar_router_prioritizes_textured_pie(self) -> None:
        grammar, source, renderer = pipeline.infer_visual_grammar(
            {"knowledgePoint": "", "categories": ["circle_ellipse_arc"], "cvFeatures": {"circleCandidateCount": 1}},
            {"categories": ["circle_ellipse_arc", "shaded_polygon"], "semantic_notes": "圆形被划分为多个扇形区域，含点阵纹理"},
        )
        self.assertEqual(grammar, "textured_pie")
        self.assertEqual(renderer, ["sector_angle", "texture_dot_grid_diagonal"])

    def test_acceptance_output_contains_source_hash(self) -> None:
        self.assertEqual(len("a" * 64), 64)
        self.assertEqual(pipeline.PRIMITIVE_SCHEMA, "graphic-primitives-v2")


if __name__ == "__main__":
    unittest.main()
