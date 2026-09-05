"""Run a versioned RAG benchmark without changing production answer behavior.

Deterministic mode deliberately uses no embedding or inference service.  Live mode
uses the installed embedding backend and the existing separate LLM HTTP client.
"""
from __future__ import annotations

import argparse
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from app_processing.file_loader import load_repo_files
from rag.grounded import answer_from_results, positive_int
from rag.local_llm import generate_answer

from evaluation.dataset import load_dataset
from evaluation.metrics import (aggregate, citation_metrics, expected_rank, grounding_reasons,
                                hit_at_k, is_refusal, reciprocal_rank)
from evaluation.report import write_reports

ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = ROOT / "datasets" / "baseline.json"
DEFAULT_FIXTURES = ROOT / "fixtures"


def _tokens(text: str) -> set[str]:
    stop_words = {"a", "an", "are", "does", "how", "is", "of", "the", "to", "what", "which", "who", "in", "on", "for", "with", "this", "that"}
    return {token for token in re.findall(r"[a-z0-9_./-]+", text.lower().replace("_", " ")) if token not in stop_words}


class DeterministicRetriever:
    """A transparent lexical fixture retriever for CI pipeline validation only."""
    def __init__(self, fixtures_root: str | Path):
        self.fixtures_root = Path(fixtures_root)
        self._documents = {}

    def retrieve(self, repository: str, question: str, top_k: int) -> list[dict]:
        if repository not in self._documents:
            path = self.fixtures_root / repository
            if not path.is_dir():
                raise FileNotFoundError(f"fixture repository is missing: {repository}")
            self._documents[repository] = load_repo_files(str(path))
        terms = _tokens(question)
        results = []
        for document in self._documents[repository]:
            text = document["text"]
            overlap = len(terms.intersection(_tokens(text)))
            if not overlap:
                continue
            metadata = dict(document.get("metadata") or {})
            # Grounded prepare_context's threshold is 0.25, so normalize positive matches.
            results.append({"text": text, "metadata": metadata, "score": round(0.25 + overlap / max(1, len(terms)), 3)})
        return sorted(results, key=lambda item: (-item["score"], item["metadata"].get("file_path", "")))[:top_k]


class LiveRetriever:
    """Build in-memory fixture stores using the production loaders, chunks and embeddings."""
    def __init__(self, fixtures_root: str | Path):
        self.fixtures_root = Path(fixtures_root)
        self._stores = {}

    def _store(self, repository: str):
        if repository in self._stores:
            return self._stores[repository]
        from app_processing.chunker import chunk_text
        from app_processing.embeddings import embed_texts
        from vector_store.store import VectorStore
        directory = self.fixtures_root / repository
        if not directory.is_dir():
            raise FileNotFoundError(f"fixture repository is missing: {repository}")
        chunks = []
        for document in load_repo_files(str(directory)):
            chunks.extend(chunk_text(document["text"], document["metadata"]))
        if not chunks:
            raise RuntimeError(f"fixture repository has no indexable chunks: {repository}")
        store = VectorStore(embed_texts([chunk["text"] for chunk in chunks]),
                            [chunk["text"] for chunk in chunks], [chunk["metadata"] for chunk in chunks])
        self._stores[repository] = store
        return store

    def retrieve(self, repository: str, question: str, top_k: int) -> list[dict]:
        from app_processing.embeddings import embed_query
        store = self._store(repository)
        # Mirror production's existing query-variant retrieval, without persisting fixtures.
        results = []
        for variant in (question, f"{question} overview", f"{question} summary", f"{question} documentation"):
            results.extend(store.search(embed_query(variant), variant, top_k=top_k, threshold=0.25))
        return results

    def prepare(self, repositories: set[str]):
        """Build ephemeral fixture indexes before latency sampling begins."""
        for repository in sorted(repositories):
            self._store(repository)


def deterministic_generate(prompt: str) -> str:
    """Echo supplied evidence after its source header; never invent an answer."""
    marker = "]\n"
    if marker not in prompt:
        return ""
    evidence = prompt.split(marker, 1)[1].split("\n\n--- END REPOSITORY EVIDENCE ---", 1)[0]
    return evidence.strip()


