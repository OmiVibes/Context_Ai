import re

GENERIC_QUERIES = {
    "hi", "hello", "hey", "hi there", "hello there", "how are you",
    "how are you doing", "who are you", "what are you", "what can you do",
    "help", "introduce yourself", "tell me about yourself", "what is your purpose",
    "what can you help with", "what do you do", "who is this", "what is this",
}


def is_generic_question(question: str) -> bool:
    text = re.sub(r"[!?.]+$", "", question.strip().lower()).strip()
    return text in GENERIC_QUERIES


def greeting_answer():
    return {"answer": "Hello! I'm an AI assistant that understands code repositories. Ask me about a project's code, architecture, or implementation.",
            "confidence": "High"}
