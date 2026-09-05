# rag/router.py

import os
from typing import Dict, Any, Optional

from rag.core import rag_answer, rag_answer_multi_repo
from utils.questions import is_generic_question, greeting_answer
from utils.repo_paths import validate_repo_id
from rag.milestones import list_milestones
from rag.risk import detect_risks
from rag.repo_detector import detect_repo_from_question, get_all_available_repos


# rag/router.py

class RouterAgent:
    def route(self, *, question: str, repo_id: Optional[str], params: dict) -> dict:
        q = question.lower().strip()
        if is_generic_question(question):
            return {**greeting_answer(), "agent": "IdentityAgent"}
        if repo_id:
            repo_id = validate_repo_id(repo_id)

        # -----------------------------
        # 🔍 REPO DETECTION (if repo_id not provided)
        # -----------------------------
        if not repo_id or not repo_id.strip():
            # Try to detect repo from question
            base_dir = os.path.dirname(os.path.dirname(__file__))
            detection_result = detect_repo_from_question(question, base_dir=base_dir)
            
            if detection_result["status"] == "unique_match":
                # Found unique match - use it
                repo_id = detection_result["repo_id"]
            elif detection_result["status"] == "project_group":
                return rag_answer_multi_repo(question=question, repo_ids=detection_result["matching_repos"],
                    show_sources=params.get("show_sources", False), show_confidence=params.get("show_confidence", False))
            elif detection_result["status"] == "multiple_matches":
                # Multiple repos match - ask user to clarify
                matching_repos = detection_result["matching_repos"]
                repos_list = "\n".join([f"{i+1}. {repo}" for i, repo in enumerate(matching_repos)])
                return {
                    "answer": (
                        f"I found multiple repositories that could match your question.\n\n"
                        f"Which one are you referring to?\n{repos_list}\n\n"
                        f"Please specify the repository by including its name in your question, "
                        f"or by using the repo_id parameter."
                    ),
                    "confidence": "Low",
                    "agent": "RepoDetectionAgent",
                    "requires_clarification": True,
                    "matching_repos": matching_repos,
                    "reason": detection_result["reason"]
                }
            elif detection_result["status"] == "general_question":
                # General question with no specific file - need to list all repos
                available_repos = get_all_available_repos(base_dir=base_dir)
                if not available_repos:
                    return {
                        "answer": (
                            "I couldn't determine which repository you're asking about, and "
                            "no repositories are currently indexed. Please index a repository first."
                        ),
                        "confidence": "Low",
                        "agent": "RepoDetectionAgent",
                        "requires_clarification": True
                    }
                
                repos_list = "\n".join([f"{i+1}. {repo}" for i, repo in enumerate(available_repos)])
                return {
                    "answer": (
                        f"I found multiple repositories. Which one are you referring to?\n\n{repos_list}\n\n"
                        f"Please specify the repository by name in your question, "
                        f"or by including a specific file name that's unique to the repository."
                    ),
                    "confidence": "Low",
                    "agent": "RepoDetectionAgent",
                    "requires_clarification": True,
                    "available_repos": available_repos
                }
            else:
                # No match found
                available_repos = get_all_available_repos(base_dir=base_dir)
                if not available_repos:
                    return {
                        "answer": (
                            "I couldn't find the file(s) mentioned in your question, and "
                            "no repositories are currently indexed. Please index a repository first."
                        ),
                        "confidence": "Low",
                        "agent": "RepoDetectionAgent",
                        "requires_clarification": True,
                        "reason": detection_result["reason"]
                    }
                
                repos_list = "\n".join([f"{i+1}. {repo}" for i, repo in enumerate(available_repos)])
                return {
                    "answer": (
                        f"I couldn't find the file(s) mentioned in your question in any indexed repository.\n\n"
                        f"Available repositories:\n{repos_list}\n\n"
                        f"Please specify which repository you're asking about, or check that the file exists."
                    ),
                    "confidence": "Low",
                    "agent": "RepoDetectionAgent",
                    "requires_clarification": True,
                    "available_repos": available_repos,
                    "reason": detection_result["reason"]
                }
        
        # At this point, repo_id should be set (either provided or auto-detected)
        repo_id = repo_id.strip() if repo_id else None
        
        if not repo_id:
            return {
                "answer": "Repository ID is required but could not be determined from your question.",
                "confidence": "Low",
                "agent": "RepoDetectionAgent",
                "requires_clarification": True
            }

        # -----------------------------
        # 👋 GREETINGS & IDENTITY (Check FIRST - works for any repo)
        # -----------------------------
        if any(k in q for k in ["milestone", "phase", "roadmap", "plan", "status"]):
            repo_owner = params.get("repo_owner")
            repo_name = params.get("repo_name")

            if repo_owner and repo_name:
                milestones = list_milestones(
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                )

                if not milestones:
                    return {
                        "answer": "No milestones found in the repository.",
                        "confidence": "Low",
                        "agent": "PlanningAgent",
                    }

                text = "\n".join(
                    f"- {m['name']}: {m['status']}"
                    for m in milestones
                )

                return {
                    "answer": text,
                    "confidence": "Medium",
                    "agent": "PlanningAgent",
                }

        # -----------------------------
        # ⚠️ Risks
        # -----------------------------
        if any(k in q for k in ["risk", "issue", "blocker", "problem"]):
            repo_owner = params.get("repo_owner")
            repo_name = params.get("repo_name")

            if repo_owner and repo_name:
                risks = detect_risks(
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                )

                # detect_risks may return a list OR dict
                if isinstance(risks, list):
                    if not risks:
                        return {
                            "answer": "No risks detected in the repository.",
                            "confidence": "Low",
                            "agent": "RiskAgent",
                        }

                    text = "\n".join(f"- {r}" for r in risks)

                    return {
                        "answer": text,
                        "confidence": "Medium",
                        "agent": "RiskAgent",
                    }

                if isinstance(risks, dict):
                    summary = risks.get("summary", "No risks detected.")
                    return {
                        "answer": summary,
                        "confidence": "Medium",
                        "agent": "RiskAgent",
                    }

        # -----------------------------
        # 🔎 Default: QueryAgent
        # -----------------------------
        result = rag_answer(
            question=question,
            repo_id=repo_id,
            show_sources=params.get("show_sources", False),
            show_confidence=params.get("show_confidence", False),
        )

        result["agent"] = "QueryAgent"
        return result
