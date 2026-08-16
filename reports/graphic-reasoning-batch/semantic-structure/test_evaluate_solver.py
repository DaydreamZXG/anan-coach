import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("evaluate_solver", HERE / "evaluate_solver.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class EvaluateSolverTests(unittest.TestCase):
    def test_answer_parser_accepts_only_option_labels(self):
        self.assertEqual("C", MODULE.answer({"answer": " c "}))
        self.assertIsNone(MODULE.answer({"answer": "E"}))
        self.assertIsNone(MODULE.answer(None))


if __name__ == "__main__":
    unittest.main()
