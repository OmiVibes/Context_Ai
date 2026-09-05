import os
import sys
import json
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rag.core import _VECTOR_STORES
from datetime import datetime
from utils.errors import install_error_handlers, ServiceError
from app_processing.file_loader import load_repo_files
from app_processing.chunker import chunk_text
from app_processing.embeddings import embed_texts
from vector_store.store import VectorStore
from utils.repo_paths import validate_repo_id, repo_path as safe_repo_path, contained_path, InvalidRepoId

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        # Fallback for older Python versions
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, errors='replace')

# RAG CORE
from rag.core import rag_answer, register_repo
from rag.metrics_extractor import extract_accuracy

# UPDATE-ONLY INDEXING
from utils.project_fingerprint import compute_project_fingerprint


# -------------------------------------------------
# CONFIG
# -------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHUNK_STORE_DIR = os.path.join(BASE_DIR, "chunk_store")
INDICES_STORE_DIR = os.path.join(BASE_DIR, "indices_store")

# 👇 parent folder that contains ALL projects + this tool
# Dynamically calculated from current file location
WORKSPACE_ROOT = os.path.dirname(BASE_DIR)

# 👇 this tool's own folder (must be excluded)
# Same as BASE_DIR - this is the CONTEXT_ASSIST project folder itself
PROJECT_CONTEXT_DIR = BASE_DIR

PROFILE_DIR = os.path.join(BASE_DIR, "repo_profiles")

# -------------------------------------------------
# GENERIC QUESTION DETECTION (GLOBAL / NON-REPO)
# -------------------------------------------------
from utils.questions import is_generic_question, greeting_answer


app = FastAPI(title="Project Context AI – Thin API")
install_error_handlers(app)


# -------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------
def create_indices_file(repo_id: str, vector_store, fingerprint: str = None, accuracy: str = None, 
                        file_types: dict = None, chunks_created: int = None):
    """
    Create or update indices.json file for a repository.
    Can be called after indexing or when loading existing vector store.
    """
    try:
        os.makedirs(INDICES_STORE_DIR, exist_ok=True)
        repo_indices_dir = str(safe_repo_path(INDICES_STORE_DIR, repo_id))
        os.makedirs(repo_indices_dir, exist_ok=True)
        
        indices_file_path = str(contained_path(repo_indices_dir, "indices.json"))
        
        # Get metadata from vector store
        metas = vector_store.metadatas if hasattr(vector_store, 'metadatas') else []
        indexed_files = sorted(list(set([m.get("file_path", "unknown") for m in metas])))
        
        # Get embedding info
        embeddings = vector_store.embeddings if hasattr(vector_store, 'embeddings') else []
        embedding_dim = len(embeddings[0]) if embeddings else 0
        
        # Get file types from metadata if not provided
        if file_types is None:
            file_types = {}
            for m in metas:
                file_path = m.get("file_path", "")
                ext = os.path.splitext(file_path)[1].lower()
                if ext:
                    file_types[ext] = file_types.get(ext, 0) + 1
        
        # Use chunks_created if provided, otherwise use embeddings count
        total_chunks = chunks_created if chunks_created is not None else len(embeddings)
        
        # Load existing profile for fingerprint if not provided
        if not fingerprint:
            profile_path = os.path.join(PROFILE_DIR, repo_id, "profile.json")
            if os.path.exists(profile_path):
                try:
                    with open(profile_path, "r", encoding="utf-8") as f:
                        profile = json.load(f)
                        fingerprint = profile.get("fingerprint")
                        if not accuracy:
                            accuracy = profile.get("accuracy")
                except Exception:
                    pass
        
        indices_snapshot = {
            "repo_id": repo_id,
            "fingerprint": fingerprint,
            "generated_at": datetime.utcnow().isoformat(),
            "index_statistics": {
                "total_embeddings": len(embeddings),
                "embedding_dimension": embedding_dim,
                "total_chunks": total_chunks,
                "total_files_indexed": len(indexed_files),
                "vector_store_path": f"vector_store/repos/{repo_id}"
            },
            "indexed_files": indexed_files,
            "file_types": file_types,
            "chunking_strategy": "markdown + code + fallback",
            "accuracy": accuracy
        }
        
        with open(indices_file_path, "w", encoding="utf-8") as f:
            json.dump(indices_snapshot, f, indent=2)
        
        print(f"[+] Indices file created/updated: {indices_file_path}")
        return True
    except Exception as e:
        print(f"[!] Error creating indices file for {repo_id}: {e}")
        return False


