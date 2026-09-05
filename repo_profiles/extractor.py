import os
import re
import json
from typing import Optional, List
from utils.repo_paths import validate_repo_id, contained_path


def _read_readme(repo_path: str) -> Optional[str]:
    """
    Reads README.md or README.txt if present.
    """
    for name in ["README.md", "README.MD", "README.txt"]:
        path = str(contained_path(repo_path, name))
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return None


def _extract_title(readme: str) -> Optional[str]:
    """
    Extracts first markdown H1 (# Title).
    """
    for line in readme.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("#").strip()
    return None


def _extract_email(text: str) -> Optional[str]:
    match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)
    return match.group(0) if match else None


def _extract_github_username(text: str) -> Optional[str]:
    match = re.search(r"github\.com/([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else None


def _extract_tech_stack(readme: str) -> List[str]:
    """
    Extracts bullet list under 'Tech Stack' or similar headings.
    """
    tech = []
    capture = False

    for line in readme.splitlines():
        l = line.lower().strip()

        if any(k in l for k in ["tech stack", "technology", "technologies used"]):
            capture = True
            continue

        if capture:
            if not line.strip():
                break

            if line.strip().startswith(("-", "*")):
                tech.append(line.lstrip("-* ").strip())
            else:
                break

    return tech


def _extract_owner(readme: str) -> Optional[str]:
    """
    Looks for 'Author', 'Maintainer', or '👨‍💻' sections.
    """
    lines = readme.splitlines()

    for i, line in enumerate(lines):
        l = line.lower()

        if any(k in l for k in ["author", "maintainer", "👨‍💻"]):
            # Look ahead a few lines
            for j in range(i + 1, min(i + 5, len(lines))):
                name = lines[j].strip()
                if name and not name.startswith("#"):
                    return name

    return None


def build_repo_profile(repo_id: str, repo_path: str, repo_url: str):
    """
    Builds deterministic metadata profile for ANY repo.
    """
    repo_id = validate_repo_id(repo_id)
    readme = _read_readme(repo_path)

    profile = {
        "repo_id": repo_id,
        "repo_url": repo_url,
        "title": None,
        "description": None,
        "owner": None,
        "email": None,
        "github_username": None,
        "tech_stack": [],
    }

    if readme:
        profile["title"] = _extract_title(readme)
        profile["description"] = readme.strip().split("\n\n")[1].strip() if "\n\n" in readme else None
        profile["email"] = _extract_email(readme)
        profile["github_username"] = _extract_github_username(readme)
        profile["owner"] = _extract_owner(readme)
        profile["tech_stack"] = _extract_tech_stack(readme)

    os.makedirs("repo_profiles", exist_ok=True)
    out_path = str(contained_path("repo_profiles", f"{repo_id}.json"))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return profile
