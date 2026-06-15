import re

MAX_TOKENS = 200


def infer_topic(header: str) -> str:
    h = header.lower()

    if any(k in h for k in ["architecture", "workflow", "pipeline", "system"]):
        return "architecture"
    if any(k in h for k in ["model", "cnn", "resnet", "network"]):
        return "model"
    if any(k in h for k in ["emotion", "psychology", "analysis", "drawing"]):
        return "domain"
    if any(k in h for k in ["limitation", "constraint", "warning"]):
        return "limitations"
    if any(k in h for k in ["overview", "purpose", "introduction"]):
        return "overview"

    return "general"


def split_sections(text: str):
    # ORIGINAL: Markdown-aware splitting
    sections = re.split(r"\n(?=#+ )", text)
    parsed = []

    for sec in sections:
        lines = sec.splitlines()
        if not lines:
            continue

        header = lines[0].replace("#", "").strip()
        body = " ".join(lines[1:]).strip()

        if body:
            parsed.append((header, body))

    return parsed


def split_code_blocks(text: str):
    """
    Adds support for code languages:
    - Python: def, class
    - JS/TS: function, export default, const name = () => {}
    - Java/C++/C#: class, public/private methods
    - HTML: split on major tags
    """
    split_patterns = [
        r"\n(?=def )",
        r"\n(?=class )",
        r"\n(?=async def )",
        r"\n(?=function )",
        r"\n(?=export )",
        r"\n(?=public )",
        r"\n(?=private )",
        r"\n(?=protected )",
        r"\n(?=<div)",
        r"\n(?=<section)",
        r"\n(?=<script)",
    ]

    combined = "|".join(split_patterns)

    parts = re.split(combined, text)
    cleaned = []

    for part in parts:
        chunk = part.strip()
        if chunk:
            cleaned.append(chunk)

    return cleaned


def chunk_large_text_fallback(text: str, metadata: dict):
    """
    Fallback chunk slicing if none of the other splitters apply.
    """
    words = text.split()
    for i in range(0, len(words), MAX_TOKENS):
        yield {
            "text": " ".join(words[i:i + MAX_TOKENS]),
            "metadata": metadata
        }


def chunk_text(text: str, metadata: dict):
    chunks = []

    # STEP 1 — Markdown section-based chunking
    sections = split_sections(text)

    # If markdown style split worked
    if len(sections) > 1:
        for header, content in sections:
            topic = infer_topic(header)
            words = content.split()

            for i in range(0, len(words), MAX_TOKENS):
                chunks.append({
                    "text": " ".join(words[i:i + MAX_TOKENS]),
                    "metadata": {
                        **metadata,
                        "section": header,
                        "topic": topic
                    }
                })
        return chunks

    # STEP 2 — Code-based splitting
    blocks = split_code_blocks(text)

    if len(blocks) > 1:
        for block in blocks:
            words = block.split()
            for i in range(0, len(words), MAX_TOKENS):
                chunks.append({
                    "text": " ".join(words[i:i + MAX_TOKENS]),
                    "metadata": {
                        **metadata,
                        "section": "code",
                        "topic": "general"
                    }
                })
        return chunks

    # STEP 3 — Fallback raw slicing
    for chunk in chunk_large_text_fallback(text, {
        **metadata,
        "section": "raw",
        "topic": "general"
    }):
        chunks.append(chunk)

    return chunks
