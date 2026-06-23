import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
LOG = ROOT / "uvicorn_api_detached.log"

with LOG.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--lifespan",
            "off",
        ],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

print(process.pid)