# -------------------------------------------------
# MODELS
# -------------------------------------------------
class IndexRequest(BaseModel):
    repo_id: str


class Query(BaseModel):
    session_id: str
    user: str
    show_sources: bool = False
    show_confidence: bool = False

# -------------------------------------------------
# SESSION STORE (IN-MEMORY)
# -------------------------------------------------
_SESSIONS: Dict[str, Dict[str, Any]] = {}


# -------------------------------------------------
# STARTUP (INTENTIONALLY EMPTY FOR DEMO)
# -------------------------------------------------
@app.on_event("startup")
def startup():
    print("[*] Project Context AI starting")
    print(f"[*] Workspace root: {WORKSPACE_ROOT}")
    
    repos_to_process = []
    
    # 1. Scan workspace root for local projects
    if os.path.exists(WORKSPACE_ROOT):
        for name in os.listdir(WORKSPACE_ROOT):
            project_path = os.path.join(WORKSPACE_ROOT, name)
            if os.path.isdir(project_path) and project_path != PROJECT_CONTEXT_DIR:
                repos_to_process.append((name, project_path, "workspace"))
    
    # 2. Scan repos/ directory for git repos
    repos_dir = os.path.join(BASE_DIR, "repos")
    if os.path.exists(repos_dir):
        for name in os.listdir(repos_dir):
            project_path = os.path.join(repos_dir, name)
            if os.path.isdir(project_path):
                repos_to_process.append((name, project_path, "git"))
    
    print(f"[*] Found {len(repos_to_process)} repositories to process")
    
    for name, project_path, source in repos_to_process:
        print(f"\n[*] Detected repository -> {name} (source: {source})")

        try:
            # Try to index (will skip if no changes)
            index_result = index_repo(IndexRequest(repo_id=name))
            
            # If indexing was skipped, try to load existing vector store
            if index_result.get("action") == "skipped":
                print(f"[*] Loading existing vector store for {name}...")
                from rag.core import register_repo, load_repo_profile
                from vector_store.store import VectorStore
                
                vector_store = VectorStore.load(repo_id=name)
                if vector_store:
                    profile = load_repo_profile(name)
                    register_repo(
                        repo_id=name,
                        repo_path=project_path,
                        vector_store=vector_store,
                        repo_profile=profile
                    )
                    print(f"[+] Loaded vector store for {name}")
                    
                    # Ensure indices file exists (create if missing)
                    indices_file = os.path.join(INDICES_STORE_DIR, name, "indices.json")
                    if not os.path.exists(indices_file):
                        fingerprint = profile.get("fingerprint") if profile else None
                        accuracy = profile.get("accuracy") if profile else None
                        create_indices_file(name, vector_store, fingerprint, accuracy)
                else:
                    print(f"[!] No vector store found for {name}")
            else:
                # Indexing happened, repo should already be registered
                print(f"[+] Indexed and registered {name}")
                
        except Exception as e:
            print(f"[!] Failed to process {name}: {e}")
            # Try to load existing vector store even if indexing failed
            try:
                from rag.core import register_repo, load_repo_profile
                from vector_store.store import VectorStore
                
                vector_store = VectorStore.load(repo_id=name)
                if vector_store:
                    profile = load_repo_profile(name)
                    register_repo(
                        repo_id=name,
                        repo_path=project_path,
                        vector_store=vector_store,
                        repo_profile=profile
                    )
                    print(f"[+] Loaded existing vector store for {name}")
                    
                    # Ensure indices file exists (create if missing)
                    indices_file = os.path.join(INDICES_STORE_DIR, name, "indices.json")
                    if not os.path.exists(indices_file):
                        fingerprint = profile.get("fingerprint") if profile else None
                        accuracy = profile.get("accuracy") if profile else None
                        create_indices_file(name, vector_store, fingerprint, accuracy)
            except Exception as load_error:
                print(f"[!] Could not load vector store for {name}: {load_error}")

    print("\n[+] Workspace scan completed")
    
    # Print summary of loaded repos
    from rag.core import _VECTOR_STORES
    loaded_repos = list(_VECTOR_STORES.keys())
    if loaded_repos:
        print(f"[+] Loaded {len(loaded_repos)} repository vector stores: {', '.join(loaded_repos)}")
    else:
        print("[!] No repositories loaded. Make sure repositories are indexed.")


