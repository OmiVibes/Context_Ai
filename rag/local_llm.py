# rag/local_llm.py

import requests
from requests.exceptions import RequestException, Timeout

LLM_API_URL = "http://127.0.0.1:9001/generate"

def generate_answer(prompt: str) -> str:
    """
    Sends prompt to the local inference service and returns the generated answer.
    This function MUST NOT contain Ollama-specific logic.
    """

    payload = {
        "prompt": prompt
    }

    try:
        resp = requests.post(
            LLM_API_URL,
            json=payload,
            timeout=60
        )
        resp.raise_for_status()

        data = resp.json()

        # 🔐 Defensive check
        if not isinstance(data, dict) or "answer" not in data:
            raise ValueError("Invalid response format from inference service")

        return str(data["answer"]).strip()

    except Timeout:
        raise RuntimeError("Inference service timed out while generating response")

    except RequestException as e:
        raise RuntimeError(f"Inference service connection failed: {e}")

    except Exception as e:
        raise RuntimeError(f"Inference service error: {e}")
