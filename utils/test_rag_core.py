import sys
import os

# -------------------------------------------------
# ENSURE PROJECT ROOT IS ON PATH
# -------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.core import rag_answer, register_repo
from vector_store.store import VectorStore
from app_processing.embeddings import embed_texts

# Dummy repo content
texts = [
    "This project uses a CNN model to detect brain tumors from MRI images."
]
metas = [{"file_path": "README.md"}]

# ✅ USE REAL EMBEDDING PIPELINE (IMPORTANT)
embeddings = embed_texts(texts)

# Create vector store
vs = VectorStore(embeddings, texts, metas)

# Register repo with RAG core
register_repo(
    repo_id="dummy-repo",
    repo_path=".",
    vector_store=vs
)

# Ask a question
result = rag_answer(
    question="What does this project do?",
    repo_id="dummy-repo",
    show_confidence=True
)

print(result)
