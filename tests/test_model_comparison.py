import unittest
from unittest.mock import patch

from evaluation.runner import run_models


class ModelComparisonTests(unittest.TestCase):
    def test_models_are_isolated_and_one_failure_does_not_stop_comparison(self):
        def fake_run(*, mode, model, **kwargs):
            if model == "bad": raise RuntimeError("unavailable")
            return {"configuration": {"model": model}, "summary": {}}
        with patch("evaluation.runner.run", side_effect=fake_run):
            result = run_models(["small", "bad"])
        self.assertEqual(result["models"]["small"]["configuration"]["model"], "small")
        self.assertIn("error", result["models"]["bad"])
