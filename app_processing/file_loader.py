import os
import re
import nbformat
import math
import json
import subprocess
import tempfile
from pathlib import Path

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
    r"AKIA[0-9A-Z]{16}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
]

SENSITIVE_KEYS = {"password", "passwd", "token", "apikey", "secret", "accesstoken"}
CONFIG_SECRET = re.compile(
    r'''(?ix)(?P<prefix>(?<![\w])['"]?(?:password|passwd|token|api[_-]?key|secret|access[_-]?token)['"]?\s*(?::|=(?!=))\s*)'''
    r'''(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;\#}\]]+)'''
)

def apply_keyword_mask(text: str) -> str:
    def redact(match):
        value = match['value']
        quote = value[0] if value.startswith(('"', "'")) else ''
        return match['prefix'] + quote + '<MASKED_SECRET>' + quote

    text = CONFIG_SECRET.sub(redact, text)
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
        opaque = (len(word) >= 32 and any(c.isdigit() for c in word)
                  and any(c.isupper() for c in word) and any(c.islower() for c in word))
        return "<MASKED_SECRET>" if opaque and shannon_entropy(word) > 3.5 else word

    return re.sub(r"[A-Za-z0-9+/=]{20,}", replace_match, text)

# FULL mask chain
def mask_secrets(text: str) -> str:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, (dict, list)):
        def redact(value):
            if isinstance(value, dict):
                return {key: ('<MASKED_SECRET>' if item is not None else None)
                        if re.sub(r'[^a-z0-9]', '', key.lower()) in SENSITIVE_KEYS else redact(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, str):
                for pattern in SECRET_PATTERNS:
                    value = re.sub(pattern, '<MASKED_SECRET>', value)
                return entropy_mask(value)
            return value
        return json.dumps(redact(data), ensure_ascii=False, indent=2)
    text = apply_keyword_mask(text)
    text = entropy_mask(text)
    return text

# ---------------------------------------------
# EMOJI + SYMBOL FILTER
# ---------------------------------------------
def clean_unicode(text: str) -> str:
    # Symbols, combining marks, emoji and indentation can all be source data.
    # Strip only a transport BOM and NULs; never normalize identifier/string values.
    return text.removeprefix('\ufeff').replace('\x00', '')

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
GENERATED_DIRS = {"chunk_store", "indices_store", "repos", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "htmlcov"}
DOCUMENT_PREFIXES = ("readme", "license", "changelog", "contributing", "authors", "credits", "history")


def iter_repo_files(repo_path):
    """Select the same source files for ingestion and content fingerprinting."""
    base = Path(repo_path).resolve()
    candidates = []
    has_ignore_rules = False
    for root, dirs, files in os.walk(base):
        has_ignore_rules = has_ignore_rules or '.gitignore' in files
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS | GENERATED_DIRS
                         and (Path(root) / d).resolve().is_relative_to(base))
        for name in sorted(files):
            path = Path(root) / name
            relative = path.relative_to(base)
            if (name.lower().startswith(DOCUMENT_PREFIXES)
                    or path.suffix.lower() not in ALLOWED_EXTENSIONS
                    or is_secret_or_binary(str(path))
                    or not path.resolve().is_relative_to(base)):
                continue
            # The tool's generated profiles are data; extractor.py remains source.
            if relative.parts[0] == "repo_profiles" and path.suffix.lower() == ".json":
                continue
            if path.stat().st_size <= MAX_TEXT_FILE_SIZE:
                candidates.append(path)
    ignored = set()
    if candidates and ((base / ".git").exists() or has_ignore_rules):
        names = [p.relative_to(base).as_posix() for p in candidates]
        if (base / '.git').exists():
            result = _git_check_ignore(base, names)
        else:
            # Archives may contain ignore rules without .git. Let Git interpret
            # those rules using disposable metadata, never initializing the source.
            with tempfile.TemporaryDirectory(prefix='context_ignore_') as metadata:
                subprocess.run(['git', 'init', '--bare', '--quiet', metadata],
                               check=True, capture_output=True, timeout=15)
                result = _git_check_ignore(base, names, metadata)
        if result.returncode not in (0, 1):
            raise RuntimeError("Cannot determine repository ignore rules")
        ignored = set(result.stdout.decode("utf-8").rstrip("\0").split("\0"))
    return [p for p in candidates if p.relative_to(base).as_posix() not in ignored]


def _git_check_ignore(base, names, metadata=None):
    command = ['git', '-C', str(base)]
    if metadata is not None:
        command.extend(['--git-dir', metadata, '--work-tree', str(base)])
    return subprocess.run(command + ['check-ignore', '--no-index', '--stdin', '-z'],
                          input=('\0'.join(names) + '\0').encode('utf-8'),
                          capture_output=True, timeout=15)


def load_repo_files(repo_path: str) -> list[dict]:
    """Read selected source content without modifying repository files."""
    documents = []
    base = Path(repo_path).resolve()
    for path in iter_repo_files(base):
        relative = str(path.relative_to(base))
        meta = {"file_path": relative, "abs_path": str(path), "doc_type": "source"}
        if path.suffix.lower() == ".ipynb":
            documents.extend(load_ipynb(str(path), meta))
            continue
        raw = read_file(path)
        cleaned = mask_secrets(clean_unicode(raw))
        if cleaned.strip():
            documents.append({"text": cleaned, "metadata": meta})
    return documents
