import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVICE_DIR = ROOT / "llm_service"
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
LOG = ROOT / "llm_service_detached.log"

with LOG.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
        ],
        cwd=str(SERVICE_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

print(process.pid)
