**Context Assist remaining audit fixes - 5 September 2026**

Continued from pushed baseline `721daa5c9c1393378ce82b7a5e1edc0691a00a78`. Previous safety/error/MCP fixes were retained. No architecture redesign or next-phase product features were added.

| Validation | Previous | Current |
|---|---|---|
| Same 74 audit checks | 62 passed / 12 failed | **70 passed / 0 code failures / 4 externally blocked** |
| Deterministic regression suite | 42 passed / 0 failed | **65 passed / 0 failed** |

All eight code-related audit failures are resolved. Four live GitHub checks still cannot succeed because GitHub rejects the configured token. They are explicitly BLOCKED, not passed. No assertions were removed or weakened. In binary pass/non-pass terms this is 70/4; it is not 74/74. The original audit mixes smoke checks, behavioral assertions, fault injection and live integrations; these counts are not a coverage percentage.

**Each of the 12 baseline failures**

| Failure | Root cause and fix | Regression evidence | Final status |
|---|---|---|---|
| Fenced-code cleanup | A greedy regex deleted fenced code. The cleaner removes paired fence lines while preserving their code, indentation and newlines. Inline backticks and unclosed fences are retained. | Exact audit example, Python/JS/SQL, tilde fences, longer outer fences, prose, inline and unclosed examples. | PASS |
| Unicode preservation | The emoji regex covered unrelated Unicode ranges; NFKC and lossy decoding changed source values. Cleaning now preserves Unicode and whitespace; source/profile readers use strict UTF-8. | Hindi, Chinese, Japanese, accented Latin, arrows, check marks, emoji and combining marks survive read/clean/chunk/embedding input/vector persistence/retrieval; Unicode README metadata survives. | PASS |
| JSON password masking | Assignment regexes missed structured JSON keys and could consume unrelated text. Nested objects/lists are parsed and sensitive keys are redacted; config assignment masking preserves delimiters and adjacent fields. | password/passwd/token/api_key/apiKey/secret/access_token, nested structures, escaped strings, ordinary descriptions and existing recognizable token patterns. | PASS |
| Percent-word accuracy | The metric parser only recognized percent symbols or decimal fractions. It now accepts percent words before/after accuracy and avoids reading 0.5 percent as 50 percent. API indexing uses the shared parser. | Exact 94.8 percent failure, 95%, accuracy of 95%, 95 percent accuracy, decimal formats and actual index profile output. | PASS |
| Missing/invalid saved index | A matching profile fingerprint returned skipped even when vectors were unavailable. A skip now requires a validated load; missing, corrupt, inconsistent or unreadable artifacts trigger rebuilding. | Missing FAISS/metadata, invalid bytes, inconsistent counts, unreadable metadata, failed rebuild/retry, retrieval after recovery, README bytes preserved. | PASS |
| Fingerprint content detection | Size and integer mtime missed rapid edits. SHA-256 now hashes eligible relative paths and content using the same file selection as ingestion. | Same-size/same-time edits, adds/deletes/renames, unchanged content at different times/locations, ignored/generated files, no unnecessary rebuild. | PASS |
| Closed issues shown as active risks | The keyword heuristic did not inspect state. Only open non-PR issues are now considered active risks. | Mocked open bug, closed fixed bug and open pull request. | PASS |
| Malformed webhook signatures | Unchecked split raised ValueError. The verifier validates scheme and exact hex length before constant-time comparison. | Valid, tampered, missing, malformed, wrong scheme, nonhex and extra-separator signatures; 400/401 responses. | PASS |
| GitHub milestone fetch | Live GitHub rejects existing credentials. Existing credential-safe error handling remains correct. | Live authentication failure plus mocked authenticated success. | BLOCKED: external credential |
| GitHub risk fetch | Same live credential failure. | Live failure; successful mocked issue fetch/risk generation. | BLOCKED: external credential |
| UI Milestones button | The backend reports the same GitHub authentication failure. UI remains stable and displays a useful message. | Actual Streamlit AppTest button plus existing routing/error-display tests. | BLOCKED: external credential |
| UI Risks button | Same backend authentication blocker. | Actual button plus existing UI/error tests. | BLOCKED: external credential |

