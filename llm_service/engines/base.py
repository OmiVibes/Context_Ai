from abc import ABC, abstractmethod

class BaseEngine(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        pass
