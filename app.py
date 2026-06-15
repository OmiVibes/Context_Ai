import os
import sys
import json
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rag.core import _VECTOR_STORES
from datetime import datetime
from app_processing.file_loader import load_repo_files
from app_processing.chunker import chunk_text
from app_processing.embeddings import embed_texts
from vector_store.store import VectorStore

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
GENERIC_QUERIES = {
    "hi", "hello", "hey", "hi there", "hello there",
    "how are you", "how are you doing",
    "who are you", "what are you",
    "what can you do", "help", "introduce yourself"
}

def is_generic_question(q: str) -> bool:
    q = q.lower().strip()

    # exact short greetings
    if q in GENERIC_QUERIES:
        return True

    # greeting prefixes
    if any(q.startswith(g) for g in ["hi", "hello", "hey"]):
        return True

    # identity / assistant questions (contains-based)
    GENERIC_PATTERNS = [
        "who are you",
        "what are you",
        "introduce yourself",
        "what can you do",
        "your purpose",
        "about you",
    ]

    return any(p in q for p in GENERIC_PATTERNS)


# -------------------------------------------------
# APP
# -------------------------------------------------
app = FastAPI(title="Project Context AI – Thin API")


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
        repo_indices_dir = os.path.join(INDICES_STORE_DIR, repo_id)
        os.makedirs(repo_indices_dir, exist_ok=True)
        
        indices_file_path = os.path.join(repo_indices_dir, "indices.json")
        
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
    repo_id = req.repo_id.strip()

    if not repo_id:
        raise HTTPException(status_code=400, detail="repo_id is required")

    print(f"[*] Index request received for: {repo_id}")
    
    # Check multiple locations: workspace root (local projects) and repos/ (git repos)
    repo_path = None
    repo_source = None
    
    # 1. Check workspace root (local projects)
    workspace_repo_path = os.path.join(WORKSPACE_ROOT, repo_id)
    if os.path.exists(workspace_repo_path):
        repo_path = workspace_repo_path
        repo_source = "workspace"
    
    # 2. Check repos/ directory (git-cloned repos)
    if not repo_path:
        repos_dir = os.path.join(BASE_DIR, "repos")
        git_repo_path = os.path.join(repos_dir, repo_id)
        if os.path.exists(git_repo_path):
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
    repo_profile_dir = os.path.join(PROFILE_DIR, repo_id)
    os.makedirs(repo_profile_dir, exist_ok=True)

    os.makedirs(PROFILE_DIR, exist_ok=True)
    profile_path = os.path.join(repo_profile_dir, "profile.json")

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
        decision = {
            "repo_id": repo_id,
            "action": "skipped",
            "reason": "No changes detected",
            "fingerprint": current_fingerprint,
            "timestamp": datetime.utcnow().isoformat()
        }

        decision_path = os.path.join(repo_profile_dir, "index_decision.json")
        with open(decision_path, "w", encoding="utf-8") as f:
            json.dump(decision, f, indent=2)

        print(f"[>] SKIPPING INDEX -> {repo_id} (no changes detected)")
        
        # Even if skipped, try to load existing vector store and register it
        try:
            vector_store = VectorStore.load(repo_id=repo_id)
            if vector_store:
                profile = None
                if os.path.exists(profile_path):
                    try:
                        with open(profile_path, "r", encoding="utf-8") as f:
                            profile = json.load(f)
                    except Exception:
                        pass
                
                register_repo(
                    repo_id=repo_id,
                    repo_path=repo_path,
                    vector_store=vector_store,
                    repo_profile=profile
                )
                print(f"[+] Loaded and registered existing vector store for {repo_id}")
                decision["vector_store_loaded"] = True
                
                # Ensure indices file exists (create if missing)
                indices_file = os.path.join(INDICES_STORE_DIR, repo_id, "indices.json")
                if not os.path.exists(indices_file):
                    fingerprint = profile.get("fingerprint") if profile else None
                    accuracy = profile.get("accuracy") if profile else None
                    create_indices_file(repo_id, vector_store, fingerprint, accuracy)
            else:
                print(f"[!] No vector store found for {repo_id}")
                decision["vector_store_loaded"] = False
        except Exception as e:
            print(f"[!] Error loading vector store for {repo_id}: {e}")
            decision["vector_store_loaded"] = False

        return decision


    print(f"\n[*] INDEXING STARTED -> {repo_id}")

    # -------------------------------------------------
    # 1️⃣ Remove README files from local projects (if any)
    # -------------------------------------------------
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            if file.lower().startswith("readme"):
                readme_path = os.path.join(root, file)
                try:
                    os.remove(readme_path)
                    print(f"[*] Removed README file: {readme_path}")
                except Exception as e:
                    print(f"[!] Could not remove README file {readme_path}: {e}")

    # -------------------------------------------------
    # 2️⃣ Load files (READMEs and docs are skipped - code files only)
    # -------------------------------------------------
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
            text = doc["text"]
            # Look for accuracy patterns in code
            import re
            # Match patterns like: accuracy = 0.4, accuracy: 0.4, accuracy=0.4, accuracy 0.4
            # Also match: accuracy = 0.948 (decimal) or accuracy: 94.8% (percentage)
            patterns = [
                r'accuracy[:\s=]+(\d{1,3}(?:\.\d+)?\s*%)',  # Percentage format: 94.8%
                r'accuracy[:\s=]+(0\.\d{1,4})',  # Decimal format: 0.948 or 0.4
                r'accuracy[:\s=]+(\d{1,3}(?:\.\d+)?)\s*(?:percent|%)',  # With word "percent"
            ]
            
            for pattern in patterns:
                accuracy_match = re.search(pattern, text, re.IGNORECASE)
                if accuracy_match:
                    value = accuracy_match.group(1).strip()
                    # Convert decimal to percentage if needed
                    try:
                        float_val = float(value.replace('%', ''))
                        # If it's a decimal (0.0 to 1.0), convert to percentage
                        if 0 <= float_val <= 1 and '%' not in value:
                            accuracy = f"{float_val * 100:.1f}%"
                        else:
                            # Already a percentage or > 1, keep as is
                            accuracy = value if '%' in value else f"{value}%"
                    except ValueError:
                        accuracy = value
                    break
            
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

    repo_chunk_dir = os.path.join(CHUNK_STORE_DIR, repo_id)
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

    chunk_file_path = os.path.join(repo_chunk_dir, "chunks.json")

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

    decision_path = os.path.join(repo_profile_dir, "index_decision.json")
    with open(decision_path, "w", encoding="utf-8") as f:
        json.dump(decision, f, indent=2)


    manifest_path = os.path.join(repo_profile_dir, "index_manifest.json")
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
    # Initialize session if doesn't exist
    if q.session_id not in _SESSIONS:
        _SESSIONS[q.session_id] = {}
    
    session = _SESSIONS[q.session_id]
    user_input = q.user.strip()
    
    if not user_input:
        raise HTTPException(status_code=400, detail="'user' field cannot be empty")
    # -------------------------------------------------
    # 🧠 GENERIC QUESTION SHORT-CIRCUIT (NO REPO)
    # -------------------------------------------------
    if is_generic_question(user_input):
        return {
            "answer": (
                "Hello! 👋 I'm an AI assistant that understands code repositories.\n\n"
                "You can ask me questions about a project, its code, architecture, "
                "or how different parts of a repository work."
            ),
            "confidence": "High"
        }

    
    from rag.repo_detector import get_all_available_repos
    
    # Check if there's a pending question in session (needs repository selection)
    if "question" in session:
        # User is selecting a repository from the clarification list
        # Check if user input matches a repository name
        available_repos = get_all_available_repos(base_dir=BASE_DIR)
        user_input_lower = user_input.lower()
        
        # Try to find matching repo (exact match or contains repo name)
        matching_repo = None
        for repo in available_repos:
            repo_lower = repo.lower()
            # Exact match or user typed just the repo name
            if user_input_lower == repo_lower or user_input_lower == repo_lower.replace("-", " ") or user_input_lower == repo_lower.replace("_", " "):
                matching_repo = repo
                break
            # Check if user input contains repo name (e.g., "I want sentiment-analysis")
            if repo_lower in user_input_lower or user_input_lower in repo_lower:
                matching_repo = repo
                break
        
        if matching_repo:
            # User selected a repository
            question = session["question"]
            repo_id = matching_repo
            del session["question"]  # Clear stored question
            print(f"[*] User selected repository '{repo_id}' for session {q.session_id}")
            
            # Execute the query with selected repository
            try:
                result = rag_answer(
                    question=question,
                    repo_id=repo_id,
                    show_sources=q.show_sources,
                    show_confidence=q.show_confidence,
                )
                print(f"[+] Query completed for {repo_id} in session {q.session_id}")
                return result
            except Exception as e:
                print(f"[!] Error processing query: {e}")
                import traceback
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")
        else:
            # User input doesn't match any repo - treat as new question or ask for clarification
            available_repos = get_all_available_repos(base_dir=BASE_DIR)
            repos_list = "\n".join([f"{i+1}. {repo}" for i, repo in enumerate(available_repos)])
            return {
                "message": f"I couldn't match '{user_input}' to a repository. Please select one from the list:\n\n{repos_list}",
                "available_repos": available_repos
            }
    
    # No pending question - treat user input as a new question
    question = user_input
    
    # Try to detect repository from question
    from rag.repo_detector import detect_repo_from_question
    detection_result = detect_repo_from_question(question, base_dir=BASE_DIR)
    
    # Auto-detected unique match or project group - answer directly
    if detection_result["status"] == "unique_match":
        repo_id = detection_result["repo_id"]
        print(f"[+] Auto-detected repository: {repo_id} for session {q.session_id}")
        
        try:
            result = rag_answer(
                question=question,
                repo_id=repo_id,
                show_sources=q.show_sources,
                show_confidence=q.show_confidence,
            )
            print(f"[+] Query completed for {repo_id} in session {q.session_id}")
            return result
        except Exception as e:
            print(f"[!] Error processing query: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")
            
    elif detection_result["status"] == "project_group":
        # Project group detected - query across all repos
        matching_repos = detection_result["matching_repos"]
        project_base = detection_result["project_base_name"]
        print(f"[+] Auto-detected project group: {project_base} with repos: {', '.join(matching_repos)}")
        
        try:
            from rag.core import rag_answer_multi_repo
            result = rag_answer_multi_repo(
                question=question,
                repo_ids=matching_repos,
                show_sources=q.show_sources,
                show_confidence=q.show_confidence,
            )
            result["project_group"] = project_base
            result["searched_repos"] = matching_repos
            print(f"[+] Query completed across {len(matching_repos)} repositories")
            return result
        except Exception as e:
            print(f"[!] Error processing multi-repo query: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error processing multi-repo query: {str(e)}")
    else:
        # Needs clarification - store question in session and return available repos
        available_repos = get_all_available_repos(base_dir=BASE_DIR)
        if not available_repos:
            return {
                "message": "No repositories are currently indexed. Please index a repository first using the /index endpoint.",
                "available_repos": []
            }
        
        # Store question in session for follow-up
        session["question"] = question
        print(f"[*] Stored question in session {q.session_id}, requesting clarification")
        
        repos_list = "\n".join([f"{i+1}. {repo}" for i, repo in enumerate(available_repos)])
        return {
            "message": f"I found multiple repositories. Which one are you referring to?\n\n{repos_list}\n\nYou can simply type the repository name (e.g., 'sentiment-analysis').",
            "available_repos": available_repos
        }

# -------------------------------------------------
# 🔎 DEBUG / STATE ENDPOINT (DEMO FRIENDLY)
# -------------------------------------------------
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

