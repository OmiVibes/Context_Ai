import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app
from utils.session_store import SessionStore


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="context_operations_")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        workspace = root / "workspace"
        (workspace / "demo").mkdir(parents=True)
        index = root / "indices_store" / "demo"
        index.mkdir(parents=True)
        (index / "indices.json").write_text(json.dumps({"repo_id": "demo", "indexed_files": ["app.py"]}))
        self.store = SessionStore(root / "runtime" / "sessions.sqlite3")
        patches = [patch.object(app, "SESSION_STORE", self.store),
                   patch.object(app, "BASE_DIR", str(root)), patch.object(app, "WORKSPACE_ROOT", str(workspace)),
                   patch.object(app, "PROJECT_CONTEXT_DIR", str(root / "tool")),
                   patch.object(app, "PROFILE_DIR", str(root / "profiles")),
                   patch.object(app, "_models_from_service", return_value={"default_model": "small", "models": ["small"]})]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        app._SESSIONS.clear(); self.addCleanup(app._SESSIONS.clear)
        self.client = TestClient(app.app, raise_server_exceptions=False)

    def test_session_survives_memory_restart_with_controls_turns_and_sources(self):
        with patch.object(app, "rag_answer", return_value={"answer": "first answer", "sources": [{"file": "app.py", "chunk_id": "a"}]}):
            self.client.post("/ask", json={"session_id": "persist", "user": "Explain demo", "repo_id": "demo", "model": "small"})
        app._SESSIONS.clear()  # Simulates process-local state after API restart.
        restored = self.client.get("/sessions/persist").json()
        self.assertEqual(restored["repository"], "demo")
        self.assertEqual(restored["model"], "small")
        self.assertEqual(restored["history"][0]["assistant"], "first answer")
        self.assertEqual(restored["history"][0]["sources"][0]["file"], "app.py")

    def test_session_history_is_persistent_but_active_memory_history_is_bounded(self):
        with patch.dict("os.environ", {"CHAT_HISTORY_TURNS": "2"}), patch.object(app, "rag_answer", side_effect=lambda **kw: {"answer": kw["question"], "sources": []}):
            for text in ("one", "two", "three"):
                self.client.post("/ask", json={"session_id": "bounded", "user": text, "repo_id": "demo"})
            app._SESSIONS.clear()
            self.assertEqual([t["user"] for t in self.client.get("/sessions/bounded").json()["history"]], ["two", "three"])
        self.assertEqual(len(self.store.load("bounded")["history"]), 3)

    def test_new_chat_and_targeted_full_delete_are_isolated(self):
        self.store.save_session("one", {"repo_id": "demo", "model": "small"})
        self.store.append_turn("one", {"user": "u", "assistant": "a", "sources": []})
        self.store.save_session("two", {"repo_id": "other", "model": "small"})
        self.store.append_turn("two", {"user": "u2", "assistant": "a2", "sources": []})
        cleared = self.client.delete("/sessions/one").json()
        self.assertEqual(cleared["repository"], "demo")
        self.assertEqual(cleared["history"], [])
        self.assertEqual(self.store.load("two")["history"][0]["assistant"], "a2")
        self.assertTrue(self.client.delete("/sessions/two/data").json()["deleted"])
        self.assertEqual(self.store.load("two"), {})

    def test_health_ready_config_and_request_ids_are_safe(self):
        health = self.client.get("/health", headers={"X-Request-ID": "caller-123"})
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.headers["X-Request-ID"], "caller-123")
        response = Mock(status_code=200); response.json.return_value = {"status": "ready"}
        with patch.object(app.requests, "get", return_value=response), patch.dict("os.environ", {"GITHUB_TOKEN": "never-return-this-value"}):
            ready = self.client.get("/ready").json()
            config = self.client.get("/config").json()
        self.assertEqual(ready["api"], "ready")
        self.assertEqual(ready["database"], "ready")
        self.assertNotIn("never-return-this-value", json.dumps(config))
        self.assertTrue(config["github_configured"])

    def test_ready_degrades_without_llm_or_database_but_health_stays_live(self):
        with patch.object(app.SESSION_STORE, "health", return_value=False), patch.object(app.requests, "get", side_effect=app.requests.ConnectionError()):
            ready = self.client.get("/ready").json()
        self.assertEqual(ready["database"], "unavailable")
        self.assertEqual(ready["llm_service"], "unavailable")
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_session_store_corruption_is_a_bounded_error(self):
        with patch.object(app.SESSION_STORE, "load", side_effect=app.SessionStoreError("bad database")):
            response = self.client.get("/sessions/failure")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "session_storage_unavailable")
