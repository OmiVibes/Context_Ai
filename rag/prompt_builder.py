def build_prompt(question: str, context: str) -> str:
    return f"""
You are an expert technical assistant.

Use ONLY the provided repository context to answer the question.
DO NOT repeat sections unnecessarily.
Give a clear, concise, well-structured answer.

If the answer is not found, say:
"I could not find this information in the repository."

--- CONTEXT ---
{context}

--- QUESTION ---
{question}

--- ANSWER (be concise, 5–7 lines max) ---
"""


# -------------------------------------------------
# ALIAS FOR RAG CORE (DO NOT REMOVE)
# -------------------------------------------------
def build_user_prompt(question: str, context: str) -> str:
    return build_prompt(question, context)
