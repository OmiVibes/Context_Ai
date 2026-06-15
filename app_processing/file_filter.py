import os

# 🌍 Supported text/code/document formats
ALLOWED_EXTENSIONS = {
    # Backend languages
    ".py", ".java", ".go", ".rb", ".php",
    ".cs", ".rs",  # C#, Rust
    ".c", ".cpp", ".h",  # C / C++

    # Web / Frontend
    ".js", ".ts", ".jsx", ".tsx",
    ".html", ".htm", ".css", ".scss", ".sass",

    # DevOps / Config
    ".json", ".yaml", ".yml", ".toml",
    ".ini", ".env.example", ".dockerfile",

    # Data / Docs
    ".md", ".txt", ".rst",

    # Notebooks
    ".ipynb",
}

# 🚫 Always skip these folders (massive / binary / useless)
SKIP_DIRS = {
    "__pycache__", "node_modules", "dist", "build",
    ".git", ".idea", ".vscode", "env", "venv",
    "models", "checkpoints", "bin", "obj",
    "logs", "tmp", "cache", "venv", "virtualenv",
}

# 🚫 Skip binary or compiled formats regardless of extension
SKIP_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".pdf", ".doc", ".docx",
    ".class", ".jar", ".exe", ".dll", ".bin",
}


def is_indexable(file_path: str) -> bool:
    """
    Returns True if a file should be read & embedded.
    Uses extension rules from ALLOWED_EXTENSIONS.
    """
    _, ext = os.path.splitext(file_path.lower())

    # Skip binary & useless files
    if ext in SKIP_EXTENSIONS:
        return False

    # If Dockerfile has no extension, special-case it
    if os.path.basename(file_path) == "Dockerfile":
        return True

    return ext in ALLOWED_EXTENSIONS


def should_skip_directory(dirname: str) -> bool:
    """
    Returns True if a directory should NOT be traversed.
    """
    return dirname.lower() in SKIP_DIRS
