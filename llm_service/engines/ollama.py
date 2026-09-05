import os
import requests
from .base import BaseEngine
from utils.errors import InferenceError

OLLAMA_API_URL = "http://127.0.0.1:11434/api/generate"


def installed_local_models():
    try:
        response = requests.get(OLLAMA_API_URL.rsplit("/", 1)[0] + "/tags", timeout=10)
        response.raise_for_status()
        return [m["name"] for m in response.json()["models"]
                if not m.get("remote_host") and not m.get("remote_model")]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise InferenceError("service_unavailable", "Cannot verify installed local models", 503) from exc


class OllamaEngine(BaseEngine):
    def __init__(self, model: str = "mistral"):
        self.model = model

    def generate(self, prompt: str) -> str:
        try:
            response = requests.post(OLLAMA_API_URL,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")))
            if response.status_code >= 400:
                try:
                    error = str(response.json().get("error", "")).lower()
                except ValueError:
                    error = ""
                if response.status_code == 404:
                    raise InferenceError("model_unavailable", "Requested model is not installed", 503)
                if any(word in error for word in ("memory", "out of memory", "allocation")):
                    raise InferenceError("insufficient_memory", "Insufficient memory to load the requested model", 503)
                if response.status_code in (408, 504):
                    raise InferenceError("inference_timeout", "Inference engine timed out", 504)
                if response.status_code in (429, 502, 503):
                    raise InferenceError("service_unavailable", "Inference engine is unavailable", 503)
                raise InferenceError("inference_failure", "Inference engine failed", 500)
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("response"), str) or not data["response"].strip():
                raise InferenceError("invalid_response", "Inference engine returned an invalid answer", 500)
            return data["response"].strip()
        except requests.Timeout as exc:
            raise InferenceError("inference_timeout", "Inference engine timed out", 504) from exc
        except requests.RequestException as exc:
            raise InferenceError("service_unavailable", "Cannot connect to the local inference engine", 503) from exc
        except ValueError as exc:
            raise InferenceError("invalid_response", "Inference engine returned invalid data or configuration", 500) from exc
