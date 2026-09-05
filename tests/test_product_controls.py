import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app


class ProductControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="context_controls_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for repo in ("calculator", "notes"):
            (self.workspace / repo).mkdir()
        indices = self.root / "indices_store"
        for repo in ("calculator", "notes"):
            folder = indices / repo
            folder.mkdir(parents=True)
            (folder / "indices.json").write_text(json.dumps({"repo_id": repo, "indexed_files": ["main.py"]}))
        patches = [patch.object(app, "BASE_DIR", str(self.root)),
                   patch.object(app, "WORKSPACE_ROOT", str(self.workspace)),
                   patch.object(app, "PROJECT_CONTEXT_DIR", str(self.root / "tool")),
                   patch.object(app, "PROFILE_DIR", str(self.root / "profiles")),
                   patch.object(app.VectorStore, "load", return_value=None),
                   patch.object(app, "_models_from_service", return_value={"default_model": "small", "models": ["small", "larger"]})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        app._SESSIONS.clear()
        app._INDEX_JOBS.clear()
        self.addCleanup(app._SESSIONS.clear)
        self.client = TestClient(app.app, raise_server_exceptions=False)

    def test_repository_list_and_stale_status(self):
        with patch.object(app, "compute_project_fingerprint", return_value="changed"):
            response = self.client.get("/repositories")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["repo_id"] for row in response.json()["repositories"]], ["calculator", "notes"])
        self.assertTrue(all(row["status"] == "not_indexed" for row in response.json()["repositories"]))

    def test_repository_status_reports_stale_when_an_index_fingerprint_changed(self):
        profile = self.root / "profiles" / "calculator"
        profile.mkdir(parents=True)
        (profile / "profile.json").write_text(json.dumps({"fingerprint": "before"}))
        with patch.object(app.VectorStore, "load", return_value=object()), patch.object(app, "compute_project_fingerprint", return_value="after"):
            rows = self.client.get("/repositories").json()["repositories"]
        self.assertEqual(next(row for row in rows if row["repo_id"] == "calculator")["status"], "stale")

    def test_session_selection_rejects_invalid_repo_and_model(self):
        good = self.client.put("/sessions/a", json={"repo_id": "calculator", "model": "small"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["repository"], "calculator")
        self.assertEqual(good.json()["model"], "small")
        self.assertEqual(self.client.put("/sessions/a", json={"repo_id": "../escape"}).status_code, 400)
        self.assertEqual(self.client.put("/sessions/a", json={"repo_id": "missing"}).status_code, 404)
        bad_model = self.client.put("/sessions/a", json={"model": "not-installed"})
        self.assertEqual(bad_model.status_code, 400)
        self.assertEqual(bad_model.json()["detail"]["code"], "model_not_available")

    def test_models_endpoint_and_selected_model_reaches_rag(self):
        self.assertEqual(self.client.get("/models").json()["models"], ["small", "larger"])
        with patch.object(app, "rag_answer", return_value={"answer": "ok", "sources": []}) as answer:
            response = self.client.post("/ask", json={"session_id": "a", "user": "Explain this", "repo_id": "calculator", "model": "larger"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(answer.call_args.kwargs["model"], "larger")

    def test_model_service_failure_is_a_bounded_api_error(self):
        with patch.object(app, "_models_from_service", side_effect=app.ServiceError("model_service_unavailable", "Cannot retrieve available inference models", 503)):
            response = self.client.get("/models")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "model_service_unavailable")

    def test_history_is_ordered_bounded_and_contains_sources(self):
        with patch.dict(os.environ, {"CHAT_HISTORY_TURNS": "2"}), patch.object(app, "rag_answer", side_effect=lambda **kw: {"answer": kw["question"], "sources": [{"file": "main.py", "chunk_id": kw["question"]}]}) as answer:
            for question in ("First topic", "Second topic", "Third topic"):
                self.client.post("/ask", json={"session_id": "history", "user": question, "repo_id": "calculator"})
            history = self.client.get("/sessions/history").json()["history"]
        self.assertEqual([turn["user"] for turn in history], ["Second topic", "Third topic"])
        self.assertEqual(history[-1]["sources"][0]["file"], "main.py")
        self.assertEqual(answer.call_count, 3)

    def test_follow_up_uses_previous_question_but_performs_fresh_retrieval(self):
        with patch.object(app, "rag_answer", return_value={"answer": "ok", "sources": []}) as answer:
            self.client.post("/ask", json={"session_id": "follow", "user": "How does authentication work?", "repo_id": "calculator"})
            self.client.post("/ask", json={"session_id": "follow", "user": "Where is that implemented?", "repo_id": "calculator"})
        self.assertEqual(answer.call_count, 2)
        self.assertIn("How does authentication work?", answer.call_args.kwargs["question"])
        self.assertIn("Where is that implemented?", answer.call_args.kwargs["question"])

    def test_reset_preserves_controls_and_clears_turns_and_pending_question(self):
        app._SESSIONS["reset"] = {"repo_id": "calculator", "model": "small", "question": "pending", "history": [{"user": "x"}]}
        response = self.client.delete("/sessions/reset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"session_id": "reset", "repository": "calculator", "model": "small", "history": []})

    def test_reindex_selected_repo_only_and_exposes_stages(self):
        with patch.object(app, "index_repo", return_value={"repo_id": "calculator", "action": "skipped"}) as index:
            response = self.client.post("/repositories/calculator/reindex")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(index.call_args.args[0].repo_id, "calculator")
        self.assertEqual(response.json()["progress"][-1], "complete")
        self.assertEqual(self.client.post("/repositories/missing/reindex").status_code, 404)

    def test_index_refresh_does_not_touch_source_files(self):
        readme = self.workspace / "calculator" / "README.md"
        source = self.workspace / "calculator" / "main.py"
        readme.write_bytes(b"# Keep documentation\r\n")
        source.write_text("# local edit\nvalue = 1\n", encoding="utf-8")
        before = (readme.read_bytes(), source.read_bytes())
        with patch.object(app, "index_repo", return_value={"repo_id": "calculator", "action": "skipped"}):
            self.client.post("/repositories/calculator/reindex")
        self.assertEqual((readme.read_bytes(), source.read_bytes()), before)

    def test_generic_question_still_bypasses_repository_and_model_services(self):
        response = self.client.post("/ask", json={"session_id": "greet", "user": "hello"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("answer", response.json())