# -------------------------------------------------
# HEALTH CHECK
# -------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Project Context AI",
        "mode": "development"
    }


# -------------------------------------------------
# 🔹 INDEX ENDPOINT (CORE DEMO FEATURE)
# -------------------------------------------------
@app.post("/index")
def index_repo(req: IndexRequest):
    try:
        repo_id = validate_repo_id(req.repo_id)
    except InvalidRepoId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    print(f"[*] Index request received for: {repo_id}")
    
    # Check multiple locations: workspace root (local projects) and repos/ (git repos)
    repo_path = None
    repo_source = None
    
    # 1. Check workspace root (local projects)
    workspace_repo_path = str(safe_repo_path(WORKSPACE_ROOT, repo_id))
    if os.path.isdir(workspace_repo_path):
        repo_path = workspace_repo_path
        repo_source = "workspace"
    
    # 2. Check repos/ directory (git-cloned repos)
    if not repo_path:
        repos_dir = os.path.join(BASE_DIR, "repos")
        git_repo_path = str(safe_repo_path(repos_dir, repo_id))
        if os.path.isdir(git_repo_path):
            repo_path = git_repo_path
            repo_source = "git"
    
    if not repo_path:
        # Get available repos for helpful error message
        available_repos = []
        if os.path.exists(WORKSPACE_ROOT):
            for name in os.listdir(WORKSPACE_ROOT):
                path = os.path.join(WORKSPACE_ROOT, name)
                if os.path.isdir(path) and path != PROJECT_CONTEXT_DIR:
                    available_repos.append(f"workspace/{name}")
        
        repos_dir = os.path.join(BASE_DIR, "repos")
        if os.path.exists(repos_dir):
            for name in os.listdir(repos_dir):
                path = os.path.join(repos_dir, name)
                if os.path.isdir(path):
                    available_repos.append(f"git/{name}")
        
        error_msg = f"Repository '{repo_id}' not found. "
        if available_repos:
            error_msg += f"Available repositories: {', '.join(available_repos)}. "
        error_msg += "Use /debug/state to see all available repositories."
        raise HTTPException(status_code=404, detail=error_msg)
    
    print(f"[*] Found repository at: {repo_path} (source: {repo_source})")
    
    # -------------------------------------------------
    # 📁 Repo-specific profile directory
    # -------------------------------------------------
    repo_profile_dir = str(safe_repo_path(PROFILE_DIR, repo_id))
    os.makedirs(repo_profile_dir, exist_ok=True)

    os.makedirs(PROFILE_DIR, exist_ok=True)
    profile_path = str(contained_path(repo_profile_dir, "profile.json"))

    # -------------------------------------------------
    # 🔁 UPDATE-ONLY INDEXING (FINGERPRINT CHECK)
    # -------------------------------------------------
    old_fingerprint = None
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                old_profile = json.load(f)
                old_fingerprint = old_profile.get("fingerprint")
        except Exception:
            pass

    current_fingerprint = compute_project_fingerprint(repo_path)

    if old_fingerprint == current_fingerprint:
        vector_store = VectorStore.load(repo_id=repo_id)
        if vector_store is not None:
            register_repo(repo_id=repo_id, repo_path=repo_path,
                          vector_store=vector_store, repo_profile=old_profile)
            create_indices_file(repo_id, vector_store, current_fingerprint, old_profile.get("accuracy"))
            decision = {
                "repo_id": repo_id, "action": "skipped", "reason": "No changes detected",
                "fingerprint": current_fingerprint, "timestamp": datetime.utcnow().isoformat(),
                "vector_store_loaded": True,
            }
            decision_path = str(contained_path(repo_profile_dir, "index_decision.json"))
            with open(decision_path, "w", encoding="utf-8") as f:
                json.dump(decision, f, indent=2)
            return decision
        # A matching profile alone does not establish a usable persisted index.
        _VECTOR_STORES.pop(repo_id, None)

    print(f"\n[*] INDEXING STARTED -> {repo_id}")

    # -------------------------------------------------
    # -------------------------------------------------
    # Documentation is filtered by the loader, never removed from the repository.
    documents = load_repo_files(repo_path)
    files_loaded = len(documents)
    
    # Show file types being indexed
    file_types = {}
    for doc in documents:
        file_path = doc["metadata"].get("file_path", "")
        ext = os.path.splitext(file_path)[1].lower()
        file_types[ext] = file_types.get(ext, 0) + 1
    
    print(f"[*] Files loaded: {files_loaded}")
    if file_types:
        type_summary = ", ".join([f"{ext}: {count}" for ext, count in sorted(file_types.items())])
        print(f"[*] File types: {type_summary}")

    # -------------------------------------------------
    # 3️⃣ Extract accuracy from code files (not README)
    # -------------------------------------------------
    accuracy = None
    # Try to find accuracy in code comments or docstrings instead
    for doc in documents:
        file_path = doc["metadata"].get("file_path", "").lower()
        # Look for accuracy mentions in Python files or config files
        if file_path.endswith((".py", ".yaml", ".yml", ".json", ".config")):
            accuracy = extract_accuracy(doc["text"])
            if accuracy:
                break

    # -------------------------------------------------
    # 3️⃣ Chunking
    # -------------------------------------------------
    chunks = []
    for doc in documents:
        chunks.extend(chunk_text(doc["text"], doc["metadata"]))

    chunks_created = len(chunks)

    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="No valid chunks found. Low-signal repository."
        )
    
    # -------------------------------------------------
    # 💾 SAVE CHUNKS SNAPSHOT (SINGLE FILE PER PROJECT)
    # -------------------------------------------------
    os.makedirs(CHUNK_STORE_DIR, exist_ok=True)

    repo_chunk_dir = str(safe_repo_path(CHUNK_STORE_DIR, repo_id))
    os.makedirs(repo_chunk_dir, exist_ok=True)

    chunk_snapshot = {
        "repo_id": repo_id,
        "fingerprint": current_fingerprint,
        "generated_at": datetime.utcnow().isoformat(),
        "files_scanned": files_loaded,
        "chunks_created": chunks_created,
        "chunks": []
    }

    for i, c in enumerate(chunks, start=1):
        chunk_snapshot["chunks"].append({
            "chunk_id": i,
            "file": c["metadata"].get("file_path"),
            "section": c["metadata"].get("section"),
            "topic": c["metadata"].get("topic"),
            "text": c["text"]
        })

    chunk_file_path = str(contained_path(repo_chunk_dir, "chunks.json"))

    with open(chunk_file_path, "w", encoding="utf-8") as f:
        json.dump(chunk_snapshot, f, indent=2)


    # -------------------------------------------------
    # 4️⃣ Embeddings
    # -------------------------------------------------
    texts = [c["text"] for c in chunks]
    metas = [c["metadata"] for c in chunks]
    embeddings = embed_texts(texts)

    # -------------------------------------------------
    # 5️⃣ Vector Store
    # -------------------------------------------------
    vector_store = VectorStore(embeddings, texts, metas)
    vector_store.save(repo_id=repo_id)

    # -------------------------------------------------
    # 💾 SAVE INDICES SNAPSHOT (SINGLE FILE PER PROJECT)
    # -------------------------------------------------
    create_indices_file(repo_id, vector_store, current_fingerprint, accuracy, file_types, chunks_created)

    # -------------------------------------------------
    # 6️⃣ Save repo profile (accuracy + fingerprint)
    # -------------------------------------------------
    profile_data = {
        "repo_id": repo_id,
        "accuracy": accuracy,
        "fingerprint": current_fingerprint
    }

    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile_data, f, indent=2)

    # -------------------------------------------------
    # 📄 INDEX MANIFEST (PROOF OF WORK)
    # -------------------------------------------------
    manifest = {
        "repo_id": repo_id,
        "indexed_at": datetime.utcnow().isoformat(),
        "files_loaded": files_loaded,
        "chunks_created": chunks_created,
        "accuracy": accuracy,
        "chunking_strategy": "markdown + code + fallback",
        "vector_store_path": f"vector_store/repos/{repo_id}"
    }

    # -------------------------------------------------
    # 📄 INDEX DECISION (INDEXED)
    # -------------------------------------------------
    decision = {
        "repo_id": repo_id,
        "action": "indexed",
        "reason": "Fingerprint changed or first index",
        "fingerprint": current_fingerprint,
        "timestamp": datetime.utcnow().isoformat()
    }

    decision_path = str(contained_path(repo_profile_dir, "index_decision.json"))
    with open(decision_path, "w", encoding="utf-8") as f:
        json.dump(decision, f, indent=2)


    manifest_path = str(contained_path(repo_profile_dir, "index_manifest.json"))
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    
    # -------------------------------------------------
    # 7️⃣ Register with RAG core
    # -------------------------------------------------
    register_repo(
        repo_id=repo_id,
        repo_path=repo_path,
        vector_store=vector_store,
        repo_profile=profile_data
    )

    print(f"[+] INDEXING COMPLETED -> {repo_id}")
    print(f"   Files loaded  : {files_loaded}")
    print(f"   Chunks created: {chunks_created}")
    print(f"   Fingerprint   : {current_fingerprint}")
    print(f"   Vector store  : vector_store/repos/{repo_id}\n")

    # -------------------------------------------------
    # 8️⃣ Sample chunks (proof indexing is real)
    # -------------------------------------------------
    sample_chunks = []
    for c in chunks[:2]:
        sample_chunks.append({
            "file": c["metadata"].get("file_path"),
            "section": c["metadata"].get("section"),
            "topic": c["metadata"].get("topic"),
            "preview": c["text"][:120] + "..."
        })

    return {
        "repo_id": repo_id,
        "status": "indexed",
        "files_loaded": files_loaded,
        "chunks_created": chunks_created,
        "accuracy": accuracy,
        "fingerprint": current_fingerprint,
        "vector_store_path": f"vector_store/repos/{repo_id}",
        "sample_chunks": sample_chunks
    }


