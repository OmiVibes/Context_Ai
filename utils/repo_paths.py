"""Shared validation for repository identifiers and repository-owned files."""
import ntpath
import re
from pathlib import Path


class InvalidRepoId(ValueError):
    pass


def validate_repo_id(repo_id: str) -> str:
    if not isinstance(repo_id, str):
        raise InvalidRepoId("repo_id must be a repository name")
    name = repo_id.strip()
    if (not name or name in {".", ".."} or name.endswith(".")
            or re.search(r'[\\/:%<>"|?*\x00-\x1f]', name)
            or ntpath.isabs(name) or ntpath.splitdrive(name)[0]
            or name.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
        raise InvalidRepoId("repo_id must be a single repository name, not a path")
    return name


def contained_path(root, *parts) -> Path:
    root = Path(root).resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise InvalidRepoId("Repository path must stay inside its configured root")
    return candidate


def repo_path(root, repo_id: str) -> Path:
    return contained_path(root, validate_repo_id(repo_id))
