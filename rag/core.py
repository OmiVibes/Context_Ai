# rag/core.py

import os
import json
from typing import Dict, Any, List, Optional

from rag.local_llm import generate_answer


from app_processing.embeddings import embed_query
from vector_store.store import VectorStore
from rag.repo_structure import infer_architecture
from rag.prompt_builder import build_user_prompt

# -------------------------------------------------
# SYSTEM PROMPT (SINGLE SOURCE OF TRUTH)
# -------------------------------------------------
SYSTEM_PROMPT = """
You are a repository-grounded assistant.

STRICT RULES (must follow):
- Answer ONLY using facts explicitly present in the provided context.
- Do NOT rephrase or summarize beyond what is written.
- Do NOT add adjectives like "high accuracy", "instant", "powerful", etc unless they appear verbatim.
- If the answer is not directly stated, say:
  "I could not find this information in the repository."
- Do NOT use general knowledge about the topic.
- Be factual and literal.
"""

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
    # Try new path structure first: repo_profiles/{repo_id}/profile.json
    path = os.path.join("repo_profiles", repo_id, "profile.json")
    if not os.path.exists(path):
        # Fallback to old path: repo_profiles/{repo_id}.json
        path = os.path.join("repo_profiles", f"{repo_id}.json")
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
                    repo_path = os.path.join(workspace_root, repo_id)
                    if not os.path.exists(repo_path):
                        repo_path = None
        except Exception:
            pass
        
        # Fallback to common locations if workspace_root not found
        if not repo_path:
            possible_paths = [
                os.path.join("repos", repo_id),
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "repos", repo_id),
            ]
            
            # Also check parent directory (workspace root)
            parent_dir = os.path.dirname(os.path.dirname(__file__))
            workspace_repo_path = os.path.join(os.path.dirname(parent_dir), repo_id)
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
    if "architecture" in q_lower and repo_path and os.path.exists(repo_path):
        arch_data = infer_architecture(repo_path)

        prompt = f"""
QUESTION:
{question}

REPO STRUCTURE:
{arch_data}

If unclear, say:
"I could not find this information in the repository."
"""

        answer = generate_answer(prompt)

        return {
            "answer": answer,
            "confidence": "Medium",
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
                        top_k=5,
                        threshold=0.25,
                    )
                )
            except Exception as e:
                print(f"[!] Error embedding query variant '{qv}': {e}")
                continue
    except Exception as e:
        print(f"[!] Error in vector search: {e}")
        return {
            "answer": f"Error during search: {str(e)}.",
            "confidence": "Low",
        }

    # Deduplicate
    seen = set()
    results = []
    for r in all_results:
        key = r["text"].strip()
        if key not in seen:
            seen.add(key)
            results.append(r)

    # ---------------------------------------------
    # 🚫 NO EVIDENCE → HARD STOP (NO LLM)
    # ---------------------------------------------
    if not results:
        return {
            "answer": "I could not find this information in the repository.",
            "confidence": "Low",
        }

    # ---------------------------------------------
    # ✅ EVIDENCE EXISTS → LLM ALLOWED
    # ---------------------------------------------
    try:
        MAX_CONTEXT_CHARS = 3000  # hard safety limit

        raw_context = "\n\n".join(r["text"] for r in results)

        # 🔒 hard truncate context (MOST IMPORTANT FIX)
        context = raw_context[:MAX_CONTEXT_CHARS]

        print(f"[RAG] Context truncated to {len(context)} characters")

        user_prompt = build_user_prompt(question, context)

        answer = generate_answer(user_prompt)
    except Exception as e:
        print(f"[!] Error calling inference service: {e}")
        error_msg = str(e)
        if "memory" in error_msg.lower() or "system memory" in error_msg.lower():
            return {
                "answer": "The inference engine ran out of memory while generating the answer.",
                "confidence": "Low",
            }
        return {
            "answer": f"Error generating answer: {error_msg}. The inference service may be unavailable.",
            "confidence": "Low",
        }
    confidence = compute_confidence(results)

    final = {"answer": answer}

    if show_confidence:
        final["confidence"] = confidence

    if show_sources:
        final["sources"] = [
            {
                "file": r["metadata"].get("file_path"),
                "section": r["metadata"].get("section"),
                "score": r["score"],
            }
            for r in results
        ]

    return final

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
    
    # Handle greetings (same as single repo)
    greeting_exact_matches = {
        "hi", "hello", "hey", "hi there", "hello there",
        "who are you", "what are you", "who is this", "what is this",
        "how are you", "how are you doing", "how's it going",
        "introduce yourself", "tell me about yourself", "what do you do",
        "what can you do", "what can you help with", "what is your purpose"
    }
    
    if q_lower in greeting_exact_matches or any(kw in q_lower for kw in [
        "who are you", "what are you", "how are you", "introduce yourself",
        "tell me about yourself", "what do you do", "what can you do",
        "what is your purpose", "what can you help"
    ]):
        return {
            "answer": (
                "Hello! I'm an AI assistant that understands repositories.\n\n"
                "I analyze source code, documentation, and structure to answer questions "
                "about specific projects. I can help you understand:\n"
                "- What a project does and how it works\n"
                "- Code architecture and structure\n"
                "- Implementation details and patterns\n"
                "- How to use or contribute to the project\n\n"
                "Just ask me questions about any indexed repository!"
            ),
            "confidence": "High",
        }
    
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
                        top_k=5,
                        threshold=0.25,
                    )
                    # Add repo_id to metadata for tracking
                    for r in results:
                        r["repo_id"] = repo_id
                        all_results.append(r)
                except Exception as e:
                    print(f"[!] Error searching {repo_id} with variant '{qv}': {e}")
                    continue
    except Exception as e:
        print(f"[!] Error in multi-repo vector search: {e}")
        return {
            "answer": f"Error during search: {str(e)}.",
            "confidence": "Low",
        }

    # Deduplicate results (same text from different repos)
    seen = set()
    results = []
    for r in all_results:
        key = r["text"].strip()
        if key not in seen:
            seen.add(key)
            results.append(r)
    
    # Sort by score (best results first)
    results.sort(key=lambda x: x["score"], reverse=True)
    # Take top 10 results across all repos
    results = results[:10]
    
    if not results:
        return {
            "answer": f"I could not find this information across the repositories: {', '.join(repo_ids)}.",
            "confidence": "Low",
        }
    
    # Generate answer from combined context
    try:
        # Add repo context to the prompt
        repos_context = ", ".join(repo_ids)
        context = "\n\n".join([
            f"[From {r['repo_id']}]: {r['text']}" 
            for r in results
        ])
        
        user_prompt = f"""This question is about a project that spans multiple repositories: {repos_context}

QUESTION:
{question}

CONTEXT FROM REPOSITORIES:
{context}

Please provide a comprehensive answer based on information from across all these repositories."""

        answer = generate_answer(user_prompt)
    except Exception as e:
        print(f"[!] Error calling inference service: {e}")
        error_msg = str(e)
        if "memory" in error_msg.lower() or "system memory" in error_msg.lower():
            return {
                "answer": "The inference engine ran out of memory while generating the answer.",
                "confidence": "Low",
            }
        return {
            "answer": f"Error during search: {str(e)}.",
            "confidence": "Low",
        }
    
    confidence = compute_confidence(results)
    
    final = {"answer": answer}
    
    if show_confidence:
        final["confidence"] = confidence
    
    if show_sources:
        final["sources"] = [
            {
                "repo_id": r["repo_id"],
                "file": r["metadata"].get("file_path"),
                "section": r["metadata"].get("section"),
                "score": r["score"],
            }
            for r in results
        ]
    
    return final


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
    _VECTOR_STORES[repo_id] = vector_store
    _REPO_PATHS[repo_id] = repo_path

    if repo_profile:
        _REPO_PROFILES[repo_id] = repo_profile
