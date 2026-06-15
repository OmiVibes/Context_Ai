import os
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"

RISK_KEYWORDS = [
    "risk", "blocker", "delay", "bug",
    "issue", "fail", "error", "missing",
    "broken", "not working"
]


def fetch_issues(repo_owner: str, repo_name: str):
    url = f"{GITHUB_API}/repos/{repo_owner}/{repo_name}/issues"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    params = {"state": "all", "per_page": 100}

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def detect_risks(repo_owner: str, repo_name: str):
    issues = fetch_issues(repo_owner, repo_name)

    risky_items = []

    for issue in issues:
        text = f"{issue.get('title','')} {issue.get('body','')}".lower()

        if any(k in text for k in RISK_KEYWORDS):
            risky_items.append({
                "title": issue["title"],
                "url": issue["html_url"]
            })

    return risky_items
