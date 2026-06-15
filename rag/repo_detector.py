# rag/repo_detector.py

import os
import json
import re
from typing import List, Dict, Optional, Set

def extract_file_names_from_question(question: str) -> List[str]:
    """
    Extract potential file names from a question.
    Looks for patterns like:
    - "sentiment.py"
    - "app.py"
    - "what does sentiment.py do?"
    - "how does the train_mask_detector.py work?"
    """
    # Pattern to match file names with extensions
    # Matches: filename.ext or filename.extension
    pattern = r'\b[\w\-_]+\.\w+\b'
    matches = re.findall(pattern, question)
    
    # Filter to common code file extensions
    code_extensions = {'.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.hpp',
                      '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt',
                      '.html', '.css', '.json', '.yaml', '.yml', '.xml',
                      '.sql', '.sh', '.bat', '.ps1', '.md', '.txt'}
    
    file_names = []
    for match in matches:
        # Check if it looks like a file name (has a valid extension)
        if any(match.lower().endswith(ext) for ext in code_extensions):
            file_names.append(match)
    
    return file_names


def get_all_indexed_repos(base_dir: str = None) -> Dict[str, Dict]:
    """
    Load all indices.json files and return a mapping of repo_id -> indices data.
    """
    if base_dir is None:
        # Try to find the indices_store directory
        current_dir = os.path.dirname(os.path.dirname(__file__))
        indices_store_dir = os.path.join(current_dir, "indices_store")
    else:
        indices_store_dir = os.path.join(base_dir, "indices_store")
    
    if not os.path.exists(indices_store_dir):
        return {}
    
    repos = {}
    
    for repo_id in os.listdir(indices_store_dir):
        repo_indices_dir = os.path.join(indices_store_dir, repo_id)
        if not os.path.isdir(repo_indices_dir):
            continue
            
        indices_file = os.path.join(repo_indices_dir, "indices.json")
        if not os.path.exists(indices_file):
            continue
        
        try:
            with open(indices_file, "r", encoding="utf-8") as f:
                indices_data = json.load(f)
                repos[repo_id] = indices_data
        except Exception as e:
            print(f"[!] Error loading indices for {repo_id}: {e}")
            continue
    
    return repos


def find_repos_with_file(file_name: str, repos: Dict[str, Dict] = None) -> List[str]:
    """
    Find all repositories that contain a specific file.
    Returns list of repo_ids that have this file.
    """
    if repos is None:
        repos = get_all_indexed_repos()
    
    matching_repos = []
    
    # Normalize file name for comparison (handle case-insensitive and path separators)
    file_name_lower = file_name.lower().replace('\\', '/')
    # Extract just the filename if it has a path
    file_name_base = os.path.basename(file_name_lower)
    
    for repo_id, indices_data in repos.items():
        indexed_files = indices_data.get("indexed_files", [])
        
        # Check if the file exists in this repo (case-insensitive, handle paths)
        for indexed_file in indexed_files:
            indexed_file_normalized = indexed_file.lower().replace('\\', '/')
            indexed_file_base = os.path.basename(indexed_file_normalized)
            
            # Match by full path or just filename
            if (file_name_lower == indexed_file_normalized or 
                file_name_base == indexed_file_base):
                if repo_id not in matching_repos:
                    matching_repos.append(repo_id)
                break
    
    return matching_repos


