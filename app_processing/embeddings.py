import re
import ollama
import httpx
from utils.errors import InferenceError


def _embed(text, model):
    try:
        return ollama.embeddings(model=model, prompt=clean(text))["embedding"]
    except httpx.TimeoutException as exc:
        raise InferenceError("embedding_timeout", "Embedding engine timed out", 504) from exc
    except (ConnectionError, httpx.RequestError, ollama.ResponseError) as exc:
        raise InferenceError("embedding_unavailable", "Embedding model or engine is unavailable", 503) from exc


def clean(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"\n+", " ", text)
    return text.strip()


def embed_texts(texts: list[str], model="nomic-embed-text"):
    embeddings = []

    for text in texts:
        embeddings.append(_embed(text, model))

    return embeddings


def embed_query(query: str):
    return _embed(query, "nomic-embed-text")
