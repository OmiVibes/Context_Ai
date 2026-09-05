import os
from pathlib import Path
from dotenv import load_dotenv
from llm_service.engine_router import get_engine
from llm_service.engines.ollama import installed_local_models
from utils.errors import InferenceError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def run_inference(*, prompt: str, engine: str = "ollama", model: str = None) -> str:
    selected = model or os.getenv("LLM_DEFAULT_MODEL", "qwen2.5:7b")
    try:
        return get_engine(engine=engine, model=selected).generate(prompt)
    except InferenceError as exc:
        fallback = os.getenv("LLM_FALLBACK_MODEL", "").strip()
        # Honor explicit requests. Only configured, installed local fallbacks may run.
        if (model or engine != "ollama" or not fallback or fallback == selected
                or exc.code not in {"model_unavailable", "insufficient_memory"}):
            raise
        names = installed_local_models()
        canonical = lambda name: name if ":" in name else name + ":latest"
        if canonical(fallback) not in {canonical(name) for name in names}:
            raise InferenceError("fallback_unavailable", "Configured fallback model is not installed locally", 503) from exc
        return get_engine(engine=engine, model=fallback).generate(prompt)
