import unittest
from unittest.mock import patch

from evaluation.runner import run_models


class ModelComparisonTests(unittest.TestCase):
    def test_models_are_isolated_and_one_failure_does_not_stop_comparison(self):
        def fake_run(*, mode, model, **kwargs):
            if model == "bad": raise RuntimeError("unavailable")
            return {"configuration": {"model": model}, "summary": {}}
        with patch("evaluation.diagnostics.preflight", return_value={"missing_models": [], "error_category": None}), patch("evaluation.diagnostics.warmup", return_value={"success": True}), patch("evaluation.runner.run", side_effect=fake_run):
            result = run_models(["small", "bad"])
        self.assertEqual(result["models"]["small"]["configuration"]["model"], "small")
        self.assertIn("error", result["models"]["bad"])

    def test_preflight_failure_prevents_all_model_runs(self):
        with patch("evaluation.diagnostics.preflight", return_value={"error_category":"llm_service_unreachable", "missing_models": []}):
            result=run_models(["small"])
        self.assertEqual(result["models"]["small"]["stage"], "preflight")
