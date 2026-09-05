# Grounded answers

Inspection of baseline `f1cf596` found four embedding query variants (question,
overview, summary, documentation), a saved VectorStore using cosine/BM25 hybrid
ranking, and an HTTP-only `rag/local_llm.py` client. Single-repository generation
discarded metadata before cutting concatenated text at 3,000 characters. Returned
sources could include excluded chunks. Multi-repository generation had no context
bound. Profile questions and architecture file inventories were deterministic.

The enhanced generation flow is:

Repository -> existing chunking -> embeddings -> saved vector store -> retrieval
-> ranked/deduplicated bounded evidence -> prompt builder -> HTTP inference API
-> engine router -> selected model -> answer with structured sources.

The retrieval backend, hybrid formula, four query variants and 0.25 relevance gate
are unchanged. Results are merged in descending score order. The configured top-k
applies per search and to the final merged evidence set. Multi-repository hits are
copied when attaching provenance instead of mutating shared result objects.

Context includes source headers and text, with both counted toward the character
budget. Whole chunks are preferred. If the highest-ranked chunk alone is too big,
only its text is excerpted and marked truncated; its metadata header stays intact.
If even a usable excerpt cannot fit, generation is skipped. Lower-ranked chunks
are not substituted when the next ranked chunk cannot fit. No chunk-store JSON is
read during answering.

The prompt requires repository-only answers, treats source content as data rather
than instructions, and places the question after the evidence. Empty, unusable or
below-threshold results return `Insufficient repository evidence to answer this
question.` with empty sources without calling generation. The existing HTTP client
retains timeout 504, unavailable service/model 503 and other inference failure 500
semantics. It omits model selection so the inference service retains its configured
default and existing fallback behavior.

Generated single-repository responses now include:

```json
{
  "answer": "The add function returns the sum of two numbers.",
  "repository": "calculator",
  "sources": [
    {
      "repository": "calculator",
      "repo_id": "calculator",
      "file": "calculator.py",
      "chunk_id": "stable-content-derived-id",
      "section": "raw",
      "score": 0.371
    }
  ]
}
```

Sources are supplied evidence, not a claim that every generated sentence has been
independently verified. They are always included for generated answers, including
when the legacy `show_sources` flag is false; that accepted flag remains compatible.
`show_confidence` still controls generated-answer confidence. The score is a hybrid
relevance value, not a probability. Multi-repository responses use a repository list
and each source identifies its repository. Existing API session selection, generic
greetings, MCP routing, and deterministic profile/architecture response shapes are
preserved. Those deterministic shortcuts do not claim retrieved-chunk citations.

Chunk IDs reuse stored IDs when present, otherwise derive stable SHA-256 identifiers
from repository, file, section, available range and text. Repeated hits are deduplicated
while separate files/repositories/ranges remain distinct. Existing indexes work without
reindexing. Missing file metadata is represented by null, never an invented filename.

Current indexing flattens whitespace and may reformat redacted JSON. It does not
retain an original-source position map, so this release provides file-level citations.
If an index already supplies valid start_line/end_line metadata it is carried through.
Adding trustworthy original line positions requires a separate ingestion mapping
change; no approximate line numbers are manufactured.

Configuration (environment variables):

| Variable | Default | Purpose |
|---|---|---|
| RAG_TOP_K | 5 | Positive maximum evidence chunks |
| RAG_MAX_CONTEXT_CHARS | 3000 | Positive evidence budget, including headers |
| LLM_API_URL | http://127.0.0.1:9001/generate | Separated inference endpoint |
| LLM_DEFAULT_MODEL | qwen2.5:7b | Inference-service default; local tested override is llama3.2:1b |
| LLM_FALLBACK_MODEL | unset | Existing explicitly configured installed fallback |
| LLM_CLIENT_TIMEOUT_SECONDS | 270 | HTTP client timeout |

The evidence budget excludes the question and fixed prompt instructions. Environment
configuration belongs in the ignored root .env or process environment. Restart services
after changes; a separately deployed inference service needs its own model environment.

Concise RAG logs record repositories, evidence counts, source IDs/files, context length,
service-default model/configuration hint, generation duration and error code. They do
not record question text, chunk content, prompts, credentials or upstream error bodies.
The service may select a fallback; the RAG log's model hint is not a claim of the model
actually used remotely.

Validation: 15 new deterministic tests cover real vector selection, full /ask-to-HTTP
requests, prompts, source alignment, context bounds, IDs/ranges, deduplication,
multi-repository provenance, insufficient evidence, transport/model errors, generic
queries, URL configuration and logging. All 65 prior tests remain passing (80 total).
A temporary repository was indexed using live embeddings and queried through a real
local API process and the separate inference service: add returned the sum with a
calculator.py source; an unrelated lunar-eclipse query returned insufficient evidence
without generation. Temporary repositories and raw logs/results are not committed.

Prompt grounding is not a formal guarantee against hallucinations or prompt injection.
Weak retrieval can still pass the existing heuristic gate, and the small model may
misinterpret supplied evidence. The live smoke test verifies the exercised examples,
not universal answer correctness. GitHub integration credentials remain an independent
external blocker; this change makes no new claim of passing those live checks.
