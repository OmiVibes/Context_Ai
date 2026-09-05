# Operations and service readiness

The production path is:

Streamlit UI -> Project Context API -> SQLite session store -> vector retrieval ->
separate inference API -> local engine/model.

The API uses `SESSION_DB_PATH` for its local SQLite store. The default is
`runtime/sessions.sqlite3`, which is created automatically and excluded from Git.
SQLite contains only UI-visible session state: session ID, selected repository/model,
pending repository-selection question, and ordered user/assistant turns with source
metadata. It never stores prompts, retrieved chunk text, environment values, tokens,
or secrets. Queries are parameterized and foreign keys remove a session's turns when
the explicitly named session is permanently deleted.

Sessions survive API restart. `CHAT_HISTORY_TURNS` (default 10) bounds the turns
loaded into active memory and used to clarify a follow-up; the database can retain
older displayed turns. Retrieval remains fresh and repository evidence remains the
only source used for generation. **New Chat** (`DELETE /sessions/{id}`) clears turns
and pending selection while keeping selected repository/model. `DELETE
/sessions/{id}/data` permanently removes only that named session.

Endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | API process liveness; no external dependencies |
| `GET /ready` | Bounded dependency states: API, SQLite, LLM service, vector storage, GitHub configuration |
| `GET /config` | Safe non-secret operational settings |
| `GET /sessions/{id}` | Selected repository/model and active bounded visible history |
| `GET /sessions/{id}/history` | Same active bounded history for UI refresh |
| `PUT /sessions/{id}` | Validated repository/model selection |
| `DELETE /sessions/{id}` | New Chat behavior |
| `DELETE /sessions/{id}/data` | Explicit permanent deletion |
| `GET /models` | Proxy of the separated inference service's available local models |
| `GET /repositories` | Validated repository IDs and index state |

The LLM service has `GET /health`: `ready` means its local backend/model list is
reachable; `degraded` means the service is alive but its backend is unavailable.
`/ready` uses a two-second LLM-service probe. `/health` never probes SQLite, GitHub,
the vector store, or the LLM service, so it remains responsive during a dependency
outage. A GitHub token that is missing or invalid reports `blocked` without affecting
local indexing or RAG. If the LLM service is down, the UI, repository browser, and
indexing remain usable; an ask request retains its existing 503/504 inference error.

Every API response receives an `X-Request-ID`. Clients may provide one (up to 128
characters) or the API generates a UUID. The RAG HTTP client forwards it to the LLM
service. Logs record IDs, action, repository/model decisions, retrieval count,
context size, inference duration, result class, and HTTP status. They do not log
questions, prompts, repository chunks, credential values, or upstream error bodies.

`GET /config` exposes default model, `RAG_TOP_K`, `RAG_MAX_CONTEXT_CHARS`,
`CHAT_HISTORY_TURNS`, LLM service scheme/host/port, workspace directory name, and a
boolean GitHub configured state. It never reads out `.env` or secret values.
