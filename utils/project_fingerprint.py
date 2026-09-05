import hashlib
from pathlib import Path
from app_processing.file_loader import iter_repo_files


def compute_project_fingerprint(root_dir: str) -> str:
    """Hash eligible relative paths and content, not timestamps or absolute paths."""
    root = Path(root_dir).resolve()
    hasher = hashlib.sha256()
    # Version invalidates old indexes after the repaired ingestion rules.
    hasher.update(b"context-assist-ingestion-v2\0")
    for path in sorted(iter_repo_files(root), key=lambda p: p.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode("utf-8")
        hasher.update(len(name).to_bytes(8, "big"))
        hasher.update(name)
        content_hash = hashlib.sha256(path.read_bytes()).digest()
        hasher.update(content_hash)
    return hasher.hexdigest()