**Indexing and text-handling details**

- Fingerprints and ingestion share eligible-source rules. They exclude .git internals, virtual environments, dependencies, caches, generated chunk/index/profile data and cloned-repo/vector artifacts. Actual Git ignore rules (including negation) apply in both Git checkouts and archives containing .gitignore. Archive rules are interpreted using disposable Git metadata outside the source; the archive is never initialized or changed.
- Fingerprints use relative names and content, not mtime or absolute location. An ingestion-version prefix invalidates old fingerprints so an explicit reindex applies the repaired cleaning rules. Existing stores are not mass-rebuilt during this task; the local launcher still disables startup scanning.
- Vector loading verifies embedding shape, finite values, document/metadata counts and types. Invalid trusted local artifacts return unavailable so /index can rebuild; canonical path validation still raises on unsafe paths. Source repositories are never deleted during recovery. An embedding failure during recovery remains a real non-2xx error, not a successful index result.
- Existing whitespace-oriented chunk boundaries and retrieval ranking were not redesigned. Unicode character values survive the tested path, but the existing chunker still flattens some source formatting. Secret masking is best-effort: recognized JSON keys, common config assignments, known token signatures and opaque high-entropy tokens are covered; this is not a claim of detecting every possible secret format. Ordinary long identifiers are no longer treated as secrets merely for having high entropy.
- Invalid UTF-8 raises a decode error instead of silently removing bytes. No lossy ASCII conversion or errors="ignore" workaround was introduced.

**GitHub environment block and configuration**

Live read-only issue calls return GitHub authentication errors. The application uses the configured credential and does not retry anonymously. Error handling and successful logic are independently covered by mocked tests; the four blocked checks remain visible in the audit output and ledger.

During final verification, one milestone fetch also encountered a transient connectivity error and was correctly recorded as FAIL. The affected audit script was rerun without changing its assertions or broadening the BLOCKED classification; its earlier output remains in the ignored local `audit/run_connectivity_retry.log`.

Supply a valid token with access to the selected repository through `GITHUB_TOKEN` in the ignored project-root `.env`, or securely inject the variable into the process environment. Never put the token in source code, a repository URL, a command committed to Git, or a test fixture. Restart the API/UI processes after configuration changes. `/health/github` reports configuration presence only; it does not claim token validity. Then rerun all three audit scripts to verify live Milestones/Risks access. Existing credentials were not printed, replaced, or committed in this task.

The local configured model remains `llama3.2:1b`; no model/config changes were made in this task. API, separated LLM service and UI are available on local ports 8000, 9001 and 8501, with automatic API indexing disabled. Live embedding, indexing, MCP, generation, saved-index reload and Postman replay tests still pass. Passing smoke tests does not establish broad answer quality: the small model may add unsupported commentary, and richer grounding/citations remain future product work.

**Tests and reproduction**

The original 42 regression tests still pass. Three new test modules add 23 tests:

- `tests/test_content_edges.py`: 11 tests covering fences, Unicode, strict decoding, redaction and metrics.
- `tests/test_index_recovery.py`: 9 tests covering recovery, retries, incremental hashing and ignore rules.
- `tests/test_github_edges.py`: 3 tests covering active risks, authenticated mocked success and webhook signatures.

The earlier suite still verifies README/docs and local-edit preservation, traversal/junction rejection, architecture isolation, inference status propagation, default/fallback model behavior, session selection, GitHub errors and MCP stdio/custom/standard calls.

```powershell
.\venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\venv\Scripts\python.exe -X utf8 audit/run_audit.py
.\venv\Scripts\python.exe -X utf8 audit/check_integrations.py
.\venv\Scripts\python.exe -X utf8 audit/check_end_to_end.py
```

No runtime dependencies were added. Git is used for exact ignore-rule interpretation. All mutation/recovery tests use temporary repositories/directories. Raw result JSON, logs, generated indexes and temporary fixtures remain ignored. Audit scripts label only recognized live authentication/configuration failures as BLOCKED; other failures remain FAIL. No success assertions were relaxed. A zero audit-script exit alone does not mean all checks passed; read its result statuses.

