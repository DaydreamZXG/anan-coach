import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("prepare_run_queue", HERE / "prepare_run_queue.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PrepareRunQueueTests(unittest.TestCase):
    def test_all_jobs_are_blocked_or_explicitly_skipped(self):
        rows = MODULE.queue_rows(HERE / "inputs/index.jsonl")
        self.assertEqual(46, len(rows))
        self.assertEqual(43, sum(row["status"] == "blocked_waiting_gpu_ready" for row in rows))
        self.assertEqual(3, sum(row["status"] == "skipped_missing_marker" for row in rows))
        self.assertFalse(any(row["status"] in {"accepted", "success", "completed"} for row in rows))

    def test_first_five_cover_representative_grammars_in_fixed_order(self):
        rows = MODULE.queue_rows(HERE / "inputs/index.jsonl")
        self.assertEqual(
            [
                "line_curve_composition",
                "straight_line_network",
                "circle_composition",
                "closed_polygon_composition",
                "grid_partition",
            ],
            [row["regularSubtype"] for row in rows[:5]],
        )


if __name__ == "__main__":
    unittest.main()
