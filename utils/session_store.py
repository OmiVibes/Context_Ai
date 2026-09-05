"""Small SQLite persistence layer for UI-visible conversation state."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class SessionStoreError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("SESSION_DB_PATH", "runtime/sessions.sqlite3"))
        self._initialize()

    def _connect(self):
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error as exc:
            raise SessionStoreError("Session storage is unavailable") from exc

    def _initialize(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        selected_repository TEXT,
                        selected_model TEXT,
                        pending_question TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS chat_turns (
                        session_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        user_question TEXT NOT NULL,
                        assistant_answer TEXT NOT NULL,
                        repository TEXT,
                        model TEXT,
                        sources_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (session_id, sequence),
                        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                    );
                """)
        except (OSError, sqlite3.Error) as exc:
            raise SessionStoreError("Session storage could not be initialized") from exc

    def load(self, session_id, limit=None):
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT selected_repository, selected_model, pending_question FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
                if row is None:
                    return {}
                query = "SELECT user_question, assistant_answer, repository, model, sources_json FROM chat_turns WHERE session_id = ? ORDER BY sequence"
                turns = connection.execute(query, (session_id,)).fetchall()
        except sqlite3.Error as exc:
            raise SessionStoreError("Session storage is unavailable") from exc
        if limit is not None:
            turns = turns[-limit:]
        history = []
        for user, answer, repository, model, sources in turns:
            try:
                decoded = json.loads(sources)
            except (TypeError, ValueError):
                decoded = []
            history.append({"user": user, "assistant": answer, "repository": repository,
                            "model": model, "sources": decoded if isinstance(decoded, list) else []})
        session = {"history": history}
        if row[0] is not None:
            session["repo_id"] = row[0]
        if row[1] is not None:
            session["model"] = row[1]
        if row[2] is not None:
            session["question"] = row[2]
        return session

    def save_session(self, session_id, session):
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute("""
                    INSERT INTO sessions(session_id, selected_repository, selected_model, pending_question, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET selected_repository=excluded.selected_repository,
                    selected_model=excluded.selected_model, pending_question=excluded.pending_question, updated_at=excluded.updated_at
                """, (session_id, session.get("repo_id"), session.get("model"), session.get("question"), now, now))
        except sqlite3.Error as exc:
            raise SessionStoreError("Session storage is unavailable") from exc

    def append_turn(self, session_id, turn):
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute("INSERT OR IGNORE INTO sessions(session_id, created_at, updated_at) VALUES (?, ?, ?)", (session_id, now, now))
                sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM chat_turns WHERE session_id = ?", (session_id,)).fetchone()[0]
                connection.execute("""
                    INSERT INTO chat_turns(session_id, sequence, user_question, assistant_answer, repository, model, sources_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (session_id, sequence, turn["user"], turn["assistant"], turn.get("repository"), turn.get("model"),
                      json.dumps(turn.get("sources", []), ensure_ascii=False), now))
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise SessionStoreError("Session storage is unavailable") from exc

    def clear_history(self, session_id):
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM chat_turns WHERE session_id = ?", (session_id,))
        except sqlite3.Error as exc:
            raise SessionStoreError("Session storage is unavailable") from exc

    def delete(self, session_id):
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        except sqlite3.Error as exc:
            raise SessionStoreError("Session storage is unavailable") from exc

    def health(self):
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1")
            return True
        except SessionStoreError:
            return False
