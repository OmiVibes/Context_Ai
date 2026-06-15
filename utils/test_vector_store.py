import sys
import os

# -------------------------------------------------
# ENSURE PROJECT ROOT IS ON PATH
# -------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from vector_store.store import VectorStore
import numpy as np

# Dummy data
texts = ["hello world", "machine learning is fun"]
metas = [{"file": "a"}, {"file": "b"}]
embeddings = np.random.rand(2, 384).tolist()

# Create & save
vs = VectorStore(embeddings, texts, metas)
vs.save(repo_id="test-repo")

# Load again
vs2 = VectorStore.load(repo_id="test-repo")

print("Loaded docs:", vs2.documents)
