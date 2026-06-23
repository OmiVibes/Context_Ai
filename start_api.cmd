@echo off
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000 --lifespan off > "%~dp0uvicorn_api_cmd.log" 2>&1
