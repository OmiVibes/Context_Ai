import subprocess
import json
import time
import sys

proc = subprocess.Popen(
    [sys.executable, "mcp/server.py"],  #USE VENV PYTHON
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

def send(req):
    if proc.poll() is not None:
        print("MCP > Server process has terminated")
        return {"error": "Server terminated"}

    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()

    start_time = time.time()
    while True:
        if time.time() - start_time > 300:  # 5 minutes timeout
            print("MCP > Timeout waiting for response")
            return {"error": "Timeout"}

        line = proc.stdout.readline()
        if not line:
            continue

        line = line.strip()
        print("MCP >", line)

        try:
            data = json.loads(line)
            if "result" in data or "error" in data:
                break
        except json.JSONDecodeError:
            continue

    return data


print("\n--- Rebuild Index ---")
response = send({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "call/rebuild_index",
    "params": {
        "repo_id": "facemask-detector",
        "repo_url": "https://github.com/Dharani-Barigeda/facemask-detector.git"
    }
})
print("Response:", response)

time.sleep(1)

print("\n--- Ask Project ---")
response = send({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "call/ask_project",
    "params": {
        "question": "What does this project do?",
        "repo_id": "facemask-detector",
        "show_confidence": True
    }
})
print("Response:", response)

proc.terminate()
