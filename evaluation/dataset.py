"""Versioned benchmark dataset loading and validation."""
from __future__ import annotations

import json
from pathlib import Path


REQUIRED = {"id", "repository", "question", "answerable"}
OPTIONAL = {
    "expected_files", "expected_terms", "expected_sections", "expected_chunk_ids",
    "expected_answer_contains", "forbidden_answer_terms", "tags", "difficulty", "notes",
}


def load_dataset(path: str | Path) -> list[dict]:
    """Load JSON or JSONL cases and reject ambiguous benchmark labels early."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix == ".jsonl":
        cases = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        cases = json.loads(text)
    if not isinstance(cases, list):
        raise ValueError("benchmark dataset must be a JSON array or JSONL records")
    seen = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        missing = REQUIRED - case.keys()
        if missing:
            raise ValueError(f"case {index} missing required fields: {', '.join(sorted(missing))}")
        unknown = set(case) - REQUIRED - OPTIONAL
        if unknown:
            raise ValueError(f"case {case.get('id', index)!r} has unknown fields: {', '.join(sorted(unknown))}")
        if not isinstance(case["id"], str) or not case["id"].strip() or case["id"] in seen:
            raise ValueError(f"case {index} needs a unique non-empty string id")
        if not isinstance(case["repository"], str) or not case["repository"].strip():
            raise ValueError(f"case {case['id']!r} needs a repository")
        if not isinstance(case["question"], str) or not case["question"].strip():
            raise ValueError(f"case {case['id']!r} needs a question")
        if not isinstance(case["answerable"], bool):
            raise ValueError(f"case {case['id']!r} answerable must be true or false")
        for field in ("expected_files", "expected_terms", "expected_sections", "expected_chunk_ids",
                      "expected_answer_contains", "forbidden_answer_terms", "tags"):
            if field in case and (not isinstance(case[field], list) or not all(isinstance(v, str) for v in case[field])):
                raise ValueError(f"case {case['id']!r} {field} must be a list of strings")
        if not case["answerable"] and any(case.get(key) for key in ("expected_files", "expected_terms", "expected_answer_contains")):
            raise ValueError(f"unsupported case {case['id']!r} cannot declare expected evidence or answer terms")
        seen.add(case["id"])
    return cases
