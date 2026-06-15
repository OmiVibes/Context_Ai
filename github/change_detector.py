def extract_changed_files(payload: dict):
    added_files = set()
    modified_files = set()
    removed_files = set()

    for commit in payload.get("commits", []):
        added_files.update(commit.get("added", []))
        modified_files.update(commit.get("modified", []))
        removed_files.update(commit.get("removed", []))

    return {
        "added": list(added_files),
        "modified": list(modified_files),
        "removed": list(removed_files)
    }
