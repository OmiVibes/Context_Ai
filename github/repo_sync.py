import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from utils.repo_paths import repo_path
from utils.errors import ServiceError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
BASE_REPO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "repos")


class RepositorySyncError(ServiceError):
    def __init__(self, message):
        super().__init__("repository_sync_failed", message, 409)


def sync_repo(repo_url: str, repo_name: str, *, repo_root=None, branch=None) -> str:
    """Clone or fast-forward a clean repository; never reset local work."""
    root = BASE_REPO_DIR if repo_root is None else repo_root
    local_repo_path = repo_path(root, repo_name)
    if not repo_url or repo_url.startswith("-"):
        raise ValueError("A repository URL is required")
    parsed = urlparse(repo_url)
    if parsed.username or parsed.password:
        raise ValueError("Repository URLs must not contain credentials")
    os.makedirs(root, exist_ok=True)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    # This header is neither saved to .git/config nor put in argv.
    if GITHUB_TOKEN and parsed.scheme == "https" and parsed.hostname == "github.com":
        import base64
        credential = base64.b64encode(f"x-access-token:{GITHUB_TOKEN}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {credential}")

    def git(*args):
        return subprocess.run(["git", *args], env=env, capture_output=True,
                              text=True, check=True, timeout=120).stdout.strip()

    try:
        if not local_repo_path.exists():
            options = ("--branch", branch) if branch else ()
            git("clone", *options, "--", repo_url, str(local_repo_path))
        else:
            prefix = ("-C", str(local_repo_path))
            if Path(git(*prefix, "rev-parse", "--show-toplevel")).resolve() != local_repo_path:
                raise RepositorySyncError("Destination is not a repository root")
            if git(*prefix, "status", "--porcelain", "--untracked-files=all"):
                raise RepositorySyncError("Repository has local changes; commit or stash them before syncing")
            if branch and git(*prefix, "branch", "--show-current") != branch:
                raise RepositorySyncError("Repository is on a different branch; switch it manually before syncing")
            if git(*prefix, "remote", "get-url", "origin") != repo_url:
                raise RepositorySyncError("Repository URL differs from the existing origin")
            upstream = git(*prefix, "rev-parse", "--abbrev-ref", "@{upstream}")
            git(*prefix, "fetch", "origin")
            if git(*prefix, "diff", "--name-only", "--no-renames", "--diff-filter=D", f"HEAD...{upstream}"):
                raise RepositorySyncError("Upstream update removes repository files; review and apply it manually")
            git(*prefix, "merge", "--ff-only", "--no-overwrite-ignore", upstream)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RepositorySyncError("Repository sync failed; local work was preserved. Check origin, upstream, credentials and connectivity.") from exc
    return str(local_repo_path)