# -------------------------------------------------
# 🔹 QUERY ENDPOINT (SESSION-BASED)
# -------------------------------------------------
@app.post("/ask")
def ask(q: Query):
    from rag.repo_detector import get_all_available_repos, detect_repo_from_question

    user_input = q.user.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="'user' field cannot be empty")
    session = _SESSIONS.setdefault(q.session_id, {})
    if is_generic_question(user_input):
        session.pop("question", None)
        return greeting_answer()

    available_repos = get_all_available_repos(base_dir=BASE_DIR)
    selection = user_input.lower()
    for prefix in ("i want to use ", "i want ", "use ", "select "):
        if selection.startswith(prefix):
            selection = selection[len(prefix):].strip()
            break
    matches = [repo for repo in available_repos if selection in
               {repo.lower(), repo.lower().replace("-", " "), repo.lower().replace("_", " ")}]
    selected = matches[0] if len(matches) == 1 else None
    pending = session.pop("question", None)
    question = pending if pending and selected else user_input
    detection = detect_repo_from_question(question, base_dir=BASE_DIR)
    repo_id = selected
    if not repo_id and detection["status"] == "unique_match":
        repo_id = detection["repo_id"]
    if not repo_id and detection["status"] == "general_question":
        active = session.get("repo_id")
        if active in available_repos:
            repo_id = active

    try:
        if repo_id:
            result = rag_answer(question=question, repo_id=repo_id,
                                show_sources=q.show_sources, show_confidence=q.show_confidence)
            session["repo_id"] = repo_id
            return result
        if detection["status"] == "project_group":
            from rag.core import rag_answer_multi_repo
            session.pop("repo_id", None)
            repos = detection["matching_repos"]
            result = rag_answer_multi_repo(question=question, repo_ids=repos,
                                           show_sources=q.show_sources, show_confidence=q.show_confidence)
            result["project_group"] = detection["project_base_name"]
            result["searched_repos"] = repos
            return result
    except (ServiceError, InvalidRepoId):
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "query_failure", "message": "Unexpected error processing query"}) from exc

    session.pop("repo_id", None)
    if not available_repos:
        return {"message": "No repositories are currently indexed. Please index a repository first using the /index endpoint.", "available_repos": []}
    session["question"] = user_input
    repos_list = "\n".join(f"{i+1}. {repo}" for i, repo in enumerate(available_repos))
    return {"message": f"Which repository are you referring to?\n\n{repos_list}\n\nType the repository name.",
            "available_repos": available_repos}


