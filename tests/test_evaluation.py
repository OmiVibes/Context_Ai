import json
import tempfile
import unittest
from pathlib import Path

from evaluation.dataset import load_dataset
from evaluation.metrics import (citation_metrics, grounding_reasons, hit_at_k, is_refusal,
                                reciprocal_rank)
from evaluation.report import write_reports
from evaluation.runner import DEFAULT_DATASET, DEFAULT_FIXTURES, DeterministicRetriever, evaluate_case, run


class EvaluationTests(unittest.TestCase):
    def test_dataset_loads_versioned_cases(self):
        cases = load_dataset(DEFAULT_DATASET)
        self.assertEqual(len(cases), 15)
        self.assertEqual({case["repository"] for case in cases}, {"calculator_repo", "service_repo", "data_pipeline_repo"})

    def test_dataset_rejects_invalid_case(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text('[{"id":"bad","repository":"x","question":"q","answerable":"yes"}]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "answerable"):
                load_dataset(path)

    def test_dataset_rejects_unsupported_expected_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text('[{"id":"bad","repository":"x","question":"q","answerable":false,"expected_files":["x.py"]}]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_dataset(path)

    def test_hit_at_k_and_mrr(self):
        results = [{"metadata": {"file_path": "wrong.py"}}, {"metadata": {"file_path": "right.py"}}]
        self.assertEqual(hit_at_k(results, ["right.py"], 2), 1.0)
        self.assertEqual(hit_at_k(results, ["right.py"], 1), 0.0)
        self.assertEqual(reciprocal_rank(results, ["right.py"]), 0.5)

    def test_citation_hit_and_precision(self):
        hit, precision = citation_metrics([{"file": "right.py"}, {"file": "extra.py"}], ["right.py"])
        self.assertEqual(hit, 1.0)
        self.assertEqual(precision, 0.5)

    def test_refusal_detection_accepts_equivalent_safe_wording(self):
        self.assertTrue(is_refusal("There is insufficient repository evidence to answer this question."))
        self.assertFalse(is_refusal("The answer is in app.py."))

    def test_grounding_checks_required_forbidden_and_citation(self):
        case = {"expected_files": ["answer.py"], "expected_terms": ["symbolic"], "forbidden_answer_terms": ["invented"]}
        reasons = grounding_reasons(case, "An invented value", [{"file": "wrong.py"}])
        self.assertEqual(len(reasons), 3)

    def test_deterministic_runner_has_no_failures(self):
        report = run(mode="deterministic", output_dir=None)
        self.assertEqual(report["summary"]["failure_categories"], {})
        self.assertEqual(report["summary"]["retrieval"]["hit_at_k"], 1.0)
        self.assertEqual(report["summary"]["grounding"]["unsupported_refusal_accuracy"], 1.0)

    def test_report_generation_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as temp:
            report = run(mode="deterministic", output_dir=None)
            json_path, markdown_path = write_reports(report, temp)
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["summary"]["cases"], 15)
            self.assertIn("## Metrics", markdown_path.read_text(encoding="utf-8"))

    def test_latency_fields_are_present(self):
        report = run(mode="deterministic", output_dir=None)
        fields = report["cases"][0]["latency"]
        self.assertEqual(set(fields), {"retrieval_seconds", "inference_seconds", "total_seconds"})
        self.assertGreaterEqual(fields["total_seconds"], fields["inference_seconds"])

    def test_config_overrides_are_reported(self):
        report = run(mode="deterministic", top_k=3, context_limit=700, output_dir=None)
        self.assertEqual(report["configuration"]["top_k"], 3)
        self.assertEqual(report["configuration"]["max_context_chars"], 700)

    def test_repository_scope_prevents_cross_fixture_leakage(self):
        retriever = DeterministicRetriever(DEFAULT_FIXTURES)
        results = retriever.retrieve("calculator_repo", "Which port does the service use?", 5)
        self.assertFalse(any(item["metadata"].get("file_path") == "config.py" for item in results))

    def test_failure_category_marks_unsupported_not_refused(self):
        class BadRetriever:
            def retrieve(self, *args):
                return [{"text": "The Sun is a star.", "metadata": {"file_path": "fake.py"}, "score": .9}]
        record = evaluate_case({"id": "bad", "repository": "repo", "question": "Which planet?", "answerable": False},
                               BadRetriever(), "deterministic", None, 5)
        self.assertIn("unsupported_not_refused", record["failure_categories"])


if __name__ == "__main__":
    unittest.main()
