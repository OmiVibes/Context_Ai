import sys
import os
import asyncio
import json
from contextlib import redirect_stdout
from pydantic import ValidationError
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
from utils.errors import ServiceError
from utils.repo_paths import validate_repo_id, repo_path as safe_repo_path, contained_path
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

SUPPORTED_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
TOOL_SCHEMAS = {
    "ask_project": (AskRequest, "Answer a question using the selected repository"),
    "list_milestones": (ListMilestonesRequest, "List milestones inferred from GitHub issues"),
    "risk_summary": (RiskSummaryRequest, "Summarize risks from GitHub issues"),
    "rebuild_index": (ReindexRequest, "Safely sync and index a repository"),
}


class ProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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
    repo_id = validate_repo_id(req.repo_id) if req.repo_id else None

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

    profile_path = str(contained_path("repo_profiles", f"{repo_id}.json"))
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
        
        repo_indices_dir = str(safe_repo_path(INDICES_STORE_DIR, repo_id))
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
        
        indices_file_path = str(contained_path(repo_indices_dir, "indices.json"))
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
    if method == "initialize":
        version = params.get("protocolVersion")
        if not isinstance(version, str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
            raise ProtocolError(-32602, "Invalid initialization parameters")
        return {"protocolVersion": version if version in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[-1],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "context-assist", "version": "1.0.0"}}
    if method in ("ping", "notifications/initialized", "notifications/cancelled"):
        return {}
    if method == "tools/list":
        return {"tools": [{"name": name, "description": description, "inputSchema": schema.model_json_schema()}
                          for name, (schema, description) in TOOL_SCHEMAS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOL_SCHEMAS:
            raise ProtocolError(-32602, "Unknown tool name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ProtocolError(-32602, "Tool arguments must be an object")
        # Validate before execution; runtime tool failures use isError.
        TOOL_SCHEMAS[name][0](**arguments)
        try:
            result = await handle_request({"method": "call/" + name, "params": arguments})
            return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}
        except ServiceError as exc:
            return {"content": [{"type": "text", "text": json.dumps(exc.detail())}], "isError": True}
        except ValueError as exc:
            raise ProtocolError(-32602, str(exc)) from exc
        except Exception:
            return {"content": [{"type": "text", "text": "Tool execution failed; check repository state and service configuration"}], "isError": True}

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

    raise ProtocolError(-32601, "Method not found")

# -------------------------------------------------
# ✅ ONE-SHOT MCP ENTRYPOINT (STREAMLIT SAFE)
# -------------------------------------------------
async def process_message(raw: str):
    request_id = None
    notification = False
    try:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(-32700, "Parse error") from exc
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            raise ProtocolError(-32600, "Invalid Request")
        request_id = request.get("id")
        if request_id is not None and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
            request_id = None
            raise ProtocolError(-32600, "Invalid request ID")
        notification = "id" not in request
        if not isinstance(request.get("params", {}), dict):
            raise ProtocolError(-32602, "Parameters must be an object")
        # Notifications never invoke mutation/query tools and never receive replies.
        if notification:
            return None
        with redirect_stdout(sys.stderr):
            result = await handle_request(request)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except ProtocolError as exc:
        error = {"code": exc.code, "message": str(exc)}
    except ValidationError:
        error = {"code": -32602, "message": "Invalid tool parameters"}
    except ValueError as exc:
        error = {"code": -32602, "message": str(exc)}
    except ServiceError as exc:
        error = {"code": -32000, "message": str(exc), "data": {"status": exc.status_code, **exc.detail()}}
    except Exception:
        error = {"code": -32603, "message": "Tool execution failed; check repository state and service configuration"}
    return None if notification else {"jsonrpc": "2.0", "id": request_id, "error": error}


async def main():
    # Newline-delimited stdio also accepts the existing one-shot payload at EOF.
    for raw in sys.stdin:
        if not raw.strip():
            continue
        reply = await process_message(raw)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
