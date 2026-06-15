import requests
from .base import BaseEngine

OLLAMA_API_URL = "http://127.0.0.1:11434/api/generate"

class OllamaEngine(BaseEngine):
    def __init__(self, model: str = "mistral"):
        self.model = model

    def generate(self, prompt: str) -> str:
        print(f"[LLM] Prompt length: {len(prompt)} characters")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }

        try:
            resp = requests.post(
                OLLAMA_API_URL,
                json=payload,
                timeout=300
            )
            resp.raise_for_status()

            data = resp.json()

            if "response" not in data:
                raise RuntimeError(f"Ollama returned unexpected response: {data}")

            return data["response"].strip()

        except requests.exceptions.Timeout:
            raise RuntimeError("Inference service timed out while generating response")

        except requests.exceptions.ConnectionError:
            raise RuntimeError("Could not connect to Ollama (is it running on 11434?)")

        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Ollama HTTP error: {e}")
