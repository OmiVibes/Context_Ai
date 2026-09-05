"""Public, bounded errors shared by the API, RAG and inference service."""
class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def detail(self):
        return {"code": self.code, "message": str(self)}


class InferenceError(ServiceError):
    pass


def install_error_handlers(app):
    from fastapi.responses import JSONResponse
    from utils.repo_paths import InvalidRepoId

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail()})

    @app.exception_handler(InvalidRepoId)
    async def invalid_repo(request, exc):
        return JSONResponse(status_code=400, content={"detail": {"code": "invalid_repo_id", "message": str(exc)}})
