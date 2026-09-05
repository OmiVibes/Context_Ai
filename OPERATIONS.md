# Operations

## Services and startup

Run the local services in this order from the repository root:

```powershell
# Ollama backend: http://127.0.0.1:11434
ollama serve

# Standalone inference service: http://127.0.0.1:9001
.\venv\Scripts\python.exe -m uvicorn llm_service.server:app --host 127.0.0.1 --port 9001

# Context Assist API: http://127.0.0.1:8000
.\venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000 --lifespan off

# Streamlit UI: default http://127.0.0.1:8501
.\venv\Scripts\python.exe -m streamlit run ui/streamlit_app.py
```

The API's `--lifespan off` mode avoids automatic workspace indexing. Index a repository explicitly through `POST /index` or the UI. RAG reaches inference through `LLM_API_URL` (default `http://127.0.0.1:9001/generate`); only the standalone inference service uses the Ollama backend.

## Readiness and diagnostics

| Check | Meaning |
|---|---|
| `GET /health` | API process liveness; does not wait for dependencies. |
| `GET /ready` | Bounded state for SQLite, vector storage, LLM service, and GitHub configuration. |
| `GET /config` | Non-secret effective operational settings. |
| `GET /models` | Available local models through the LLM service. |
| `GET /repositories` | Repository and index state. |
| `GET http://127.0.0.1:9001/health` | LLM service and Ollama-backend readiness. |

Use the evaluation preflight before a live benchmark:

```powershell
.\venv\Scripts\python.exe -m evaluation.diagnostics --model llama3.2:1b
```

It distinguishes an unreachable LLM service, unavailable Ollama backend, absent model, timeout, connection reset, and generation error. It also runs a small warm-up that is reported separately from benchmark latency.

## Sessions, request IDs, and logs

`SESSION_DB_PATH` defaults to `runtime/sessions.sqlite3`. It is created locally and ignored by Git. SQLite stores UI-visible session selections, pending repository-selection state, and ordered chat turns with source metadata. `CHAT_HISTORY_TURNS` (default `10`) bounds the active history used for a follow-up. `DELETE /sessions/{id}` implements New Chat by clearing turns while retaining the selected repository/model; `DELETE /sessions/{id}/data` permanently removes that named session.

Every API response includes `X-Request-ID`. A client can provide an ID up to 128 characters, otherwise the API creates one and forwards it to inference. Operational logs include IDs, action, repository/model decisions, retrieval count, context size, inference duration, result class, and status. They do not log questions, prompts, retrieved chunks, tokens, or upstream error bodies.

## Degraded operation and common recovery

- **Ollama unavailable:** the LLM service reports `degraded`; start `ollama serve`, verify a model with `ollama list`, then retry `/models` or diagnostics.
- **LLM service unavailable:** API health, indexing, repository browsing, and session management remain usable. Ask requests return their normal inference error; start the service on port 9001 and check `/health`.
- **Model unavailable:** install the selected local model (for example, `ollama pull llama3.2:1b`) or choose one shown by `GET /models`. An explicit model request does not silently fall back.
- **GitHub token unavailable or invalid:** GitHub-backed risks, milestones, and sync features report blocked/configuration state. Local repository indexing and RAG continue without it. Set `GITHUB_TOKEN` only in the environment or ignored `.env`, then restart affected processes.
- **SQLite unavailable:** check the writable parent of `SESSION_DB_PATH`; the readiness endpoint identifies the dependency state.

Keep `LLM_CLIENT_TIMEOUT_SECONDS` above the service's potential model-attempt time. The LLM engine timeout is `LLM_TIMEOUT_SECONDS`. Restart services after changing environment configuration.\n