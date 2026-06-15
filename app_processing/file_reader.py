import os

# ⛔️ Skip only command/install lines — NOT headings
SKIP_COMMANDS = [
    # Python
    "pip install",
    "pip3 install",
    "requirements.txt",
    "conda install",

    # Git
    "git clone",
    "git fetch",
    "git pull",

    # Node / JS / TS
    "npm install",
    "npm run",
    "yarn install",
    "pnpm install",

    # Java / Gradle / Maven
    "mvn ",
    "gradle ",
    "./gradlew",

    # Docker
    "docker build",
    "docker run",
    "docker compose",
    "docker-compose",
]


def read_markdown_file(file_path: str) -> str:
    """
    Reads Markdown files and strips ONLY noisy command lines.
    Keeps ALL sections, headings, and descriptive text.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_path} not found")

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    cleaned_lines = []

    for line in lines:
        lower = line.lower()

        # 🎯 Skip raw setup/install command lines
        if any(cmd in lower for cmd in SKIP_COMMANDS):
            continue

        cleaned_lines.append(line)

    return "".join(cleaned_lines)


def read_file(file_path: str) -> str:
    """
    Reads ANY text/code file safely.
    Used for .py, .js, .ts, .html, .css, .java, .c, .cpp, etc.
    Does not skip anything — raw text returned.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_path} not found")

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        # Gracefully skip unreadable/binary edge cases
        return ""
