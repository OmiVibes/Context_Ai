import os
from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel, Field
from llm_service.core import run_inference
from llm_service.engines.ollama import installed_local_models
from utils.errors import InferenceError, install_error_handlers

app = FastAPI(title="LLM Inference Service")
install_error_handlers(app)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: Optional[str] = Field(default=None, min_length=1)


@app.post("/generate")
def generate(req: GenerateRequest):
    try:
        return {"answer": run_inference(prompt=req.prompt, model=req.model)}
    except InferenceError:
        raise
    except Exception as exc:
        raise InferenceError("inference_failure", "Unexpected inference engine failure", 500) from exc


@app.get("/models")
def models():
    """Expose only models the inference service can run locally."""
    default = os.getenv("LLM_DEFAULT_MODEL", "qwen2.5:7b")
    available = installed_local_models()
    return {"default_model": default, "models": available}


@app.get("/health")
def health():
    try:
        models = installed_local_models()
        return {"status": "ready", "backend": "ready", "models_available": len(models)}
    except InferenceError:
        # The service process is alive even when its local engine cannot be reached.
        return {"status": "degraded", "backend": "unavailable", "models_available": 0}
