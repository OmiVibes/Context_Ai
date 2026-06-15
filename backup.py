import os
import json
import subprocess
import shutil
import ollama
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app_processing.file_loader import load_repo_files
from app_processing.chunker import chunk_text
from app_processing.embeddings import embed_texts, embed_query
from vector_store.store import VectorStore

# NEW ✨
from rag.repo_structure import infer_architecture

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REPO_NAME = "facemask-detector"
REPO_PATH = os.path.join(BASE_DIR, "repos", REPO_NAME)

GITHUB_REPO_URL = "https://github.com/Dharani-Barigeda/facemask-detector.git"
GITHUB_BRANCH = "main"

# ✨ NEW FILE TO TRACK LAST INDEXED COMMIT
LAST_COMMIT_FILE = os.path.join(BASE_DIR, "vector_store", "last_commit.txt")

# -------------------------------------------------
# APP
# -------------------------------------------------
app = FastAPI(title="Context Assist – Talk to Repository")

vector_store = None
MANIFEST = None
PROJECT_PROFILE = None

# -------------------------------------------------
# MODELS
# -------------------------------------------------
class Query(BaseModel):
    question: str
    show_sources: bool = False
    show_confidence: bool = False

# -------------------------------------------------
# GENERIC QUESTION ROUTING (PROFILE LOOKUP)
# -------------------------------------------------
def generic_question_type(question: str):
    q = question.lower()

    if "name of the project" in q:
        return "project_name"
    if "what does" in q and any(w in q for w in ["project", "repository", "psyreport"]):
        return "detailed_description"
    if "useful" in q or "industry" in q:
        return "industry_usefulness"
    if "problem" in q:
        return "problem_statement"
    if "who" in q and any(w in q for w in ["user", "intended"]):
        return "intended_users"
    if "limitation" in q or "limitations" in q:
        return "limitations"

    return None

# -------------------------------------------------
# TOPIC CLASSIFICATION (RAG)
# -------------------------------------------------
def classify_topic(question: str):
    q = question.lower()

    if any(k in q for k in ["architecture", "workflow", "pipeline", "system"]):
        return "architecture"
    if any(k in q for k in ["model", "cnn", "resnet", "network"]):
        return "model"
    if any(k in q for k in ["emotion", "psychology", "drawing", "analysis"]):
        return "domain"
    if any(k in q for k in ["limitation", "risk", "warning"]):
        return "limitations"

    return None

# -------------------------------------------------
# CONFIDENCE SCORING
# -------------------------------------------------
def compute_confidence(results: list) -> str:
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
# VALIDATE A REPO IS COMPLETE (NO PARTIAL CLONE)
# -------------------------------------------------
def repo_is_valid():
    # .git must exist
    if not os.path.exists(os.path.join(REPO_PATH, ".git")):
        return False

    # HEAD must resolve
    try:
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_PATH
        )
        return True
    except Exception:
        return False

# -------------------------------------------------
# ⭐ NEW SAFE WRAPPER FOR COMMIT READ
# -------------------------------------------------
def safe_commit():  # ⭐ NEW
    try:
        if not os.path.exists(os.path.join(REPO_PATH, ".git")):
            return None
        return subprocess.check_output(
            ["git", "log", "-1", "--pretty=format:%h"],
            cwd=REPO_PATH,
            stderr=subprocess.STDOUT
        ).decode().strip()
    except Exception:
        return None

# -------------------------------------------------
# REPO SYNC (CLONE OR UPDATE WITH FAILSAFE)
# -------------------------------------------------
def sync_repo():
    # ---------------------------------
    # If repo folder exists but invalid
    # ---------------------------------
    if os.path.exists(REPO_PATH) and not repo_is_valid():
        print("⚠️ Detected corrupted or partially cloned repo!")
        print("🗑 Removing invalid repo...")
        try:
            shutil.rmtree(REPO_PATH)
        except Exception as e:
            print("❌ Failed to remove corrupted repo:", e)

    # ---------------------------------
    # Clone if missing
    # ---------------------------------
    if not os.path.exists(REPO_PATH):
        print("📥 Repository not present — cloning fresh...")

        for attempt in range(1, 4):  # retry 3 times
            print(f"🔁 Clone attempt {attempt}...")
            try:
                subprocess.check_call(
                    ["git", "clone", "--branch", GITHUB_BRANCH, GITHUB_REPO_URL, REPO_PATH]
                )
                if repo_is_valid():
                    print("✅ Clone succeeded")
                    break
            except Exception as e:
                print(f"❌ Clone failed: {e}")

            if attempt == 3:
                raise RuntimeError("🚨 Git clone failed after 3 attempts!")

        return

    # ---------------------------------
    # Repo exists & is valid — pull
    # ---------------------------------
    print("🔄 Repository exists — syncing with GitHub")

    try:
        subprocess.check_call(["git", "fetch", "origin"], cwd=REPO_PATH)
        subprocess.check_call(
            ["git", "reset", "--hard", f"origin/{GITHUB_BRANCH}"],
            cwd=REPO_PATH
        )
    except Exception as e:
        print("❌ Git sync failed:", e)
        print("⚠️ Removing repo and retrying...")
        shutil.rmtree(REPO_PATH)
        return sync_repo()

    commit = safe_commit()  # ⭐ changed
    print(f"✅ Repository synced to commit: {commit}")

