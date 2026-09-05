import os
from pathlib import Path
from app_processing.file_loader import ALLOWED_EXTENSIONS, EXCLUDE_DIRS


def summarize_structure(base_path):
    base = Path(base_path).resolve()
    if not base.is_dir():
        return ""
    lines = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS
                         and (Path(root) / d).resolve().is_relative_to(base))
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.lower() in ALLOWED_EXTENSIONS and path.resolve().is_relative_to(base):
                lines.append("- " + path.relative_to(base).as_posix())
    return "\n".join(lines)


def infer_architecture(base_path):
    structure = summarize_structure(base_path)
    if not structure:
        return "I could not find sufficient repository evidence to describe its architecture."
    return ("Observed source/configuration files:\n" + structure
            + "\n\nFile names alone are insufficient evidence to infer component responsibilities or data flow.")
