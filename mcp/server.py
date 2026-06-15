import sys
import os
import asyncio
import json
from urllib.parse import urlparse

# -------------------------------------------------
# ENSURE PROJECT ROOT IS ON PATH
# -------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# -------------------------------------------------
# CORE IMPORTS (NO app.py)
# -------------------------------------------------
from rag.core import rag_answer, register_repo
from rag.milestones import list_milestones
from rag.risk import detect_risks
from rag.router import RouterAgent   # ✅ NEW
from github.repo_sync import sync_repo

from app_processing.file_loader import load_repo_files
from app_processing.chunker import chunk_text
from app_processing.embeddings import embed_texts
from vector_store.store import VectorStore

from repo_profiles.extractor import build_repo_profile

from mcp.schemas import (
    AskRequest,
    AskResponse,
    ReindexRequest,
    GenericResponse,
    ListMilestonesRequest,
    ListMilestonesResponse,
    RiskSummaryRequest,
)

# -------------------------------------------------
# ROUTER AGENT (SINGLE INSTANCE)
# -------------------------------------------------
router = RouterAgent()

# -------------------------------------------------
# HELPER: EXTRACT OWNER FROM GITHUB URL
# -------------------------------------------------
def extract_repo_owner(repo_url: str):
    try:
        path = urlparse(repo_url).path.strip("/")
        return path.split("/")[0] if "/" in path else None
    except Exception:
        return None

# -------------------------------------------------
# 🔁 INDEX AGENT (UNCHANGED)
# -------------------------------------------------
def index_agent(req: ReindexRequest):
    repo_url = req.repo_url
    repo_id = req.repo_id

    if not repo_url or not repo_id:
        raise ValueError("repo_id and repo_url are required")

    # 1️⃣ Sync repository
    repo_path = sync_repo(repo_url, repo_id)

    # 2️⃣ Deterministic repo profile
    build_repo_profile(
        repo_id=repo_id,
        repo_path=repo_path,
        repo_url=repo_url,
    )

    profile_path = os.path.join("repo_profiles", f"{repo_id}.json")
    if not os.path.exists(profile_path):
        raise RuntimeError("Repo profile was not created")

    with open(profile_path, "r", encoding="utf-8") as f:
        repo_profile = json.load(f)

    # 3️⃣ Load + chunk files
    files = load_repo_files(repo_path)
    chunks = []
    for doc in files:
        chunks.extend(chunk_text(doc["text"], doc["metadata"]))

    if not chunks:
        raise RuntimeError("Repository contains no indexable content")

    texts = [c["text"] for c in chunks]
    metas = [c["metadata"] for c in chunks]
    embeddings = embed_texts(texts)

    # 4️⃣ Build + persist vector store
    vector_store = VectorStore(embeddings, texts, metas)
    vector_store.save(repo_id)

    # 5️⃣ Create indices file (similar to chunks_store)
    try:
        from datetime import datetime
        INDICES_STORE_DIR = os.path.join(PROJECT_ROOT, "indices_store")
        os.makedirs(INDICES_STORE_DIR, exist_ok=True)
        
        repo_indices_dir = os.path.join(INDICES_STORE_DIR, repo_id)
        os.makedirs(repo_indices_dir, exist_ok=True)
        
        # Get unique files from metadata
        indexed_files = sorted(list(set([m.get("file_path", "unknown") for m in metas])))
        
        # Get embedding dimensions
        embedding_dim = len(embeddings[0]) if embeddings else 0
        
        # Get file types
        file_types = {}
        for m in metas:
            file_path = m.get("file_path", "")
            ext = os.path.splitext(file_path)[1].lower()
            if ext:
                file_types[ext] = file_types.get(ext, 0) + 1
        
        fingerprint = repo_profile.get("fingerprint")
        accuracy = repo_profile.get("accuracy")
        
        indices_snapshot = {
            "repo_id": repo_id,
            "fingerprint": fingerprint,
            "generated_at": datetime.utcnow().isoformat(),
            "index_statistics": {
                "total_embeddings": len(embeddings),
                "embedding_dimension": embedding_dim,
                "total_chunks": len(chunks),
                "total_files_indexed": len(indexed_files),
                "vector_store_path": f"vector_store/repos/{repo_id}"
            },
            "indexed_files": indexed_files,
            "file_types": file_types,
            "chunking_strategy": "markdown + code + fallback",
            "accuracy": accuracy
        }
        
        indices_file_path = os.path.join(repo_indices_dir, "indices.json")
        with open(indices_file_path, "w", encoding="utf-8") as f:
            json.dump(indices_snapshot, f, indent=2)
    except Exception as e:
        print(f"[!] Error creating indices file for {repo_id}: {e}")

    # 6️⃣ Register repo
    register_repo(
        repo_id=repo_id,
        repo_path=repo_path,
        vector_store=vector_store,
        repo_profile=repo_profile,
    )

    return GenericResponse(
        status="ok",
        detail=f"Repository '{repo_id}' indexed and loaded successfully",
    ).model_dump()