**Files changed**

`app.py`; `app_processing/embeddings.py`; `app_processing/file_loader.py`; `app_processing/file_reader.py`; `github/webhook.py`; `rag/metrics_extractor.py`; `rag/risk.py`; `repo_profiles/extractor.py`; `utils/project_fingerprint.py`; `vector_store/store.py`; `audit/run_audit.py`; `audit/check_integrations.py`; `audit/PROJECT_AUDIT.md`; `README.md`; and the three new regression modules listed above.

The user's unrelated Postman edit is preserved and excluded from the commit. No .env, credentials, model files, virtual environments, caches, generated indexes, test artifacts or local logs are included. Remaining broader limitations (trusted pickle metadata, lack of API authentication/session expiry, first-page GitHub issue retrieval, heuristic milestones, answer grounding and citation detail) are outside this audit-fix task.

**All 74 audit checks**

| Check | Result |
|---|---|
| Python syntax | PASS |
| API health | PASS |
| API OpenAPI | PASS |
| LLM docs | PASS |
| UI HTTP | PASS |
| UI health | PASS |
| README LLM module import | PASS |
| ask greeting | PASS |
| ask empty | PASS |
| ask missing fields | PASS |
| index empty | PASS |
| index missing repo | PASS |
| index missing fields | PASS |
| unknown endpoint | PASS |
| technical question not mistaken for greeting | PASS |
| fenced source survives embedding cleanup | PASS |
| Unicode code preserved | PASS |
| assignment secret masking | PASS |
| JSON password masking | PASS |
| chunk length bound | PASS |
| empty chunk input | PASS |
| decimal accuracy extraction | PASS |
| percent-word accuracy extraction | PASS |
| real default LLM generation | PASS |
| missing model produces HTTP error | PASS |
| source/notebook ingestion and excluded files | PASS |
| architecture grounded in calculator fixture | PASS |
| real embeddings, vector persistence and retrieval | PASS |
| real indexing pipeline | PASS |
| index preserves README | PASS |
| repeat index skips unchanged input | PASS |
| third index skips unchanged input | PASS |
| deterministic metadata answer | PASS |
| real grounded RAG answer with sources | PASS |
| repo detection by name | PASS |
| repo detection by filename | PASS |
| session clarification and repo selection | PASS |
| session retains selected repository | PASS |
| missing vector index self-heals | PASS |
| fingerprint detects content changes | PASS |
| index rejects repository path traversal | PASS |
| embedding outage distinguishable from missing evidence | PASS |
| multi-repository response/source wiring | PASS |
| MCP custom tool discovery | PASS |
| MCP chat greeting | PASS |
| MCP unknown method reports JSON-RPC error | PASS |
| MCP standard initialize | PASS |
| MCP standard tools/call | PASS |
| Streamlit rendering and chat interaction | PASS |
| milestone label grouping | PASS |
| closed issues excluded from current risks | PASS |
| GitHub milestone issue fetch | BLOCKED |
| GitHub risk issue fetch | BLOCKED |
| webhook valid and tampered signatures | PASS |
| webhook malformed signature handling | PASS |
| fresh clone contains profile extractor | PASS |
| Postman route alignment | PASS |
| Git clone basic operation | PASS |
| Git sync preserves README and LICENSE | PASS |
| Git sync preserves local edits | PASS |
| Git sync isolated integration | PASS |
| UI milestones tab real button | BLOCKED |
| UI risks tab real button | BLOCKED |
| MCP rebuild missing inputs rejected | PASS |
| explicit frontend selection stays in one repo | PASS |
| original vector-store smoke script | PASS |
| GitHub change extraction and deduplication | PASS |
| installed smaller LLM fallback | PASS |
| complete MCP rebuild | PASS |
| MCP rebuilt profile retains project metadata | PASS |
| complete RAG with smaller installed model | PASS |
| RAG lazy reload from persisted MCP index | PASS |
| Postman live read/query replay HTTP checks | PASS |
| original RAG smoke script answer check | PASS |
