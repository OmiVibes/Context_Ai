# Repository, model, and chat controls

The Streamlit UI uses the Project Context API for product controls. It does not call
Ollama directly. The flow is:

Select repository -> select available model -> ask -> fresh vector retrieval ->
bounded repository evidence -> separated inference service -> answer and sources.

`GET /repositories` returns validated repository IDs and an index state. `indexed`
means a usable persisted vector store is present; `not_indexed` means it is absent;
`stale` means the eligible-source fingerprint changed; `indexing`, `complete`, and
`failed` are the stage states for an API refresh. The UI never refreshes an index
merely because it renders. **Refresh Index** calls
`POST /repositories/{repo_id}/reindex` only for the selected validated ID and uses
the existing safe update-only indexing flow. It has no Git cleanup or source-file
deletion step. Stages are deliberately descriptive rather than fabricated percent
values.

`GET /models` proxies the separate inference service's local-model list and default.
The UI uses `PUT /sessions/{session_id}` to store a selected model or repository.
An unavailable model is rejected before it is sent to inference. A selected model is
passed through the existing RAG HTTP client to the inference API; the API service,
not Streamlit or RAG, owns engine/model execution and fallback behavior.

Session state is intentionally process-local and held in memory: selected repository,
selected model, pending repository selection, and up to `CHAT_HISTORY_TURNS` ordered
turns (default 10). A turn stores the user question, returned answer, repository,
model, and source metadata, never the full retrieved prompt/context or credentials.
After a process restart, start a new chat. **New Chat** clears turns and a pending
question but retains the selected repository and model.

For referential follow-ups such as “Where is that implemented?”, the latest user
question can clarify the fresh retrieval query. Previous assistant output is never
used as repository evidence; vector retrieval still runs for every repository answer.
Generic greetings remain independent of repository selection.

Each answer exposes a collapsible **Sources / Evidence** section. It lists only
retrieved chunks that were sent to inference, deduplicated by repository/file/chunk
ID, with file, section, chunk ID, score, and an original line range only when one was
already supplied by indexing. The UI does not show hidden prompt text or entire
retrieved chunks.