# -------------------------------------------------
# MCP DISPATCH
# -------------------------------------------------
async def handle_request(request: dict):
    method = request.get("method")
    params = request.get("params", {})

    # -----------------------------
    # Tool discovery
    # -----------------------------
    if method == "tools/list":
        return {
            "tools": [
                {"name": "ask_project", "agent": "RouterAgent"},
                {"name": "list_milestones", "agent": "PlanningAgent"},
                {"name": "risk_summary", "agent": "RiskAgent"},
                {"name": "rebuild_index", "agent": "IndexAgent"},
            ]
        }

    # -----------------------------
    # Routed project questions
    # -----------------------------
    if method == "call/ask_project":
        req = AskRequest(**params)

        result = router.route(
            question=req.question,
            repo_id=req.repo_id,  # Can be None, router will detect
            params=params,
        )

        # Handle clarification responses (they may have additional fields)
        # The AskResponse model will extract answer, confidence, sources
        return result  # Return full result dict for MCP (includes clarification fields)

    # -----------------------------
    # Milestones (direct agent)
    # -----------------------------
    if method == "call/list_milestones":
        req = ListMilestonesRequest(**params)
        milestones = list_milestones(
            repo_owner=req.repo_owner,
            repo_name=req.repo_name,
        )
        return ListMilestonesResponse(milestones=milestones).model_dump()

    # -----------------------------
    # Risks (direct agent)
    # -----------------------------
    if method == "call/risk_summary":
        req = RiskSummaryRequest(**params)
        risks = detect_risks(
            repo_owner=req.repo_owner,
            repo_name=req.repo_name,
        )

        # detect_risks may return list or dict
        if isinstance(risks, list):
            if not risks:
                return {
                    "summary": "No risks detected in the repository.",
                    "count": 0,
                }

            text = "\n".join(
                f"- {r.get('title', r)}"
                if isinstance(r, dict) else f"- {r}"
                for r in risks
            )

            return {
                "summary": text,
                "count": len(risks),
            }

        if isinstance(risks, dict):
            return {
                "summary": risks.get("summary", "No risks detected."),
                "count": len(risks.get("items", [])),
            }

    # -----------------------------
    # Reindex
    # -----------------------------
    if method == "call/rebuild_index":
        req = ReindexRequest(**params)
        return index_agent(req)

    return {"error": f"Unknown method '{method}'"}

# -------------------------------------------------
# ✅ ONE-SHOT MCP ENTRYPOINT (STREAMLIT SAFE)
# -------------------------------------------------
async def main():
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        return

    try:
        request = json.loads(raw_input)
        response = await handle_request(request)

        reply = {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": response,
        }

        sys.stdout.write(json.dumps(reply))
        sys.stdout.flush()

    except Exception as e:
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "error": str(e),
        }))
        sys.stdout.flush()

if __name__ == "__main__":
    asyncio.run(main())
