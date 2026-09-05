# RAG evaluation benchmark

This package measures repeatable indicators around the existing Context AI RAG
pipeline. It does not change retrieval thresholds, prompts, chunking, or grounding
behavior. The fixture repositories are synthetic and intentionally small; no user
or private repository content is committed.

`datasets/baseline.json` is version controlled. Each JSON object has a unique `id`,
the fixture `repository`, a `question`, and `answerable`. Answerable cases label
`expected_files` and may add `expected_terms`, `expected_answer_contains`,
`forbidden_answer_terms`, tags, difficulty, and notes. Unsupported cases set
`answerable` to `false` and intentionally have no expected repository evidence.

Run the deterministic benchmark without Ollama:

```powershell
.\venv\Scripts\python.exe -m evaluation.runner --mode deterministic
```

It uses fixture-scoped lexical retrieval and a deterministic evidence echo while
still passing results through the production evidence/context/citation function.
It validates retrieval labels, context construction, citation propagation, refusal
routing, metrics, reports, and repository isolation. It is pipeline validation, not
a live LLM quality score.

Run the optional live benchmark when the local embedding backend and separate LLM
service are running:

```powershell
.\venv\Scripts\python.exe -m evaluation.runner --mode live --model llama3.2:1b
```

Live mode builds in-memory indexes from the same fixtures using the existing file
loader, chunker, embeddings, VectorStore, grounded prompt path, and HTTP inference
client. It writes no fixture index or repository data. Use `--top-k`,
`--context-limit`, and `--model` to compare configurations without editing source.
For example, compare `--top-k 3` and `--top-k 5` in separate saved report folders.

Compare installed models with the same dataset and RAG configuration:

```powershell
.\venv\Scripts\python.exe -m evaluation.runner --mode live --models llama3.2:1b,mistral:latest
```

Live multi-model mode runs a quick preflight (service, backend, model availability)
and one warm-up per model. Warm-up is reported separately and is excluded from
case latency. A failed preflight prevents a misleading benchmark; a failed model is
shown as incomplete while other models continue. Use
`.\venv\Scripts\python.exe -m evaluation.diagnostics --model llama3.2:1b` to
distinguish an unreachable service, unreachable Ollama backend, missing model,
timeout, connection reset, and generation error before running RAG.

Reports default to `evaluation/results/latest.json` and `latest.md`, which are
ignored by Git. JSON contains per-case retrieval ranks, answer, sources, failure
categories, and retrieval/inference/total latencies. Markdown presents aggregates.
Hit@K means a labeled expected file was retrieved among the first K results; MRR
rewards a higher-ranked first expected file. Citation hit rate is whether any source
matches an expected file; citation precision is the share of returned sources that
do. Unsupported refusal accuracy is the share of unsupported cases that returned
the centralized insufficient-evidence behavior. Grounding checks enforce case labels
such as required/forbidden terms and expected citations; these are indicators, not a
guarantee of factual correctness or semantic answer quality.

Failure categories distinguish `retrieval_miss`, `weak_retrieval`,
`unsupported_not_refused`, `incorrect_citation`, `grounded_answer_mismatch`,
`inference_error`, and `timeout` so later optimization can use evidence rather than
tuning against this benchmark blindly. Small fixture latency samples are operational
observations, not statistically strong performance conclusions.

## Evidence gating and calibration

Production retrieval still returns its configured top-K candidates. Before context
construction, `rag.grounded.select_evidence` accepts only finite candidates at or
above `RAG_EVIDENCE_MIN_SCORE` (default `0.25`). The hybrid VectorStore score is
sorted descending, so a higher score is more relevant. Candidates below
`RAG_EVIDENCE_STRONG_SCORE` (default `0.35`) need meaningful normalized query/text
overlap; lower-ranked candidates without overlap are also excluded below
`RAG_EVIDENCE_RELATIVE_SCORE` (default `0.72`) of the best score. This rejects weak
embedding noise before inference while retaining identifiers such as `add`,
snake_case, and filenames. `RAG_MAX_SOURCES` (default `3`) bounds returned citations.

The 15-case synthetic calibration selected this simple overlap safeguard rather than
raising a global retrieval threshold: it preserved deterministic supported evidence
acceptance and rejected all six expanded unsupported examples. This is a small,
synthetic calibration only; it should be rechecked against representative repositories
before changing defaults. Accepted chunks alone enter the prompt. Returned citations
deduplicate same-file chunks unless trusted source ranges differ, while allowing
multiple files for multi-file answers.

Live `llama3.2:1b` observations after the change: Hit@5 `1.0`, MRR `0.7963`, citation
hit `1.0`, citation precision `0.8333`, and unsupported refusal accuracy `1.0` across
15 cases. Exact lexical grounding was `0.6667`; the recorded mismatches were
evidence-consistent paraphrases or omitted literal syntax, so they remain visible
rather than being treated as model-quality improvements.
