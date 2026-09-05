"""Read-only GitHub requests with bounded, credential-safe failures."""
import os
import re
from pathlib import Path
import requests
from dotenv import load_dotenv
from utils.errors import ServiceError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def configuration_status():
    return {"configured": bool(os.getenv("GITHUB_TOKEN", "").strip()),
            "authentication_verified": False}


def fetch_issues(repo_owner: str, repo_name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", repo_owner or "") or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo_name or ""):
        raise ServiceError("github_repository_invalid", "Invalid GitHub owner or repository name", 400)
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise ServiceError("github_not_configured", "Set GITHUB_TOKEN to enable GitHub Milestones and Risks", 503)
    try:
        response = requests.get(f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"state": "all", "per_page": 100}, timeout=15)
        if response.status_code == 401:
            raise ServiceError("github_auth_failed", "GitHub authentication failed. Check GITHUB_TOKEN and its validity", 503)
        if response.status_code == 403:
            raise ServiceError("github_access_denied", "GitHub denied access. Check token permissions or rate limits", 503)
        if response.status_code == 404:
            raise ServiceError("github_repository_unavailable", "GitHub repository was not found or the token cannot access it", 404)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Expected issue list")
        return data
    except requests.Timeout as exc:
        raise ServiceError("github_timeout", "GitHub request timed out", 504) from exc
    except (requests.RequestException, ValueError) as exc:
        raise ServiceError("github_unavailable", "GitHub request failed. Check connectivity and configuration", 503) from exc
