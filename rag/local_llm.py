import os
from pathlib import Path
import requests
from dotenv import load_dotenv
from utils.errors import InferenceError
from utils.request_context import current_request_id

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
LLM_API_URL = "http://127.0.0.1:9001/generate"


def generate_answer(prompt: str, model: str = None) -> str:
    try:
        payload = {"prompt": prompt}
        if model:
            payload["model"] = model
        response = requests.post(os.getenv("LLM_API_URL", LLM_API_URL), json=payload,
            headers={"X-Request-ID": current_request_id()},
            timeout=float(os.getenv("LLM_CLIENT_TIMEOUT_SECONDS", "270")))
        if response.status_code >= 400:
            status = response.status_code if response.status_code in (500, 503, 504) else 500
            # Only propagate known public fields, never arbitrary upstream body text.
            try:
                code = response.json().get("detail", {}).get("code")
            except (ValueError, AttributeError):
                code = None
            messages = {"model_unavailable": "Requested model is unavailable",
                        "insufficient_memory": "Insufficient memory for the requested model",
                        "fallback_unavailable": "Configured fallback model is not installed locally"}
            default = "Inference service timed out" if status == 504 else "Inference service is unavailable" if status == 503 else "Inference service failed"
            raise InferenceError(code if code in messages else "inference_error", messages.get(code, default), status)
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("answer"), str) or not data["answer"].strip():
            raise InferenceError("invalid_response", "Inference service returned an invalid answer", 500)
        return data["answer"].strip()
    except requests.Timeout as exc:
        raise InferenceError("inference_timeout", "Inference service timed out", 504) from exc
    except requests.RequestException as exc:
        raise InferenceError("service_unavailable", "Cannot connect to the inference service", 503) from exc
    except ValueError as exc:
        raise InferenceError("invalid_response", "Inference service returned invalid data or configuration", 500) from exc
