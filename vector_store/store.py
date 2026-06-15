import os
import numpy as np
import faiss
import pickle
from rank_bm25 import BM25Okapi

# -------------------------------------------------
# BASE DIRECTORY FOR ALL REPO VECTOR STORES
# -------------------------------------------------
BASE_VECTOR_DIR = os.path.join("vector_store", "repos")


def get_repo_dir(repo_id: str) -> str:
    """
    Returns the directory path for a given repo's vector store.
    """
    return os.path.join(BASE_VECTOR_DIR, repo_id)


class VectorStore:
    def __init__(self, embeddings, documents, metadatas):
        self.embeddings = embeddings
        self.documents = documents
        self.metadatas = metadatas

        tokenized_docs = [doc.lower().split() for doc in documents]
        self.bm25 = BM25Okapi(tokenized_docs)

        # FAISS index (L2)
        dim = len(embeddings[0])
        self.index = faiss.IndexFlatL2(dim)
        self.index.add(np.array(embeddings).astype("float32"))

    # -------------------------------------------------
    # COSINE SIMILARITY
    # -------------------------------------------------
    def cosine(self, a, b):
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

    # -------------------------------------------------
    # SEARCH
    # -------------------------------------------------
    def search(
        self,
        query_embedding,
        query_text,
        top_k=5,
        threshold=0.3,
        topic=None,
    ):
        results = []
        bm25_scores = self.bm25.get_scores(query_text.lower().split())

        for idx, (emb, doc, meta) in enumerate(
            zip(self.embeddings, self.documents, self.metadatas)
        ):
            if topic and meta.get("topic") != topic:
                continue

            vec_score = self.cosine(query_embedding, emb)
            bm25_score = bm25_scores[idx]

            final_score = 0.6 * vec_score + 0.4 * (bm25_score / 10)

            if final_score >= threshold:
                results.append({
                    "text": doc,
                    "metadata": meta,
                    "score": round(final_score, 3),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # -------------------------------------------------
    # SAVE VECTOR STORE (PER REPO)
    # -------------------------------------------------
    def save(self, repo_id: str):
        repo_dir = get_repo_dir(repo_id)
        os.makedirs(repo_dir, exist_ok=True)

        faiss_path = os.path.join(repo_dir, "index.faiss")
        meta_path = os.path.join(repo_dir, "metadata.pkl")

        faiss.write_index(self.index, faiss_path)

        with open(meta_path, "wb") as f:
            pickle.dump(
                {
                    "embeddings": self.embeddings,
                    "documents": self.documents,
                    "metadatas": self.metadatas,
                },
                f,
            )

    # -------------------------------------------------
    # LOAD VECTOR STORE (PER REPO)
    # -------------------------------------------------
    @staticmethod
    def load(repo_id: str):
        repo_dir = get_repo_dir(repo_id)
        faiss_path = os.path.join(repo_dir, "index.faiss")
        meta_path = os.path.join(repo_dir, "metadata.pkl")

        # ✅ IMPORTANT: return None (not exception)
        # so rag/core.py can gracefully handle auto-load
        if not os.path.exists(faiss_path) or not os.path.exists(meta_path):
            return None

        index = faiss.read_index(faiss_path)

        with open(meta_path, "rb") as f:
            data = pickle.load(f)

        store = VectorStore.__new__(VectorStore)
        store.embeddings = data["embeddings"]
        store.documents = data["documents"]
        store.metadatas = data["metadatas"]
        store.index = index

        # Rebuild BM25 exactly like __init__()
        store.bm25 = BM25Okapi(
            [doc.lower().split() for doc in store.documents]
        )

        return store
