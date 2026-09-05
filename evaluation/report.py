"""Write portable JSON and concise Markdown reports."""
from __future__ import annotations

import json
from pathlib import Path


def write_reports(report: dict, output_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = directory / "latest.json", directory / "latest.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = report["summary"]
    lines = ["# Context AI RAG evaluation", "", f"Mode: `{report['configuration']['mode']}`", "",
             "## Configuration", "", "| Setting | Value |", "| --- | --- |"]
    for key in ("model", "top_k", "max_context_chars", "dataset"):
        lines.append(f"| {key} | {report['configuration'].get(key)} |")
    lines += ["", "## Dataset", "", f"- Cases: {summary['cases']}", f"- Repositories: {', '.join(summary['repositories'])}",
              "", "## Metrics", "", f"- Retrieval Hit@{summary['retrieval']['k']}: {summary['retrieval']['hit_at_k']}",
              f"- MRR: {summary['retrieval']['mrr']}", f"- Citation hit rate: {summary['citations']['hit_rate']}",
              f"- Citation precision: {summary['citations']['precision']}",
              f"- Supported-answer grounding score: {summary['grounding']['supported_answer_score']}",
              f"- Unsupported refusal accuracy: {summary['grounding']['unsupported_refusal_accuracy']}", "", "## Latency", "",
              "| Stage | Mean ms | Median ms | p95 ms | Samples |", "| --- | ---: | ---: | ---: | ---: |"]
    for stage, values in summary["latency"].items():
        lines.append(f"| {stage} | {values['mean_ms']} | {values['median_ms']} | {values['p95_ms']} | {values['sample_size']} |")
    failures = [record for record in report["cases"] if record["failure_categories"]]
    lines += ["", "## Failures", ""]
    lines += ([f"- `{record['id']}`: {', '.join(record['failure_categories'])}" for record in failures]
              if failures else ["- None"])
    lines += ["", "Deterministic mode validates the fixture-scoped pipeline; it is not a measure of live model quality."]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def write_model_comparison(comparison: dict, output_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(output_dir); directory.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = directory / "models.json", directory / "models.md"
    json_path.write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
    lines = ["# Model comparison", "", "| Model | Hit@K | Citation precision | Refusal accuracy | Median inference ms | Failures |", "| --- | ---: | ---: | ---: | ---: | --- |"]
    for name, report in comparison["models"].items():
        if "error" in report:
            lines.append(f"| {name} | - | - | - | - | {report['error']} |"); continue
        summary = report["summary"]
        lines.append(f"| {name} | {summary['retrieval']['hit_at_k']} | {summary['citations']['precision']} | {summary['grounding']['unsupported_refusal_accuracy']} | {summary['latency']['inference']['median_ms']} | {summary['failure_categories']} |")
    lines += ["", "Models use the same dataset and RAG configuration. Small-sample latency is indicative only."]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
