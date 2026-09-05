# Context AI

Context AI is a local developer assistant that indexes software repositories and answers project-specific questions with retrieval-augmented generation. It combines file loading, chunking, embeddings, vector search, repository profiling, MCP tooling, FastAPI endpoints, and a Streamlit UI.

## Features

- Index local or GitHub repositories into searchable chunks
- Generate embeddings and store vectors with FAISS
- Ask natural-language questions about indexed codebases
- Expose context tools through an MCP server
- Use FastAPI endpoints for indexing and querying; MCP tools for risks and milestones
- Provide a Streamlit interface for repository chat workflows
- Route prompts to local LLM backends such as Ollama

## Project Structure

```text
app.py                  FastAPI app for indexing and RAG queries
app_processing/         File loading, filtering, chunking, and embeddings
github/                 Repository sync and webhook helpers
llm_service/            Local LLM inference service
mcp/                    MCP JSON-RPC server and schemas
rag/                    Retrieval, prompt building, metrics, risks, milestones
ui/                     Streamlit frontend
utils/                  MCP client, tests, and project fingerprint helpers
vector_store/           FAISS vector store implementation
evaluation/             Versioned RAG benchmark, synthetic fixtures, and reports
```

## Requirements

- Python 3.10+
- Ollama, if using local LLM inference
- Dependencies listed in `requirements.txt`

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file for local configuration if needed. The `.env` file is intentionally ignored by git.

## Run The API

```bash
uvicorn app:app --host 127.0.0.1 --port 8000 --lifespan off
```

The main API starts from `app.py` and exposes repository indexing and RAG query endpoints.
`--lifespan off` disables automatic workspace indexing. Submit `/index` explicitly.
Indexing preserves source and documentation files. Repository IDs are single names,
not paths. Git sync refuses dirty repositories, divergent histories, or updates that
remove files; it never resets local changes. Apply such updates manually after review.

## Run The Streamlit UI

```bash
streamlit run ui/streamlit_app.py
```

Use the sidebar to provide a repository ID and GitHub repository URL, rebuild the index, and ask questions about the project.

## Run The LLM Service

```bash
uvicorn llm_service.server:app --host 127.0.0.1 --port 9001
```

The LLM service routes prompts to a configured local model backend.

Set configuration in the ignored root `.env` or the process environment:

```dotenv
LLM_DEFAULT_MODEL=qwen2.5:7b
# Optional: choose an already installed local model suitable for your memory.
# LLM_FALLBACK_MODEL=llama3.2:1b
LLM_TIMEOUT_SECONDS=120
LLM_CLIENT_TIMEOUT_SECONDS=270
```

The original default is retained. On this audit machine, `llama3.2:1b` worked where
`qwen2.5:7b` could not load. The fallback is disabled unless configured. It runs only
after model-unavailable or insufficient-memory failures and only if installed
locally; it never downloads a model or selects a cloud model. An explicit `model`
in `/generate` is always honored and disables fallback for that request.

Inference failures return `{"detail":{"code":"...","message":"..."}}` with
503 for unavailable models/services or memory limits, 504 for timeouts, and 500
for unexpected failures. `/ask` propagates these statuses instead of returning an
error as an answer. Keep the client timeout above two engine attempts plus model
discovery time if fallback is enabled. Restart services after changing configuration.

GitHub Milestones/Risks require `GITHUB_TOKEN` with access to the selected repository.
Missing/invalid tokens produce clear configuration errors; they are never printed
or committed, and invalid authentication is not retried anonymously. `/health/github`
reports configuration presence only, not token validity. The UI derives owner/name
from the entered GitHub URL.

