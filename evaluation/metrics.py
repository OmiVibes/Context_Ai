"""Deterministic evaluation metrics; they do not judge model semantics."""
from __future__ import annotations

import re
from statistics import mean, median

from rag.grounded import INSUFFICIENT


def source_files(sources: list[dict]) -> list[str]:
    return [source.get("file") for source in sources if isinstance(source, dict) and isinstance(source.get("file"), str)]


def expected_rank(results: list[dict], expected_files: list[str]) -> int | None:
    expected = set(expected_files)
    for position, result in enumerate(results, 1):
        if (result.get("metadata") or {}).get("file_path") in expected:
            return position
    return None


def hit_at_k(results: list[dict], expected_files: list[str], k: int) -> float:
    rank = expected_rank(results, expected_files)
    return float(rank is not None and rank <= k)


def reciprocal_rank(results: list[dict], expected_files: list[str]) -> float:
    rank = expected_rank(results, expected_files)
    return 0.0 if rank is None else 1.0 / rank


def citation_metrics(sources: list[dict], expected_files: list[str]) -> tuple[float, float]:
    actual = source_files(sources)
    expected = set(expected_files)
    hit = float(bool(expected.intersection(actual)))
    precision = 0.0 if not actual else sum(file in expected for file in actual) / len(actual)
    return hit, precision


def is_refusal(answer: str) -> bool:
    normalized = " ".join((answer or "").lower().split())
    return normalized == " ".join(INSUFFICIENT.lower().split()) or bool(re.search(
        r"(?:insufficient|not enough|cannot find).{0,40}(?:repository )?(?:evidence|information)", normalized))


def grounding_reasons(case: dict, answer: str, sources: list[dict]) -> list[str]:
    """Check declared case constraints, without treating word overlap as answer truth."""
    answer_lower = (answer or "").lower()
    reasons = []
    required = case.get("expected_answer_contains", case.get("expected_terms", []))
    absent = [term for term in required if term.lower() not in answer_lower]
    if absent:
        reasons.append("missing_expected_terms:" + ",".join(absent))
    forbidden = [term for term in case.get("forbidden_answer_terms", []) if term.lower() in answer_lower]
    if forbidden:
        reasons.append("forbidden_answer_terms:" + ",".join(forbidden))
    expected = case.get("expected_files", [])
    if expected and not set(expected).intersection(source_files(sources)):
        reasons.append("citation_missing_expected_file")
    return reasons


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {"mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "sample_size": 0}
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * .95 + .999999)) - 1))
    return {"mean_ms": round(mean(values) * 1000, 3), "median_ms": round(median(values) * 1000, 3),
            "p95_ms": round(ordered[index] * 1000, 3), "sample_size": len(values)}


def aggregate(records: list[dict], top_k: int) -> dict:
    supported = [record for record in records if record["answerable"]]
    unsupported = [record for record in records if not record["answerable"]]
    average = lambda values: round(sum(values) / len(values), 4) if values else None
    return {
        "cases": len(records), "repositories": sorted({record["repository"] for record in records}),
        "retrieval": {"hit_at_k": average([record["hit_at_k"] for record in supported]),
                      "mrr": average([record["mrr"] for record in supported]), "k": top_k,
                      "evaluated_cases": len(supported)},
        "citations": {"hit_rate": average([record["citation_hit"] for record in supported]),
                      "precision": average([record["citation_precision"] for record in supported]),
                      "evaluated_cases": len(supported)},
        "grounding": {"supported_answer_score": average([float(not record["grounding_reasons"]) for record in supported]),
                      "unsupported_refusal_accuracy": average([float(record["refused"]) for record in unsupported]),
                      "supported_cases": len(supported), "unsupported_cases": len(unsupported)},
        "latency": {"retrieval": latency_summary([record["latency"]["retrieval_seconds"] for record in records]),
                    "inference": latency_summary([record["latency"]["inference_seconds"] for record in records]),
                    "total": latency_summary([record["latency"]["total_seconds"] for record in records])},
        "failure_categories": _failure_counts(records),
    }


def _failure_counts(records: list[dict]) -> dict:
    counts = {}
    for record in records:
        for category in record["failure_categories"]:
            counts[category] = counts.get(category, 0) + 1
    return counts