def detect_repo_from_question(question: str, base_dir: str = None) -> Dict[str, any]:
    """
    Detect which repository the question is referring to.
    
    Returns:
    {
        "status": "unique_match" | "multiple_matches" | "no_match" | "general_question",
        "repo_id": str (if unique_match),
        "matching_repos": List[str] (if multiple_matches),
        "reason": str
    }
    """
    # Get all available repositories
    repos = get_all_indexed_repos(base_dir)
    
    if not repos:
        return {
            "status": "no_match",
            "repo_id": None,
            "matching_repos": [],
            "reason": "No indexed repositories found"
        }
    
    # First, check if repository name or project group is mentioned
    question_lower = question.lower()
    available_repo_ids = list(repos.keys())
    
    # Detect repository groups (frontend/backend pairs)
    repo_groups = detect_repo_groups(base_dir)
    
    # Check if project base name is mentioned (e.g., "myapp" matches "myapp-frontend" + "myapp-backend")
    matching_project_groups = []
    for base_name, repo_list in repo_groups.items():
        base_name_variations = [
            base_name,
            base_name.replace("-", " "),
            base_name.replace("_", " "),
        ]
        for variation in base_name_variations:
            if variation in question_lower:
                matching_project_groups.append((base_name, repo_list))
                break
    
    # If project group is mentioned, return the group
    if len(matching_project_groups) == 1:
        base_name, repo_list = matching_project_groups[0]
        return {
            "status": "project_group",
            "repo_id": None,
            "matching_repos": repo_list,
            "project_base_name": base_name,
            "reason": f"Project group '{base_name}' detected with repositories: {', '.join(repo_list)}",
            "matched_by": "project_group"
        }
    
    # Check for exact repo name matches in question (case-insensitive)
    matching_repo_names = []
    for repo_id in available_repo_ids:
        repo_id_lower = repo_id.lower()
        # Check for exact repo name or repo name with spaces/dashes
        if repo_id_lower in question_lower or repo_id_lower.replace("-", " ") in question_lower or repo_id_lower.replace("_", " ") in question_lower:
            # Check if this repo is part of a group
            group = find_project_group(repo_id, base_dir)
            if group and len(group) > 1:
                # If asking about a specific repo that's in a group, still match just that repo
                # unless the base name is mentioned
                base_name = None
                for base, repos in repo_groups.items():
                    if repo_id in repos:
                        base_name = base
                        break
                
                # If base name is NOT in question, match just the specific repo
                if not base_name or base_name not in question_lower:
                    matching_repo_names.append(repo_id)
            else:
                matching_repo_names.append(repo_id)
    
    # If exactly one repo name is mentioned, use it
    if len(matching_repo_names) == 1:
        return {
            "status": "unique_match",
            "repo_id": matching_repo_names[0],
            "matching_repos": [matching_repo_names[0]],
            "reason": f"Repository name '{matching_repo_names[0]}' found in question",
            "matched_by": "repo_name"
        }
    
    # If multiple repo names mentioned, that's ambiguous
    if len(matching_repo_names) > 1:
        return {
            "status": "multiple_matches",
            "repo_id": None,
            "matching_repos": matching_repo_names,
            "reason": f"Multiple repository names found in question: {', '.join(matching_repo_names)}",
            "matched_by": "repo_name"
        }
    
    # Extract file names from question
    file_names = extract_file_names_from_question(question)
    
    # If no file names found, this is a general question
    if not file_names:
        return {
            "status": "general_question",
            "repo_id": None,
            "matching_repos": [],
            "reason": "No specific file or repository name mentioned in question"
        }
    
    # Get all indexed repositories
    repos = get_all_indexed_repos(base_dir)
    
    if not repos:
        return {
            "status": "no_match",
            "repo_id": None,
            "matching_repos": [],
            "reason": "No indexed repositories found"
        }
    
    # Find repos that contain any of the mentioned files
    all_matching_repos = set()
    file_repo_map = {}
    
    for file_name in file_names:
        matching_repos = find_repos_with_file(file_name, repos)
        file_repo_map[file_name] = matching_repos
        all_matching_repos.update(matching_repos)
    
    # If no repos found for any file
    if not all_matching_repos:
        return {
            "status": "no_match",
            "repo_id": None,
            "matching_repos": [],
            "reason": f"File(s) {file_names} not found in any indexed repository"
        }
    
    # If exactly one repo matches, return it
    if len(all_matching_repos) == 1:
        repo_id = list(all_matching_repos)[0]
        return {
            "status": "unique_match",
            "repo_id": repo_id,
            "matching_repos": [repo_id],
            "reason": f"Unique match: File(s) {file_names} found only in repository '{repo_id}'",
            "matched_files": file_names
        }
    
    # Multiple repos match - need user clarification
    matching_repos_list = sorted(list(all_matching_repos))
    return {
        "status": "multiple_matches",
        "repo_id": None,
        "matching_repos": matching_repos_list,
        "reason": f"File(s) {file_names} found in multiple repositories: {', '.join(matching_repos_list)}",
        "file_repo_map": file_repo_map,
        "matched_files": file_names
    }