@contextmanager
def configured(top_k: int | None, context_limit: int | None):
    old = {key: os.environ.get(key) for key in ("RAG_TOP_K", "RAG_MAX_CONTEXT_CHARS")}
    if top_k is not None:
        os.environ["RAG_TOP_K"] = str(top_k)
    if context_limit is not None:
        os.environ["RAG_MAX_CONTEXT_CHARS"] = str(context_limit)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def evaluate_case(case: dict, retriever, mode: str, model: str | None, top_k: int) -> dict:
    started = time.perf_counter()
    inference_seconds = 0.0
    error = None
    try:
        retrieval_started = time.perf_counter()
        results = retriever.retrieve(case["repository"], case["question"], top_k)
        retrieval_seconds = time.perf_counter() - retrieval_started

        def generate(prompt: str) -> str:
            nonlocal inference_seconds
            inference_started = time.perf_counter()
            try:
                return deterministic_generate(prompt) if mode == "deterministic" else generate_answer(prompt, model=model)
            finally:
                inference_seconds += time.perf_counter() - inference_started

        response = answer_from_results(case["question"], results, [case["repository"]], generate, False, lambda _: "Low")
        answer, sources = response["answer"], response.get("sources", [])
    except TimeoutError as exc:
        results, answer, sources, error = [], "", [], f"timeout: {exc}"
        retrieval_seconds = time.perf_counter() - started
    except Exception as exc:  # Preserve an individual failure in the report and continue the suite.
        results, answer, sources, error = [], "", [], f"{type(exc).__name__}: {exc}"
        retrieval_seconds = time.perf_counter() - started
    total_seconds = time.perf_counter() - started
    expected = case.get("expected_files", [])
    rank = expected_rank(results, expected) if case["answerable"] else None
    citation_hit, citation_precision = citation_metrics(sources, expected) if case["answerable"] else (None, None)
    reasons = grounding_reasons(case, answer, sources) if case["answerable"] and not error else ([] if not error else [error])
    refused = is_refusal(answer)
    categories = []
    if error:
        categories.append("timeout" if error.startswith("timeout:") else "inference_error")
    elif case["answerable"]:
        if rank is None:
            categories.append("retrieval_miss")
        elif rank > top_k:
            categories.append("weak_retrieval")
        if citation_hit == 0:
            categories.append("incorrect_citation")
        if reasons:
            categories.append("grounded_answer_mismatch")
    elif not refused:
        categories.append("unsupported_not_refused")
    return {"id": case["id"], "repository": case["repository"], "question": case["question"],
            "answerable": case["answerable"], "answer": answer, "sources": sources,
            "retrieved": [{"file": (item.get("metadata") or {}).get("file_path"), "score": item.get("score"),
                           "chunk_id": (item.get("metadata") or {}).get("chunk_id")} for item in results],
            "expected_rank": rank, "hit_at_k": hit_at_k(results, expected, top_k) if case["answerable"] else None,
            "mrr": reciprocal_rank(results, expected) if case["answerable"] else None,
            "citation_hit": citation_hit, "citation_precision": citation_precision,
            "refused": refused, "grounding_reasons": reasons, "failure_categories": categories,
            "latency": {"retrieval_seconds": round(retrieval_seconds, 6), "inference_seconds": round(inference_seconds, 6),
                        "total_seconds": round(total_seconds, 6)}}


def run(dataset_path: str | Path = DEFAULT_DATASET, fixtures_root: str | Path = DEFAULT_FIXTURES,
        mode: str = "deterministic", top_k: int | None = None, context_limit: int | None = None,
        model: str | None = None, output_dir: str | Path | None = None) -> dict:
    if mode not in {"deterministic", "live"}:
        raise ValueError("mode must be deterministic or live")
    cases = load_dataset(dataset_path)
    with configured(top_k, context_limit):
        effective_top_k = positive_int("RAG_TOP_K", 5)
        effective_context = positive_int("RAG_MAX_CONTEXT_CHARS", 3000)
        retriever = DeterministicRetriever(fixtures_root) if mode == "deterministic" else LiveRetriever(fixtures_root)
        if mode == "live":
            retriever.prepare({case["repository"] for case in cases})
        records = [evaluate_case(case, retriever, mode, model, effective_top_k) for case in cases]
    report = {"configuration": {"mode": mode, "model": model or ("deterministic-evidence-echo" if mode == "deterministic" else os.getenv("LLM_DEFAULT_MODEL", "service-default")),
                                "top_k": effective_top_k, "max_context_chars": effective_context, "dataset": str(Path(dataset_path))},
              "summary": aggregate(records, effective_top_k), "cases": records}
    if output_dir is not None:
        json_path, markdown_path = write_reports(report, output_dir)
        report["outputs"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Context AI retrieval, citations, refusals and grounding.")
    parser.add_argument("--mode", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--fixtures-root", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--context-limit", type=int)
    parser.add_argument("--model")
    parser.add_argument("--output-dir", default=str(ROOT / "results"))
    args = parser.parse_args()
    report = run(args.dataset, args.fixtures_root, args.mode, args.top_k, args.context_limit, args.model, args.output_dir)
    summary = report["summary"]
    print(f"{args.mode} evaluation: {summary['cases']} cases; Hit@{summary['retrieval']['k']}={summary['retrieval']['hit_at_k']}; failures={summary['failure_categories']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
