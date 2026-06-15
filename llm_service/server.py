from fastapi import FastAPI
from pydantic import BaseModel
from core import run_inference

app = FastAPI(title="LLM Inference Service")

class GenerateRequest(BaseModel):
    prompt: str
    model: str = "mistral"

@app.post("/generate")
def generate(req: GenerateRequest):
    answer = run_inference(
        prompt=req.prompt,
        model=req.model
    )
    return {"answer": answer}
