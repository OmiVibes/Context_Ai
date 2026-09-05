"""Fast, non-RAG diagnostics for the separated local inference service."""
from __future__ import annotations
import argparse
import time
import requests


def preflight(models=(), base_url="http://127.0.0.1:9001"):
    result = {"service": False, "backend": False, "models": [], "requested_models": list(models), "missing_models": []}
    try:
        health = requests.get(base_url + "/health", timeout=5); health.raise_for_status()
        result["service"] = True; result["backend"] = health.json().get("backend") == "ready"
        listing = requests.get(base_url + "/models", timeout=5); listing.raise_for_status()
        result["models"] = listing.json().get("models", [])
        result["missing_models"] = [model for model in models if model not in result["models"]]
    except requests.RequestException as exc:
        result["error_category"] = "llm_service_unreachable"
        result["detail"] = type(exc).__name__
    if result["service"] and not result["backend"]: result["error_category"] = "ollama_unreachable"
    elif result["missing_models"]: result["error_category"] = "model_unavailable"
    return result


def warmup(model, base_url="http://127.0.0.1:9001"):
    started = time.perf_counter()
    try:
        response = requests.post(base_url + "/generate", json={"model": model, "prompt": "Reply with OK."}, timeout=150)
        response.raise_for_status()
        return {"requested_model": model, "actual_model": model, "success": True, "latency_seconds": round(time.perf_counter()-started, 3)}
    except requests.Timeout:
        return {"requested_model": model, "success": False, "category": "inference_timeout", "latency_seconds": round(time.perf_counter()-started, 3)}
    except requests.ConnectionError:
        return {"requested_model": model, "success": False, "category": "connection_reset", "latency_seconds": round(time.perf_counter()-started, 3)}
    except requests.RequestException as exc:
        return {"requested_model": model, "success": False, "category": "generation_error", "detail": type(exc).__name__, "latency_seconds": round(time.perf_counter()-started, 3)}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--model", action="append", default=[]); args=parser.parse_args()
    print(preflight(args.model))
    for model in args.model: print(warmup(model))
