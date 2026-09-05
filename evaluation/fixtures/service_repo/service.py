from config import PORT


def health():
    return {"status": "ok", "port": PORT}


ROUTES = {"/health": health}
