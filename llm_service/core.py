from engine_router import get_engine

def run_inference(
    *,
    prompt: str,
    engine: str = "ollama",
    model: str = "mistral"
) -> str:
    llm = get_engine(engine=engine, model=model)
    return llm.generate(prompt)
