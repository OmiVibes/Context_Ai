import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = PROJECT_ROOT / "mcp" / "server.py"
API_URL = os.getenv("CONTEXT_ASSIST_API_URL", "http://127.0.0.1:8000")
DEFAULT_REPO_URL = "https://github.com/Dharani-Barigeda/facemask-detector.git"


def call_api(method, path, payload=None):
    try:
        response = requests.request(method, API_URL + path, json=payload, timeout=30)
        data = response.json()
        if response.status_code >= 400:
            detail = data.get("detail", data)
            return {"error": detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)}
        return data
    except (requests.RequestException, ValueError):
        return {"error": "Cannot reach the Project Context API"}


def call_mcp(payload):
    proc = subprocess.Popen([sys.executable, str(MCP_SERVER)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, _ = proc.communicate(json.dumps(payload), timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {"error": "Repository operation timed out"}
    if not stdout.strip():
        return {"error": "Empty response from MCP"}
    try:
        response = json.loads(stdout)
        if isinstance(response.get("error"), dict):
            response["error"] = response["error"].get("message", "MCP operation failed")
        return response
    except json.JSONDecodeError:
        return {"error": "Invalid JSON from MCP"}


def show_sources(sources):
    if not sources:
        return
    with st.expander("Sources / Evidence"):
        seen = set()
        for source in sources:
            key = (source.get("repository"), source.get("file"), source.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            line = (f" lines {source['start_line']}–{source['end_line']}"
                    if "start_line" in source and "end_line" in source else "")
            st.markdown(f"- `{source.get('file') or 'Unknown file'}`{line} · score {source.get('score', 0):.3f}")
            st.caption(f"Section: {source.get('section') or 'n/a'} · chunk: {source.get('chunk_id')}")


st.set_page_config(page_title="Project Context AI", layout="wide")
st.title("Project Context AI")
st.caption("Select a repository and model, then ask grounded questions with evidence.")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
session_id = st.session_state.session_id
session = call_api("GET", f"/sessions/{session_id}")
repos_response = call_api("GET", "/repositories")
models_response = call_api("GET", "/models")
repositories = repos_response.get("repositories", []) if "error" not in repos_response else []
repo_ids = [item["repo_id"] for item in repositories]
models = models_response.get("models", []) if "error" not in models_response else []

with st.sidebar:
    st.header("Chat controls")
    if st.button("New Chat"):
        call_api("DELETE", f"/sessions/{session_id}")
        st.rerun()
    if repo_ids:
        current_repo = session.get("repository") if "error" not in session else None
        repo_index = repo_ids.index(current_repo) if current_repo in repo_ids else 0
        selected_repo = st.selectbox("Repository", repo_ids, index=repo_index)
        selected_info = next(item for item in repositories if item["repo_id"] == selected_repo)
        st.caption(f"Index status: {selected_info['status']} · {selected_info['stage']}")
        if selected_repo != current_repo:
            changed = call_api("PUT", f"/sessions/{session_id}", {"repo_id": selected_repo})
            if "error" in changed:
                st.error(changed["error"])
            else:
                st.rerun()
        if st.button("Refresh Index"):
            with st.status("Refreshing selected repository index…", expanded=True) as status:
                st.write("Scanning files → cleaning → chunking → embedding → saving index")
                indexed = call_api("POST", f"/repositories/{selected_repo}/reindex")
                if "error" in indexed:
                    status.update(label="Index refresh failed", state="error")
                    st.error(indexed["error"])
                else:
                    status.update(label=f"Index {indexed.get('action', indexed.get('status', 'complete'))}", state="complete")
                    st.rerun()
    else:
        selected_repo = None
        st.info("No indexed repositories are available.")
    if models:
        current_model = session.get("model") if "error" not in session else None
        default = models_response.get("default_model")
        model_index = models.index(current_model) if current_model in models else (models.index(default) if default in models else 0)
        selected_model = st.selectbox("Model", models, index=model_index)
        if selected_model != current_model:
            changed = call_api("PUT", f"/sessions/{session_id}", {"model": selected_model})
            if "error" in changed:
                st.error(changed["error"])
            else:
                st.rerun()
    else:
        selected_model = None
        st.caption(models_response.get("error", "No inference models available"))

tab_chat, tab_milestones, tab_risks = st.tabs(["Chat", "Milestones", "Risks"])

with tab_chat:
    history = session.get("history", []) if "error" not in session else []
    for turn in history:
        with st.chat_message("user"):
            st.write(turn["user"])
        with st.chat_message("assistant"):
            st.write(turn["assistant"])
            show_sources(turn.get("sources", []))
    question = st.chat_input("Ask about the selected repository")
    if question:
        payload = {"session_id": session_id, "user": question, "show_sources": True, "show_confidence": True}
        if selected_repo:
            payload["repo_id"] = selected_repo
        if selected_model:
            payload["model"] = selected_model
        result = call_api("POST", "/ask", payload)
        if "error" in result:
            st.error(result["error"])
        elif result.get("answer"):
            with st.chat_message("user"):
                st.write(question)
            with st.chat_message("assistant"):
                st.write(result["answer"])
                if result.get("confidence"):
                    st.caption(f"Confidence: {result['confidence']}")
                show_sources(result.get("sources", []))
        else:
            st.info(result.get("message", "Select a repository to continue."))

with tab_milestones:
    repo_url = st.text_input("GitHub Repo URL", DEFAULT_REPO_URL)
    if st.button("Load Milestones"):
        path = urlparse(repo_url).path.strip("/").split("/")
        resp = call_mcp({"jsonrpc": "2.0", "id": 3, "method": "call/list_milestones",
                         "params": {"repo_owner": path[0], "repo_name": path[-1].removesuffix(".git")}})
        if "result" in resp:
            for milestone in resp["result"]["milestones"]:
                st.write(f"• **{milestone['name']}** — {milestone['status']}")
        else:
            st.error(resp.get("error", "Failed to load milestones"))

with tab_risks:
    repo_url = st.text_input("GitHub Repo URL", DEFAULT_REPO_URL, key="risks_url")
    if st.button("Analyze Risks"):
        path = urlparse(repo_url).path.strip("/").split("/")
        resp = call_mcp({"jsonrpc": "2.0", "id": 4, "method": "call/risk_summary",
                         "params": {"repo_owner": path[0], "repo_name": path[-1].removesuffix(".git")}})
        if "result" in resp:
            st.write(resp["result"]["summary"])
            st.caption(f"Issues analyzed: {resp['result']['count']}")
        else:
            st.error(resp.get("error", "Risk analysis failed"))