def get_all_available_repos(base_dir: str = None) -> List[str]:
    """
    Get list of all available (indexed) repository IDs.
    """
    repos = get_all_indexed_repos(base_dir)
    return sorted(list(repos.keys()))


def detect_repo_groups(base_dir: str = None) -> Dict[str, List[str]]:
    """
    Detect repository groups (e.g., frontend/backend pairs, or numbered repos).
    
    Groups repositories that share a common base name:
    - "myapp-frontend" + "myapp-backend" -> group: "myapp"
    - "project-api" + "project-ui" -> group: "project"
    - "sentiment-analysis-1" + "sentiment-analysis-2" -> group: "sentiment-analysis"
    - "app_1" + "app_2" + "app_3" -> group: "app"
    
    Returns:
    {
        "base_name": ["repo1", "repo2", ...],
        ...
    }
    """
    repos = get_all_indexed_repos(base_dir)
    repo_ids = list(repos.keys())
    
    # Common suffixes that indicate related repos
    related_suffixes = ["-frontend", "-backend", "-api", "-ui", "-client", "-server", 
                       "_frontend", "_backend", "_api", "_ui", "_client", "_server",
                       "-fe", "-be", "-fw", "-bw"]
    
    groups = {}
    
    for repo_id in repo_ids:
        repo_lower = repo_id.lower()
        base_name = None
        
        # First, check for numeric suffixes (e.g., -1, -2, _1, _2)
        # Pattern: base-name-NUMBER or base_name_NUMBER
        numeric_pattern = re.match(r'^(.+?)[-_](\d+)$', repo_lower)
        if numeric_pattern:
            base_name = numeric_pattern.group(1)
            if base_name not in groups:
                groups[base_name] = []
            groups[base_name].append(repo_id)
            continue
        
        # Check if repo has a related suffix (frontend/backend pattern)
        for suffix in related_suffixes:
            if repo_lower.endswith(suffix):
                # Extract base name
                base_name = repo_lower[:-len(suffix)]
                break
        
        if base_name:
            if base_name not in groups:
                groups[base_name] = []
            groups[base_name].append(repo_id)
        else:
            # Check if repo name without suffix matches another repo's base
            # This handles cases like "myapp" matching "myapp-frontend"
            for other_repo in repo_ids:
                if other_repo == repo_id:
                    continue
                
                other_lower = other_repo.lower()
                
                # Check for numeric pattern in other repo
                other_numeric = re.match(r'^(.+?)[-_](\d+)$', other_lower)
                if other_numeric:
                    other_base = other_numeric.group(1)
                    if repo_lower == other_base:
                        # Found a match: "sentiment-analysis" matches "sentiment-analysis-1"
                        if other_base not in groups:
                            groups[other_base] = []
                        if repo_id not in groups[other_base]:
                            groups[other_base].append(repo_id)
                        if other_repo not in groups[other_base]:
                            groups[other_base].append(other_repo)
                        continue
                
                # Check for related suffix pattern
                for suffix in related_suffixes:
                    if other_lower.endswith(suffix):
                        other_base = other_lower[:-len(suffix)]
                        if repo_lower == other_base:
                            # Found a match: "myapp" matches "myapp-frontend"
                            if other_base not in groups:
                                groups[other_base] = []
                            if repo_id not in groups[other_base]:
                                groups[other_base].append(repo_id)
                            if other_repo not in groups[other_base]:
                                groups[other_base].append(other_repo)
                            break
    
    # Only return groups with 2+ repos (actual groups)
    return {base: repos for base, repos in groups.items() if len(repos) >= 2}


def find_project_group(repo_id: str, base_dir: str = None) -> Optional[List[str]]:
    """
    Find if a repository belongs to a project group (frontend/backend pair).
    
    Returns:
    - List of repo IDs in the group (including the given repo_id) if group found
    - None if no group found
    """
    groups = detect_repo_groups(base_dir)
    
    # Check if repo_id is in any group
    for base_name, repo_list in groups.items():
        if repo_id in repo_list:
            return repo_list
    
    return None
