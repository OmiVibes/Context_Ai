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
    """Remove paired Markdown fence lines, preserving code and its whitespace."""
    lines = text.splitlines(keepends=True)
    output = []
    position = 0
    while position < len(lines):
        opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*", lines[position])
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            marker = opening[1]
            closing = re.compile(r" {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*[\r\n]*")
            end = next((i for i in range(position + 1, len(lines)) if closing.fullmatch(lines[i])), None)
            if end is not None:
                output.extend(lines[position + 1:end])
                position = end + 1
                continue
        # Unclosed fences and inline backticks might be source text: retain them.
        output.append(lines[position])
        position += 1
    return "".join(output).strip("\r\n")


def embed_texts(texts: list[str], model="nomic-embed-text"):
    embeddings = []

    for text in texts:
        embeddings.append(_embed(text, model))

    return embeddings


def embed_query(query: str):
    return _embed(query, "nomic-embed-text")