@app.get("/health/github")
def github_configuration():
    from github.api import configuration_status
    return configuration_status()


@app.get("/debug/state")
def debug_state():
    # repos currently indexed in memory
    indexed_repos = list(_VECTOR_STORES.keys())

    # repos present on disk (vector stores)
    vector_stores_on_disk = []
    vector_store_base = os.path.join("vector_store", "repos")
    if os.path.exists(vector_store_base):
        for name in os.listdir(vector_store_base):
            path = os.path.join(vector_store_base, name)
            if os.path.isdir(path):
                faiss_path = os.path.join(path, "index.faiss")
                meta_path = os.path.join(path, "metadata.pkl")
                if os.path.exists(faiss_path) and os.path.exists(meta_path):
                    vector_stores_on_disk.append(name)

    # repos present on disk (profiles)
    repo_profiles_present = []
    if os.path.exists(PROFILE_DIR):
        for name in os.listdir(PROFILE_DIR):
            path = os.path.join(PROFILE_DIR, name)
            if os.path.isdir(path):
                repo_profiles_present.append(name)

    # repos in workspace (local projects)
    repos_in_workspace = []
    if os.path.exists(WORKSPACE_ROOT):
        for name in os.listdir(WORKSPACE_ROOT):
            path = os.path.join(WORKSPACE_ROOT, name)
            if os.path.isdir(path) and path != PROJECT_CONTEXT_DIR:
                repos_in_workspace.append(name)

    # git repos in repos/ directory
    git_repos = []
    repos_dir = os.path.join(BASE_DIR, "repos")
    if os.path.exists(repos_dir):
        for name in os.listdir(repos_dir):
            path = os.path.join(repos_dir, name)
            if os.path.isdir(path):
                git_repos.append(name)

    return {
        "indexed_repos_in_memory": indexed_repos,
        "vector_stores_loaded": len(_VECTOR_STORES),
        "vector_stores_on_disk": vector_stores_on_disk,
        "repo_profiles_present": repo_profiles_present,
        "local_repos_in_workspace": repos_in_workspace,
        "git_repos_in_repos_folder": git_repos,
        "all_available_repos": repos_in_workspace + git_repos,
        "workspace_root": WORKSPACE_ROOT,
        "repos_directory": repos_dir
    }

