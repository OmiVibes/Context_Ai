import re
import ollama


def clean(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"\n+", " ", text)
    return text.strip()


def embed_texts(texts: list[str], model="nomic-embed-text"):
    embeddings = []

    for text in texts:
        response = ollama.embeddings(
            model=model,
            prompt=clean(text)
        )
        embeddings.append(response["embedding"])

    return embeddings


def embed_query(query: str):
    return ollama.embeddings(
        model="nomic-embed-text",
        prompt=clean(query)
    )["embedding"]
