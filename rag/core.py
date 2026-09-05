# rag/core.py

import os
import json
from typing import Dict, Any, List, Optional

from rag.local_llm import generate_answer
from utils.questions import is_generic_question, greeting_answer
from utils.errors import InferenceError
from utils.repo_paths import validate_repo_id, repo_path as safe_repo_path, contained_path


from app_processing.embeddings import embed_query
from vector_store.store import VectorStore
from rag.repo_structure import infer_architecture
from rag.grounded import answer_from_results, positive_int

# -------------------------------------------------
# 🔐 GLOBAL REGISTRIES (MULTI-REPO SAFE)
# -------------------------------------------------
_VECTOR_STORES: Dict[str, VectorStore] = {}
_REPO_PATHS: Dict[str, Optional[str]] = {}
_REPO_PROFILES: Dict[str, Dict[str, Any]] = {}

# -------------------------------------------------
# CONFIDENCE SCORING
# -------------------------------------------------
def compute_confidence(results: List[dict]) -> str:
    if not results:
        return "Low"

    scores = [r["score"] for r in results]
    avg = sum(scores) / len(scores)

    if len(results) >= 3 and avg >= 0.65:
        return "High"
    if len(results) >= 2 and avg >= 0.45:
        return "Medium"

    return "Low"

# -------------------------------------------------
# LOAD VECTOR STORE FROM DISK
# -------------------------------------------------
def load_repo_from_disk(repo_id: str) -> Optional[VectorStore]:
    return VectorStore.load(repo_id)

# -------------------------------------------------
# LOAD REPO PROFILE FROM DISK
# -------------------------------------------------
def load_repo_profile(repo_id: str) -> Optional[Dict[str, Any]]:
    repo_id = validate_repo_id(repo_id)
    # Try new path structure first: repo_profiles/{repo_id}/profile.json
    path = str(contained_path(safe_repo_path("repo_profiles", repo_id), "profile.json"))
    if not os.path.exists(path):
        # Fallback to old path: repo_profiles/{repo_id}.json
        path = str(contained_path("repo_profiles", f"{repo_id}.json"))
        if not os.path.exists(path):
            return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# -------------------------------------------------
# CORE RAG ENTRY (CALLED BY MCP)
# -------------------------------------------------
def rag_answer(
    *,
    question: str,
    repo_id: str,
    show_sources: bool = False,
    show_confidence: bool = False,
) -> Dict[str, Any]:

    repo_id = validate_repo_id(repo_id)
    question = question.strip()
    q_lower = question.lower().strip()
    
    # ---------------------------------------------
    # 🔁 ENSURE VECTOR STORE IS LOADED
    # ---------------------------------------------
    if repo_id not in _VECTOR_STORES:
        vector_store = load_repo_from_disk(repo_id)
        if vector_store is None:
            return {
                "answer": f"Repository '{repo_id}' is not indexed. Please index it first using the /index endpoint with a valid repo_id. Use /debug/state to see available repositories.",
                "confidence": "Low",
            }

        # Try to find the actual repo path
        # Get workspace root from app.py if available, otherwise try common locations
        repo_path = None
        try:
            # Try to import WORKSPACE_ROOT from app
            import sys
            import importlib
            if 'app' in sys.modules:
                app_module = sys.modules['app']
                workspace_root = getattr(app_module, 'WORKSPACE_ROOT', None)
                if workspace_root:
                    repo_path = str(safe_repo_path(workspace_root, repo_id))
                    if not os.path.exists(repo_path):
                        repo_path = None
        except Exception:
            pass
        
        # Fallback to common locations if workspace_root not found
        if not repo_path:
            possible_paths = [
                str(safe_repo_path("repos", repo_id)),
                str(safe_repo_path(os.path.join(os.path.dirname(os.path.dirname(__file__)), "repos"), repo_id)),
            ]
            
            # Also check parent directory (workspace root)
            parent_dir = os.path.dirname(os.path.dirname(__file__))
            workspace_repo_path = str(safe_repo_path(os.path.dirname(parent_dir), repo_id))
            possible_paths.append(workspace_repo_path)
            
            for path in possible_paths:
                if os.path.exists(path):
                    repo_path = path
                    break
            
        profile = load_repo_profile(repo_id)
        
        register_repo(
            repo_id=repo_id,
            repo_path=repo_path,
            vector_store=vector_store,
            repo_profile=profile,
        )
        
        print(f"[+] Auto-loaded vector store for {repo_id}")

    # ---------------------------------------------
    # 🔁 ENSURE PROFILE IS LOADED
    # ---------------------------------------------
    if repo_id not in _REPO_PROFILES:
        profile = load_repo_profile(repo_id)
        if profile:
            _REPO_PROFILES[repo_id] = profile

    vector_store = _VECTOR_STORES[repo_id]
    repo_path = _REPO_PATHS.get(repo_id)
    profile = _REPO_PROFILES.get(repo_id)

    # ---------------------------------------------
    # 📌 METADATA QUESTIONS (DETERMINISTIC)
    # ---------------------------------------------
    if profile:
        # 🎯 ACCURACY (DETERMINISTIC)
        if "accuracy" in q_lower and profile and profile.get("accuracy"):
            return {
                "answer": f"The reported accuracy of this project is {profile['accuracy']}.",
                "confidence": "High"
            }

        if "title" in q_lower and profile.get("title"):
            return {"answer": profile["title"], "confidence": "High"}

        if "owner" in q_lower and profile.get("owner"):
            return {
                "answer": f"The project is authored by **{profile['owner']}**.",
                "confidence": "High",
            }

        if ("what is this project" in q_lower or "project about" in q_lower) and profile.get("description"):
            return {"answer": profile["description"], "confidence": "High"}

        if "tech stack" in q_lower and profile.get("tech_stack"):
            return {
                "answer": "Tech stack used:\n- " + "\n- ".join(profile["tech_stack"]),
                "confidence": "High",
            }

    # ---------------------------------------------
    # 🏗 ARCHITECTURE QUESTIONS (REPO-ONLY)
    # ---------------------------------------------
    if "architecture" in q_lower:
        return {
            "answer": infer_architecture(repo_path) if repo_path else "I could not find sufficient repository evidence to describe its architecture.",
            "confidence": "Low",
        }

    # ---------------------------------------------
    # 🔎 VECTOR SEARCH (SEMANTIC — EVIDENCE GATED)
    # ---------------------------------------------
    try:
        query_variants = [
            question,
            f"{question} overview",
            f"{question} summary",
            f"{question} documentation",
        ]

        all_results = []
        for qv in query_variants:
            try:
                emb = embed_query(qv)
                all_results.extend(
                    vector_store.search(
                        query_embedding=emb,
                        query_text=qv,
                        top_k=positive_int("RAG_TOP_K", 5),
                        threshold=0.25,
                    )
                )
            except InferenceError:
                raise
            except Exception as e:
                raise InferenceError("embedding_unavailable", "Embedding backend is unavailable", 503) from e
    except InferenceError:
        raise
    except Exception as e:
        print(f"[!] Error in vector search: {e}")
        return {
            "answer": f"Error during search: {str(e)}.",
            "confidence": "Low",
        }

    return answer_from_results(question, all_results, [repo_id], generate_answer,
                               show_confidence, compute_confidence)

