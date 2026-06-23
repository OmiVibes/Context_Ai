from fastapi import FastAPI
from pydantic import BaseModel
from core import run_inference

app = FastAPI(title="LLM Inference Service")

class GenerateRequest(BaseModel):
    prompt: str
    model: str = "qwen2.5:7b"

@app.post("/generate")
def generate(req: GenerateRequest):
    try:
        answer = run_inference(
            prompt=req.prompt,
            model=req.model
        )
    except Exception as exc:
        answer = f"Error generating answer: {exc}"

    return {"answer": answer}
