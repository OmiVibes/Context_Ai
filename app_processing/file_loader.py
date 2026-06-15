import os
import re
import nbformat
import unicodedata
import math

# ADD THESE IMPORTS (NEW ⭐)
from .file_reader import read_markdown_file, read_file

# ---------------------------------------------
# ALLOWED EXTENSIONS  (kept yours + same set)
# ---------------------------------------------
# Focus on code files - exclude documentation markdown files
ALLOWED_EXTENSIONS = {
    # Python
    ".py", ".pyx", ".pyi",
    # JavaScript/TypeScript
    ".js", ".jsx", ".ts", ".tsx",
    # Java
    ".java", ".kt", ".scala",
    # C/C++
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    # C#
    ".cs",
    # Go
    ".go",
    # Rust
    ".rs",
    # Ruby
    ".rb",
    # PHP
    ".php",
    # Swift
    ".swift",
    # Other code/config
    ".ipynb", ".json", ".yaml", ".yml", ".toml", ".xml",
    # Shell scripts
    ".sh", ".bash", ".zsh", ".fish",
    # PowerShell
    ".ps1", ".psm1",
    # SQL
    ".sql",
    # HTML/CSS (for web projects)
    ".html", ".css", ".scss", ".sass",
    # Configuration files (may contain code patterns)
    ".config", ".conf", ".ini", ".properties"
}

# ---------------------------------------------
# SKIP FOLDERS  (yours + expanded)
# ---------------------------------------------
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    "venv",
    ".venv",
    "env",
    ".env",
    "node_modules",
    "site-packages",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "assets",
    "models",
    "checkpoints",
    "logs",
    "data",
    "images",
    "raw",
    "datasets"
}

# ---------------------------------------------
# SKIP SECRET / BINARY FILE PATTERNS
# ---------------------------------------------
SKIP_FILE_PATTERNS = [
    r".*\.key$", r".*\.pem$", r".*\.crt$", r".*\.env$",
    r".*\.pkl$", r".*\.h5$", r".*\.pt$", r".*\.onnx$",
    r".*\.jpg$", r".*\.jpeg$", r".*\.png$", r".*\.gif$",
    r".*\.mp4$", r".*\.bin$", r".*\.exe$", r".*\.dll$"
]

# ---------------------------------------------
# SIZE GUARD: skip > 300 KB text files
# ---------------------------------------------
MAX_TEXT_FILE_SIZE = 300 * 1024

# ---------------------------------------------
# SECRET MASKING — KEYWORD BASED (NEW ⭐⭐)
# ---------------------------------------------
SECRET_PATTERNS = [
    r"(?i)(api[_-]?key\s*=\s*[\"']?[A-Za-z0-9_\-]{8,})",
    r"(?i)(secret\s*=\s*[\"']?.{6,})",
    r"(?i)(password\s*=\s*[\"']?.{4,})",
    r"(?i)(token\s*=\s*[\"']?[A-Za-z0-9_\-]{8,})",
    r"AKIA[0-9A-Z]{16}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
]

def apply_keyword_mask(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, "<MASKED_SECRET>", text)
    return text

# ---------------------------------------------
# SECRET MASKING — ENTROPY DETECTION (NEW 🔥)
# ---------------------------------------------
def shannon_entropy(s):
    prob = [float(s.count(c)) / len(s) for c in dict.fromkeys(s)]
    return -sum([p * math.log(p, 2) for p in prob])

def entropy_mask(text: str) -> str:
    def replace_match(match):
        word = match.group(0)
        return "<MASKED_SECRET>" if shannon_entropy(word) > 3.5 else word

    return re.sub(r"[A-Za-z0-9+/=]{20,}", replace_match, text)

# FULL mask chain
def mask_secrets(text: str) -> str:
    text = apply_keyword_mask(text)
    text = entropy_mask(text)
    return text

# ---------------------------------------------
# EMOJI + SYMBOL FILTER
# ---------------------------------------------
EMOJI_PATTERN = re.compile(
    "[" 
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE
)

def clean_unicode(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    cleaned = EMOJI_PATTERN.sub("", normalized)
    cleaned = "".join(c for c in cleaned if c.isprintable() or c == "\n")
    return cleaned

# ---------------------------------------------
# Basic non-English detection (debug only)
# ---------------------------------------------
def detect_language_sample(text: str) -> str:
    if re.search(r"[\u0900-\u097F]", text):
        return "Hindi/Indic"
    if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", text):
        return "Japanese"
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "Chinese"
    return "English/Latin"

def is_secret_or_binary(file_path: str) -> bool:
    for pattern in SKIP_FILE_PATTERNS:
        if re.match(pattern, file_path.lower()):
            return True
    return False

# ---------------------------------------------
# HANDLE NOTEBOOK LOADING
# ---------------------------------------------
def load_ipynb(file_path: str, metadata: dict) -> list[dict]:
    documents = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)

        for cell in nb.cells:
            if cell.cell_type in ("markdown", "code") and cell.source.strip():
                cleaned = clean_unicode(cell.source)
                cleaned = mask_secrets(cleaned)
                if cleaned:
                    lang = detect_language_sample(cleaned[:400])
                    if lang != "English/Latin":
                        print(f"🌐 Non-English text detected ({lang}) in {metadata['file_path']}")
                    documents.append({
                        "text": cleaned,
                        "metadata": metadata | {"section": cell.cell_type}
                    })

    except Exception as e:
        print(f"⚠️ Error reading notebook {file_path}:", e)

    return documents

# ---------------------------------------------
# MAIN INGEST FUNCTION (merged with new rules)
# ---------------------------------------------
def load_repo_files(repo_path: str) -> list[dict]:
    """
    Load code files from repository, excluding documentation files.
    Focuses on actual source code to answer questions based on implementation.
    """
    documents = []

    # Skip REPO_MANIFEST.md - it's documentation, not code
    # Focus on actual code files only

    # Walk repo and apply enhanced guards
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for file in files:
            # Skip documentation files - focus on code only
            if file == "REPO_MANIFEST.md":
                continue

            # Skip README and documentation files to focus on code
            file_lower = file.lower()
            if file_lower.startswith("readme") or file_lower.startswith("license"):
                continue
            
            # Skip other common documentation files
            doc_patterns = ["changelog", "contributing", "authors", "credits", "history"]
            if any(file_lower.startswith(pattern) for pattern in doc_patterns):
                continue

            path = os.path.join(root, file)
            rel_path = path.replace(repo_path, "").lstrip(os.sep)

            # Skip secret or binary file types
            if is_secret_or_binary(path):
                continue

            ext = os.path.splitext(file)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            # Size skip guard
            try:
                if os.path.getsize(path) > MAX_TEXT_FILE_SIZE:
                    continue
            except Exception:
                continue

            meta = {
                "file_path": rel_path,
                "abs_path": path,
                "doc_type": "source"
            }

            # Notebook support
            if ext == ".ipynb":
                documents.extend(load_ipynb(path, meta))
                continue

            # Normal text/code file
            try:
                # Skip markdown files entirely - focus on actual code files
                # Markdown files are typically documentation, not code
                if ext == ".md":
                    continue
                
                # Read code files
                raw = read_file(path)

                cleaned = clean_unicode(raw)
                cleaned = mask_secrets(cleaned)

                if cleaned.strip():
                    lang = detect_language_sample(cleaned[:400])
                    if lang != "English/Latin":
                        print(f"🌐 Non-English text detected ({lang}) in {rel_path}")
                    documents.append({
                        "text": cleaned,
                        "metadata": meta
                    })
            except Exception as e:
                print(f"⚠️ Error reading {path}: {e}")

    return documents
