import os
import hashlib

def compute_project_fingerprint(root_dir: str) -> str:
    """
    Computes a lightweight fingerprint of a project directory
    based on file paths, sizes, and modification times.
    """

    hasher = hashlib.md5()

    for root, _, files in os.walk(root_dir):
        for file in sorted(files):
            path = os.path.join(root, file)
            try:
                stat = os.stat(path)
                hasher.update(path.encode())
                hasher.update(str(stat.st_size).encode())
                hasher.update(str(int(stat.st_mtime)).encode())
            except Exception:
                continue

    return hasher.hexdigest()
