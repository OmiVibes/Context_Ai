import streamlit as st
import subprocess
import json
import sys
from pathlib import Path

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = PROJECT_ROOT / "mcp" / "server.py"

DEFAULT_REPO_ID = "facemask-detector"
DEFAULT_REPO_URL = "https://github.com/Dharani-Barigeda/facemask-detector.git"

# -------------------------------------------------
# MCP CALL HELPER (STRICT JSON, STREAMLIT SAFE)
# -------------------------------------------------
def call_mcp(payload: dict):
    """
    Calls MCP via stdin/stdout (JSON-RPC)
    One-shot process. JSON only. No logs.
    """
    proc = subprocess.Popen(
        [sys.executable, str(MCP_SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Send request
    proc.stdin.write(json.dumps(payload))
    proc.stdin.close()

    stdout = proc.stdout.read().strip()
    stderr = proc.stderr.read().strip()

    # MCP must respond with JSON ONLY
    if not stdout:
        return {"error": "Empty response from MCP"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "error": "Invalid JSON from MCP",
            "raw_output": stdout,
            "stderr": stderr,
        }

# -------------------------------------------------
# UI LAYOUT
# -------------------------------------------------
st.set_page_config(page_title="Project Context AI", layout="wide")

st.title("🧠 Project Context AI")
st.caption("Ask questions about a GitHub repository using MCP + RAG")

# -------------------------------------------------
# SIDEBAR
# -------------------------------------------------
st.sidebar.header("Repository")

repo_id = st.sidebar.text_input(
    "Repository ID",
    DEFAULT_REPO_ID,
)

repo_url = st.sidebar.text_input(
    "GitHub Repo URL",
    DEFAULT_REPO_URL,
)

if st.sidebar.button("🔄 Rebuild Index"):
    resp = call_mcp({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "call/rebuild_index",
        "params": {
            "repo_id": repo_id,
            "repo_url": repo_url,
        },
    })

    if "result" in resp:
        st.sidebar.success(resp["result"]["detail"])
    else:
        st.sidebar.error(resp.get("error", "Index failed"))

# -------------------------------------------------
# TABS
# -------------------------------------------------
tab_chat, tab_milestones, tab_risks = st.tabs(
    ["💬 Chat", "📌 Milestones", "⚠️ Risks"]
)

# -------------------------------------------------
# CHAT TAB
# -------------------------------------------------
with tab_chat:
    st.subheader("Ask about this project")

    question = st.text_input(
        "Your question",
        placeholder="What does this project do?",
    )

    if st.button("Ask"):
        resp = call_mcp({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "call/ask_project",
            "params": {
                "question": question,
                "repo_id": repo_id,
                "show_confidence": True,
            },
        })

        if "result" in resp:
            st.markdown("### Answer")
            st.write(resp["result"]["answer"])

            if resp["result"].get("confidence"):
                st.caption(f"Confidence: {resp['result']['confidence']}")
        else:
            st.error(resp.get("error", "No response from MCP"))

# -------------------------------------------------
# MILESTONES TAB
# -------------------------------------------------
with tab_milestones:
    st.subheader("Project Milestones")

    if st.button("Load Milestones"):
        resp = call_mcp({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "call/list_milestones",
            "params": {
                "repo_owner": "OmiVibes",
                "repo_name": repo_id,
            },
        })

        if "result" in resp:
            for m in resp["result"]["milestones"]:
                st.write(f"• **{m['name']}** — {m['status']}")
        else:
            st.error(resp.get("error", "Failed to load milestones"))

# -------------------------------------------------
# RISKS TAB
# -------------------------------------------------
with tab_risks:
    st.subheader("Project Risks")

    if st.button("Analyze Risks"):
        resp = call_mcp({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "call/risk_summary",
            "params": {
                "repo_owner": "OmiVibes",
                "repo_name": repo_id,
            },
        })

        if "result" in resp:
            st.write(resp["result"]["summary"])
            st.caption(f"Issues analyzed: {resp['result']['count']}")
        else:
            st.error(resp.get("error", "Risk analysis failed"))
