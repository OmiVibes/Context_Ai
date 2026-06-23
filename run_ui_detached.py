import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STREAMLIT = ROOT / "venv" / "Scripts" / "streamlit.exe"
LOG = ROOT / "streamlit_ui_detached.log"

with LOG.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(
        [
            str(STREAMLIT),
            "run",
            "ui/streamlit_app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            "8501",
        ],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

print(process.pid)