# -------------------------------------------------
# INDEX BUILDER (WITH CHUNK PRINTING 🔥)
# -------------------------------------------------
def build_index():
    global vector_store, MANIFEST, PROJECT_PROFILE

    print("\n🔁 Re-indexing repository...\n")

    # Load project profile if exists
    profile_path = os.path.join(REPO_PATH, "PROJECT_PROFILE.json")
    PROJECT_PROFILE = None
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            PROJECT_PROFILE = json.load(f)

    files = load_repo_files(REPO_PATH)
    chunks = []

    for doc in files:
        if doc["metadata"].get("file_path") == "REPO_MANIFEST.md":
            MANIFEST = doc["text"].strip()

        chunks.extend(chunk_text(doc["text"], doc["metadata"]))

    print("\n📦 CHUNKS GENERATED (REAL-TIME VIEW)\n")

    for i, c in enumerate(chunks, start=1):
        print("=" * 100)
        print(f"Chunk #{i}")
        print(f"File   : {c['metadata'].get('file_path')}")
        print(f"Section: {c['metadata'].get('section')}")
        print("-" * 60)
        print(c["text"][:700])
        print("=" * 100 + "\n")

    # -----------------------------------------------------
    # NEW ✨ Skip indexing if repo has no meaningful content
    # -----------------------------------------------------
    total_chars = sum(len(c["text"]) for c in chunks)
    if len(chunks) == 0 or total_chars < 500:
        print("⚠️ No usable content found in repository. Skipping embedding.")

        # Save marker so startup knows it's empty
        no_content_flag = os.path.join(BASE_DIR, "vector_store", "no_content.txt")
        with open(no_content_flag, "w") as f:
            f.write("NO_CONTENT")

        return    

    texts = [c["text"] for c in chunks]
    metas = [c["metadata"] for c in chunks]
    embeddings = embed_texts(texts)

    vector_store = VectorStore(embeddings, texts, metas)

    # Save FAISS + metadata
    from pathlib import Path
    vs_dir = os.path.join(BASE_DIR, "vector_store")
    os.makedirs(vs_dir, exist_ok=True)
    vector_store.save(
        os.path.join(vs_dir, "index.faiss"),
        os.path.join(vs_dir, "metadata.pkl")
    )

    print("\n💾 Vector store saved to disk")
    print(f"📦 TOTAL CHUNKS: {len(chunks)}\n")

    # ✨ NEW — SAVE LAST INDEXED COMMIT
    commit = safe_commit()  # ⭐ changed

    with open(LAST_COMMIT_FILE, "w", encoding="utf-8") as f:
        f.write(commit)

    print(f"🔐 Saved last indexed commit: {commit}")

# -------------------------------------------------
# STARTUP
# -------------------------------------------------
@app.on_event("startup")
def startup():
    global vector_store

    sync_repo()

    # ✨ NEW — READ CURRENT GIT COMMIT
    current_commit = safe_commit()  # ⭐ changed

    print(f"\n📌 Current repo commit: {current_commit}")

    old_commit = None
    if os.path.exists(LAST_COMMIT_FILE):
        with open(LAST_COMMIT_FILE, "r", encoding="utf-8") as f:
            old_commit = f.read().strip()
        print(f"📌 Last indexed commit: {old_commit}")

        # NEW ✨ If repo has no content, do NOT load vector store
        no_content_flag = os.path.join(BASE_DIR, "vector_store", "no_content.txt")
        if os.path.exists(no_content_flag):
            print("⚠️ Skipping vector store load — repo contains no usable content.")
            vector_store = None
            return

    faiss_path = os.path.join(BASE_DIR, "vector_store", "index.faiss")
    meta_path = os.path.join(BASE_DIR, "vector_store", "metadata.pkl")

    # ✨ If no change, skip rebuild
    if (
        old_commit == current_commit and
        os.path.exists(faiss_path) and
        os.path.exists(meta_path)
    ):
        print("⏩ No repo changes — loading cached vector store")
        vector_store = VectorStore.load(faiss_path, meta_path)

    else:
        print("🔁 Repo changed or no index exists — rebuilding index...")
        build_index()

    print("🚀 Server ready")
    print("🔔 GitHub webhook active")