MCP supports newline-delimited stdio `initialize`, `ping`, `tools/list`, `tools/call`,
and notifications, while retaining existing `call/ask_project`, `call/rebuild_index`,
`call/list_milestones`, and `call/risk_summary`. One-shot requests at EOF still work.
Errors now use JSON-RPC error objects with the original request ID. Standard tool
execution failures use `isError` content. Protocol references:
[lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

Run deterministic regression tests without a live model or GitHub credentials:

```powershell
.\venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

The integration audit commands and remaining limitations are documented in
`audit/PROJECT_AUDIT.md`. Architecture responses list observed repository files
and explicitly state that file names alone cannot establish responsibilities or
data flow. Sessions remember an explicitly selected repository in memory, allow
explicit switching, and replace stale pending questions; no persistent chat storage
has been added.

## Notes

The answering flow now keeps retrieved evidence and citations together:
repository -> chunking -> embeddings -> vector index -> retrieval -> bounded context
and prompt builder -> separated inference API -> engine/model -> grounded answer
with sources. Configure `RAG_TOP_K` (5), `RAG_MAX_CONTEXT_CHARS` (3000),
`LLM_API_URL` (`http://127.0.0.1:9001/generate`) and the inference service's
`LLM_DEFAULT_MODEL` as needed. See [RAG pipeline](rag/PIPELINE.md) for response
fields, configuration, compatibility, tests and file-level citation limitations.

The Streamlit product flow is **select repository → select model → ask → inspect
Sources / Evidence**. Repository and model choices are validated through the API;
the model selector only shows models reported by the separate inference service.
The sidebar displays index status and offers a selected-repository-only refresh using
the existing safe indexing pipeline. Chat turns are bounded in memory by
`CHAT_HISTORY_TURNS` (default 10); **New Chat** clears turns while retaining the
selected repository and model. See [UI controls](ui/CONTROLS.md) for API endpoints,
status meanings, follow-ups, and persistence limits.

Sessions now persist selected controls and visible chat turns in local SQLite across
API restarts. Configure `SESSION_DB_PATH` if the default `runtime/sessions.sqlite3`
does not suit deployment; runtime data remains ignored by Git. Use `/health` for API
liveness, `/ready` for bounded dependency state, and `/config` for safe operational
settings. [Operations](OPERATIONS.md) documents reset/delete behavior, degraded
service behavior, correlation IDs, and the complete endpoint contract.

Generated data such as virtual environments, caches, repository indexes, vector files, and local profiles are excluded from version control through `.gitignore`.

Index fingerprints hash eligible relative paths and source content with SHA-256;
timestamps alone never determine whether content changed. Ingestion and fingerprinting
share source/ignore rules. Git interprets `.gitignore` rules, including for archives
using disposable metadata outside the source directory. A matching fingerprint skips
indexing only if the persisted vector artifacts load and validate; missing or corrupt
artifacts are rebuilt without deleting source files. The repaired ingestion version
causes an explicit reindex of older profiles once, then unchanged repositories skip.

Source and profile text is read as strict UTF-8. Unicode is preserved, fenced code
content is retained, and invalid UTF-8 is reported rather than silently discarded.
Secret masking covers common JSON/config keys and known token signatures, but is
not a guarantee of detecting every secret format. Risks now include only open issues;
closed issues and pull requests are excluded from active risk summaries.

The audit distinguishes `PASS`, code `FAIL`, and externally `BLOCKED` checks. A rejected
GitHub credential remains blocked, never passed. Supply `GITHUB_TOKEN` securely through
the ignored `.env` or process environment, restart services, and rerun the audit to
verify live GitHub access. Do not paste or commit credentials into code or fixtures.

## RAG evaluation

The version-controlled synthetic benchmark in `evaluation/` measures retrieval Hit@K
and MRR, file-level citation hit/precision, deterministic grounding constraints,
unsupported-question refusal behavior, and latency. Run it without Ollama using
`.\venv\Scripts\python.exe -m evaluation.runner --mode deterministic`. Optional
`--mode live --model llama3.2:1b` uses the existing local embedding backend and
separate inference service. Generated reports are ignored; see
[`evaluation/README.md`](evaluation/README.md) for dataset fields, configuration
comparisons, interpretation, and limitations.