# -------------------------------------------------
# 🔄 MULTI-REPO RAG (FOR FRONTEND/BACKEND GROUPS)
# -------------------------------------------------
def rag_answer_multi_repo(
    *,
    question: str,
    repo_ids: List[str],
    show_sources: bool = False,
    show_confidence: bool = False,
) -> Dict[str, Any]:
    """
    Query across multiple repositories (e.g., frontend + backend).
    
    This function searches across all specified repositories and combines
    the results to provide a comprehensive answer.
    """
    question = question.strip()
    q_lower = question.lower().strip()
    
    if is_generic_question(question):
        return greeting_answer()

    repo_ids = [validate_repo_id(repo_id) for repo_id in repo_ids]
    # Ensure all repos are loaded
    loaded_stores = {}
    for repo_id in repo_ids:
        if repo_id not in _VECTOR_STORES:
            vector_store = load_repo_from_disk(repo_id)
            if vector_store:
                profile = load_repo_profile(repo_id)
                register_repo(
                    repo_id=repo_id,
                    repo_path=_REPO_PATHS.get(repo_id),
                    vector_store=vector_store,
                    repo_profile=profile,
                )
            else:
                print(f"[!] Could not load vector store for {repo_id}, skipping...")
                continue
        loaded_stores[repo_id] = _VECTOR_STORES[repo_id]
    
    if not loaded_stores:
        return {
            "answer": f"None of the specified repositories ({', '.join(repo_ids)}) could be loaded.",
            "confidence": "Low",
        }
    
    # Search across all repositories
    all_results = []
    try:
        query_variants = [
            question,
            f"{question} overview",
            f"{question} summary",
            f"{question} documentation",
        ]
        
        for repo_id, vector_store in loaded_stores.items():
            for qv in query_variants:
                try:
                    emb = embed_query(qv)
                    results = vector_store.search(
                        query_embedding=emb,
                        query_text=qv,
                        top_k=positive_int("RAG_TOP_K", 5),
                        threshold=0.25,
                    )
                    # Add repo_id to metadata for tracking
                    for r in results:
                        all_results.append({**r, "repo_id": repo_id})
                except InferenceError:
                    raise
                except Exception as e:
                    raise InferenceError("embedding_unavailable", "Embedding backend is unavailable", 503) from e
    except InferenceError:
        raise
    except Exception as e:
        print(f"[!] Error in multi-repo vector search: {e}")
        return {
            "answer": f"Error during search: {str(e)}.",
            "confidence": "Low",
        }

    return answer_from_results(question, all_results, repo_ids, generate_answer,
                               show_confidence, compute_confidence)


# -------------------------------------------------
# 🧠 REPO REGISTRATION (CALLED BY MCP)
# -------------------------------------------------
def register_repo(
    repo_id: str,
    repo_path: Optional[str],
    vector_store: VectorStore,
    *,
    repo_profile: Optional[Dict[str, Any]] = None,
):
    repo_id = validate_repo_id(repo_id)
    _VECTOR_STORES[repo_id] = vector_store
    _REPO_PATHS[repo_id] = repo_path

    if repo_profile:
        _REPO_PROFILES[repo_id] = repo_profile
