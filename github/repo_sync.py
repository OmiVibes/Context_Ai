import os
import subprocess

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

BASE_REPO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "repos"
)

# -------------------------------------------------
# REPO SYNC (SILENT & SAFE)
# -------------------------------------------------
def sync_repo(repo_url: str, repo_name: str) -> str:
    """
    Clone or pull a GitHub repository silently.
    Returns local repo path.

    IMPORTANT:
    - NO stdout output
    - NO stderr output
    - Safe for MCP JSON-RPC
    """

    os.makedirs(BASE_REPO_DIR, exist_ok=True)
    local_repo_path = os.path.join(BASE_REPO_DIR, repo_name)

    # Inject token only if present (private repo support)
    if GITHUB_TOKEN:
        auth_repo_url = repo_url.replace(
            "https://github.com/",
            f"https://{GITHUB_TOKEN}@github.com/"
        )
    else:
        auth_repo_url = repo_url

    try:
        # ---------------------------------
        # CLONE (if repo does not exist)
        # ---------------------------------
        if not os.path.exists(local_repo_path):
            subprocess.run(
                ["git", "clone", auth_repo_url, local_repo_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

        # ---------------------------------
        # PULL (if repo exists)
        # ---------------------------------
        else:
            subprocess.run(
                ["git", "-C", local_repo_path, "fetch", "origin"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            subprocess.run(
                ["git", "-C", local_repo_path, "reset", "--hard", "origin/HEAD"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

    except subprocess.CalledProcessError as e:
        # IMPORTANT: raise error, but DO NOT print
        raise RuntimeError(f"Git operation failed for repo '{repo_name}'") from e

    # Remove README and documentation files to focus on code
    doc_files_to_remove = ["readme", "license", "changelog", "contributing", "authors", "credits"]
    for root, dirs, files in os.walk(local_repo_path):
        for file in files:
            file_lower = file.lower()
            # Remove common documentation files
            if any(file_lower.startswith(pattern) for pattern in doc_files_to_remove):
                try:
                    file_path = os.path.join(root, file)
                    os.remove(file_path)
                    print(f"[*] Removed documentation file: {file_path}")
                except Exception as e:
                    print(f"[!] Could not remove {file_path}: {e}")

    return local_repo_path
