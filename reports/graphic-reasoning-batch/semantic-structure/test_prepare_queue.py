import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("semantic_prepare_queue", HERE / "prepare_queue.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PrepareQueueTests(unittest.TestCase):
    def test_boxes_are_row_major_despite_one_pixel_y_jitter(self):
        boxes = [
            {"x": 440, "y": 28, "width": 138, "height": 138},
            {"x": 108, "y": 28, "width": 138, "height": 139},
            {"x": 273, "y": 28, "width": 138, "height": 139},
            {"x": 274, "y": 189, "width": 138, "height": 138},
            {"x": 109, "y": 190, "width": 138, "height": 137},
        ]
        ordered = MODULE.row_major_boxes(boxes)
        self.assertEqual([(108, 28), (273, 28), (440, 28), (109, 190), (274, 189)], [(box["x"], box["y"]) for box in ordered])

    def test_rows_are_clustered_before_x_sort(self):
        boxes = [
            {"x": 300, "y": 100, "width": 80, "height": 80},
            {"x": 10, "y": 101, "width": 80, "height": 79},
            {"x": 15, "y": 10, "width": 80, "height": 80},
        ]
        rows = MODULE.cluster_box_rows(boxes)
        self.assertEqual([[(15, 10)], [(10, 101), (300, 100)]], [[(box["x"], box["y"]) for box in row] for row in rows])


if __name__ == "__main__":
    unittest.main()
