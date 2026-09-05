from collections import defaultdict
from github.api import fetch_issues


def list_milestones(repo_owner: str, repo_name: str):
    issues = fetch_issues(repo_owner, repo_name)

    milestones = defaultdict(lambda: {
        "open": [],
        "closed": []
    })

    for issue in issues:
        title = issue.get("title", "").lower()
        labels = [l["name"].lower() for l in issue.get("labels", [])]
        state = issue.get("state")

        # very simple milestone detection (GOOD ENOUGH)
        milestone_name = None
        for label in labels:
            if "phase" in label or "milestone" in label or "v1" in label:
                milestone_name = label
                break

        if not milestone_name and "phase" in title:
            milestone_name = "phase"

        if milestone_name:
            milestones[milestone_name][state].append({
                "title": issue["title"],
                "url": issue["html_url"]
            })

    # format response
    result = []
    for name, data in milestones.items():
        result.append({
            "name": name,
            "open_items": len(data["open"]),
            "closed_items": len(data["closed"]),
            "status": "completed" if not data["open"] else "in-progress"
        })

    return result
