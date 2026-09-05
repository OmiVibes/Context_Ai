**Context Assist safety and correctness repair ? 5 September 2026**

The requested high-priority repairs are implemented. This remains a prototype with known lower-priority defects and a GitHub credential problem; it is not fully production-ready.

| Validation | Before | After |
|---|---|---|
| Original 74-check audit | 42 passed / 32 failed | 62 passed / 12 failed |
| New deterministic regression suite | Not present | 42 passed / 0 failed |

The 74 checks mix smoke tests, behavior assertions, failure injection and live integrations. Counts are not a coverage percentage or a count of independent root causes. No original audit check was removed. Two expectations were adapted to the intentionally corrected contracts: dirty Git sync may safely refuse with an exception, and embedding failure must raise a structured 503 rather than return an ordinary answer. GitHub success checks remain failures; they were not redefined as passing error-handling checks.

**Repairs completed**

- Indexing preserves README, LICENSE, documentation and source files. Documentation remains logically excluded by the existing loader. Source reads skip symlinks/Windows junctions that escape the selected repository.
- Shared Git sync detects staged, unstaged and untracked work, verifies origin/upstream, and uses fast-forward-only updates. It refuses upstream file deletions and prevents overwriting ignored local files. No reset, clean, forced checkout or recursive repository deletion remains in the sync implementations. The legacy backup.py sync wrapper now uses the same safe implementation; its other legacy endpoints were not modernized.
- Repository IDs reject Unix/Windows paths, drive-relative paths, encoded traversal and Windows special names. Canonical containment checks cover repository lookup, vector files, profile files and index/chunk output paths. Invalid HTTP IDs receive 400; MCP invalid parameters use JSON-RPC errors.
- Architecture responses no longer invent tumor/CNN workflows. They return observed source/configuration paths from the selected repository, and state that paths alone are insufficient to infer responsibilities or data flow. Empty repositories receive an explicit insufficient-evidence answer. Two unrelated repository fixtures prove isolation.
- Model/service unavailability and memory errors return structured 503 responses; timeouts return 504; unexpected failures return 500. The RAG client and /ask propagate these errors instead of presenting them as successful answers. Query embedding outages are also distinguishable from absent evidence.
- Package imports and the LLM launcher work from the project root. LLM_DEFAULT_MODEL selects the default; LLM_FALLBACK_MODEL optionally selects an installed local fallback. Fallback is allowed only for missing-model/memory failures on implicit default requests, never for explicitly requested models or cloud models. No downloads occur.
- Shared greeting detection avoids treating words like history as hi. API and MCP greetings work without a repository. New questions replace stale pending selections; successful selection clears pending state; explicit repo names override active selection and group-name matches. In-memory selection is isolated by session and is not persisted to disk.
- GitHub configuration is loaded consistently, requests have a timeout, and missing/invalid credentials yield useful structured messages without leaking tokens. Authentication is not bypassed. /health/github reports configuration presence only, not token validity. UI owner/repository values come from the entered URL.
- MCP supports standard initialize/version negotiation, ping, tools/list with schemas, tools/call with content/isError, notification silence, and JSON-RPC error codes/request IDs. Newline-delimited requests work without closing stdin; existing custom call/* methods and one-shot EOF requests still work. Streamlit reads subprocess pipes through communicate with a timeout and displays structured errors.
- The required repo_profiles/extractor.py is now tracked; generated profile data remains ignored. The extractor validates output paths and retains README metadata because sync preserves documentation.

MCP implementation references: [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports). This is the tools/stdio subset used by this project, not a claim of implementing every optional MCP feature or cancellation of an in-flight synchronous operation.

**Configuration and live validation**

The committed default remains qwen2.5:7b. This machine's ignored .env now sets LLM_DEFAULT_MODEL=llama3.2:1b, an already installed model verified by the earlier audit. Optional fallback is disabled in this local configuration; fallback behavior is covered with deterministic mocks. An earlier live rerun with the larger default plus optional fallback encountered an intermittent engine failure, so the final 74-check run uses the explicitly configured smaller default. No credential values were changed or committed.

With that configuration, live embeddings, API indexing of a temporary repository, MCP local-Git rebuild, grounded calculator answers with source metadata, persisted-index reload, and the original RAG smoke script complete. Passing the old RAG smoke script means no runtime error was returned; its printed answer is not a rigorous answer-quality evaluation. The smaller model can still add unsupported or unnecessary commentary. Broad correctness/grounding evaluation remains future work.

API, LLM and Streamlit were restarted and left running on 127.0.0.1 ports 8000, 9001 and 8501. API startup scanning remains disabled by its launcher (--lifespan off). All indexing/deletion/dirty-Git/security reproductions used temporary repositories. Existing repositories and the pre-existing Postman edit were preserved; that Postman edit is excluded from this commit.

**Exactly which original audit checks still fail**

| Check | Remaining cause |
|---|---|
| fenced source survives embedding cleanup | Embedding cleanup still removes fenced code; deferred preprocessing repair. |
| Unicode code preserved | Existing emoji regex removes some non-emoji Unicode, including Chinese. |
| JSON password masking | Existing masking patterns do not cover the JSON password fixture. |
| percent-word accuracy extraction | The documented 94.8 percent format remains unsupported. |
| missing vector index self-heals | Unchanged fingerprint can skip rebuilding a missing FAISS artifact. |
| fingerprint detects content changes | Same-size/same-timestamp content changes can be missed. |
| closed issues excluded from current risks | Closed fixed bugs still count as risks in the keyword heuristic. |
| GitHub milestone issue fetch | Existing token is rejected by GitHub; now a clear configuration error. |
| GitHub risk issue fetch | Same credential blocker. |
| webhook malformed signature handling | Malformed signature still raises ValueError; outside the requested integration fixes. |
| UI milestones tab real button | Live GitHub authentication still fails; UI displays the new clear message. |
| UI risks tab real button | Same credential blocker. |

Four failures reflect one external authentication blocker. A valid GITHUB_TOKEN with repository access is needed to verify successful Milestones/Risks operation. Mocked authentication-failure tests pass. No private-repository credentials were guessed, printed, replaced, or committed.

Other known limitations remain: hybrid search loops through all embeddings instead of querying FAISS; two profile layouts and CWD-relative stores coexist; pickle metadata assumes trusted local storage; the API has no authentication or session expiry; GitHub issue pagination/PR filtering and actual milestone retrieval remain incomplete; chunk formatting and citation scope are limited. No selector UI, persistent chat storage, richer citations, progress UI or other feature work was added.

**Regression tests added**

| File | Tests | Coverage |
|---|---:|---|
| tests/test_safety.py | 8 | Byte preservation, dirty Git, clean fast-forward, upstream deletion refusal, legacy invalid directory preservation, traversal, Windows junctions and source/storage containment. |
| tests/test_inference.py | 12 | Engine/HTTP/transport failures, status propagation, configurable default, explicit-model behavior, installed-only fallback, cloud exclusion and embedding outages. |
| tests/test_questions.py | 7 | Greetings, stale pending questions, explicit selection phrases, session isolation/switching, repo/group detection and architecture isolation. |
| tests/test_integrations.py | 13 | GitHub configuration/auth/timeout, MCP negotiation/discovery/custom and standard calls/errors/notifications, traversal and real stdio/EOF subprocess behavior. |
| tests/test_ui.py | 2 | URL-derived GitHub routing, structured error display and subprocess timeout cleanup. |

Tests use unittest, unittest.mock, temporary directories, FastAPI TestClient and Streamlit AppTest already available in the environment. No new runtime dependency was added.

```powershell
.\venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\venv\Scripts\python.exe -X utf8 audit/run_audit.py
.\venv\Scripts\python.exe -X utf8 audit/check_integrations.py
.\venv\Scripts\python.exe -X utf8 audit/check_end_to_end.py
```

The regression suite requires no live model or GitHub token. Audit scripts require local services and models, perform read-only GitHub calls, and record outcomes individually rather than exiting on the first failure. Inspect the reported outcomes; a zero audit-script exit is not an all-green assertion. Raw results/logs and generated test data are ignored and excluded from the commit. Git diff/secret/artifact checks and the final suite are run before committing.

**Files changed for this task**

- Safety/data access: app.py, backup.py, github/repo_sync.py, app_processing/file_loader.py, vector_store/store.py, repo_profiles/extractor.py, utils/repo_paths.py, .gitignore.
- Inference/errors: app_processing/embeddings.py, llm_service/server.py, llm_service/core.py, llm_service/engine_router.py, llm_service/engines/ollama.py, rag/local_llm.py, rag/core.py, utils/errors.py, run_llm_detached.py.
- Questions/architecture: rag/repo_structure.py, rag/repo_detector.py, rag/router.py, utils/questions.py, with session changes in app.py.
- GitHub/MCP/UI: github/api.py, rag/milestones.py, rag/risk.py, mcp/server.py, mcp/schemas.py, ui/streamlit_app.py.
- Documentation/tests: README.md, this report, the three repeatable audit scripts, and the five regression test files listed above.

**Final original-audit check ledger**

| Check | Result | Mode |
|---|---|---|
| Python syntax | PASS | deterministic |
| API health | PASS | live HTTP |
| API OpenAPI | PASS | live HTTP |
| LLM docs | PASS | live HTTP |
| UI HTTP | PASS | live HTTP |
| UI health | PASS | live HTTP |
| README LLM module import | PASS | deterministic |
| ask greeting | PASS | ASGI actual handlers |
| ask empty | PASS | ASGI actual handlers |
| ask missing fields | PASS | ASGI actual handlers |
| index empty | PASS | ASGI actual handlers |
| index missing repo | PASS | ASGI actual handlers |
| index missing fields | PASS | ASGI actual handlers |
| unknown endpoint | PASS | ASGI actual handlers |
| technical question not mistaken for greeting | PASS | deterministic |
| fenced source survives embedding cleanup | FAIL | deterministic |
| Unicode code preserved | FAIL | deterministic |
| assignment secret masking | PASS | deterministic |
| JSON password masking | FAIL | deterministic |
| chunk length bound | PASS | deterministic |
| empty chunk input | PASS | deterministic |
| decimal accuracy extraction | PASS | deterministic |
| percent-word accuracy extraction | FAIL | deterministic |
| real default LLM generation | PASS | live local qwen2.5:7b |
| missing model produces HTTP error | PASS | live HTTP |
| source/notebook ingestion and excluded files | PASS | deterministic |
| architecture grounded in calculator fixture | PASS | deterministic |
| real embeddings, vector persistence and retrieval | PASS | live local embedding model |
| real indexing pipeline | PASS | temporary fixture + live embeddings |
| index preserves README | PASS | deterministic |
| repeat index skips unchanged input | PASS | deterministic |
| third index skips unchanged input | PASS | deterministic |
| deterministic metadata answer | PASS | deterministic |
| real grounded RAG answer with sources | PASS | live embeddings + default LLM |
| repo detection by name | PASS | deterministic |
| repo detection by filename | PASS | deterministic |
| session clarification and repo selection | PASS | deterministic |
| session retains selected repository | PASS | deterministic |
| missing vector index self-heals | FAIL | deterministic |
| fingerprint detects content changes | FAIL | deterministic |
| index rejects repository path traversal | PASS | temporary fixture only |
| embedding outage distinguishable from missing evidence | PASS | fault injection |
| multi-repository response/source wiring | PASS | mock embeddings and LLM |
| MCP custom tool discovery | PASS | real subprocess |
| MCP chat greeting | PASS | real subprocess |
| MCP unknown method reports JSON-RPC error | PASS | real subprocess |
| MCP standard initialize | PASS | real subprocess |
| MCP standard tools/call | PASS | real subprocess |
| Streamlit rendering and chat interaction | PASS | Streamlit AppTest + real MCP |
| milestone label grouping | PASS | mock GitHub issues |
| closed issues excluded from current risks | FAIL | mock GitHub issues |
| GitHub milestone issue fetch | FAIL | live read-only GitHub |
| GitHub risk issue fetch | FAIL | live read-only GitHub |
| webhook valid and tampered signatures | PASS | deterministic |
| webhook malformed signature handling | FAIL | deterministic |
| fresh clone contains profile extractor | PASS | deterministic |
| Postman route alignment | PASS | deterministic |
| Git clone basic operation | PASS | fixture |
| Git sync preserves README and LICENSE | PASS | fixture |
| Git sync preserves local edits | PASS | fixture |
| Git sync isolated integration | PASS | fixture |
| UI milestones tab real button | FAIL | AppTest + real MCP/GitHub |
| UI risks tab real button | FAIL | AppTest + real MCP/GitHub |
| MCP rebuild missing inputs rejected | PASS | real subprocess, no Git writes |
| explicit frontend selection stays in one repo | PASS | fixture |
| original vector-store smoke script | PASS | fixture |
| GitHub change extraction and deduplication | PASS | fixture |
| installed smaller LLM fallback | PASS | live local llama3.2:1b; no config change |
| complete MCP rebuild | PASS | temporary local Git + real embeddings |
| MCP rebuilt profile retains project metadata | PASS | temporary local Git fixture |
| complete RAG with smaller installed model | PASS | real embeddings/retrieval/LLM; model override in audit process only |
| RAG lazy reload from persisted MCP index | PASS | real disk reload/embeddings/LLM; audit model override |
| Postman live read/query replay HTTP checks | PASS | real HTTP; temporary session IDs |
| original RAG smoke script answer check | PASS | real default pipeline; no indexing writes |