# -------------------------------------------------
# MAIN QA ENDPOINT (STRICT GUARDRAILS)
# -------------------------------------------------
@app.post("/ask")
def ask(q: Query):
    question = q.question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Empty question")

    q_lower = question.lower()

    # -------------------------------
    # GREETINGS / IDENTITY ANSWERS
    # -------------------------------
    greetings = ["hi", "hello", "hey", "who are you", "what are you"]
    if q_lower in greetings:
        return {
            "answer": (
                f"Hello! 👋 I'm the built-in AI assistant for the **{REPO_NAME}** project.\n\n"
                f"I read and analyze all the files and notebooks in this repository.\n"
                f"Ask me about its purpose, design, architecture, workflow and usage.\n"
                f"I do NOT add outside knowledge."
            )
        }

    # -------------------------------
    # TOPIC FIRST: ARCHITECTURE
    # -------------------------------
    topic = classify_topic(question)
    if topic == "architecture":
        arch_data = infer_architecture(REPO_PATH)

        prompt = f"""
You are the AI assistant for ONE repository.
Use ONLY the provided context.
Do NOT use outside knowledge.
If unclear, respond: "I couldn't find relevant information in the repository."

PROJECT:
{REPO_NAME}

QUESTION:
{question}

REPO CONTEXT:
{arch_data}

Answer in 2–4 sentences.
"""
        resp = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.05},
        )
        return {
            "answer": resp["message"]["content"].strip(),
            "confidence": "Medium"
        }

    # -------------------------------
    # PROFILE ANSWERS
    # -------------------------------
    q_type = generic_question_type(question)
    if q_type and PROJECT_PROFILE:
        ans = PROJECT_PROFILE.get(q_type)
        if isinstance(ans, list):
            ans = ", ".join(ans)

        response = {"answer": ans}
        if q.show_confidence:
            response["confidence"] = "High"
        return response

    # -------------------------------
    # VECTOR SEARCH
    # -------------------------------

    # NEW ✨ If there is no usable repo content
    if vector_store is None:
        return {
            "answer": "This repository contains no meaningful content to analyze.",
            "confidence": "Low"
        }
    query_embedding = embed_query(question)


    results = vector_store.search(
        query_embedding=query_embedding,
        query_text=question,
        top_k=5,
        threshold=0.3,
        topic=topic
    )

    # -------------------------------
    # NO MATCH FALLBACK
    # -------------------------------
    if not results:
        arch_data = infer_architecture(REPO_PATH)

        prompt = f"""
No relevant chunks found in this repository.
Use ONLY this structure summary to infer.

QUESTION:
{question}

REPO STRUCTURE:
{arch_data}

If answer cannot be inferred, say:
"I couldn't find relevant information in the repository."
"""

        resp = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.05},
        )

        return {
            "answer": resp["message"]["content"].strip(),
            "confidence": "Low"
        }

    # -------------------------------
    # BUILD PROMPT FOR LLM (STRICT)
    # -------------------------------
    context = "\n\n".join(r["text"] for r in results)

    prompt = f"""
You are the AI assistant for ONE repository only.
Use ONLY the context below.
Do NOT add external or assumed knowledge.
If the answer is not present, reply:
"I couldn't find relevant information in the repository."

PROJECT:
{REPO_NAME}

QUESTION:
{question}

CONTEXT:
{context}

Answer concisely in 2–3 sentences.
"""

    response = ollama.chat(
        model="llama3.1",
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.05}
    )

    answer = response["message"]["content"].strip()
    confidence = compute_confidence(results)

    # -------------------------------
    # LOW CONFIDENCE OVERRIDE 🛡️
    # -------------------------------
    if confidence == "Low":
        return {
            "answer": "I couldn't find relevant information in the repository.",
            "confidence": "Low"
        }

    final = {"answer": answer}

    if q.show_confidence:
        final["confidence"] = confidence

    if q.show_sources:
        final["sources"] = [
            {
                "file": r["metadata"].get("file_path"),
                "section": r["metadata"].get("section"),
                "score": r["score"]
            }
            for r in results
        ]

    return final

# -------------------------------------------------
# GITHUB WEBHOOK (AUTO UPDATE)
# -------------------------------------------------
@app.post("/github/webhook")
async def github_webhook(request: Request):
    event = request.headers.get("X-GitHub-Event")

    if event != "push":
        return {"status": "ignored"}

    print("\n📩 GitHub push event received\n")

    sync_repo()
    build_index()

    return {"status": "updated"}
