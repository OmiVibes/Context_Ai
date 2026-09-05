from github.api import fetch_issues

RISK_KEYWORDS = [
    "risk", "blocker", "delay", "bug",
    "issue", "fail", "error", "missing",
    "broken", "not working"
]



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
