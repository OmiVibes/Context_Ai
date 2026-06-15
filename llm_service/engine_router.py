from engines.ollama import OllamaEngine

def get_engine(engine: str = "ollama", model: str = "mistral"):
    if engine == "ollama":
        return OllamaEngine(model=model)

    # future
    # if engine == "deepseek":
    #     return DeepSeekEngine(model=model)

    raise ValueError(f"Unknown engine: {engine}")
