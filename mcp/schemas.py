# context_assist/mcp/schemas.py

from pydantic import BaseModel
from typing import List, Optional


# ----------------------------
# MCP CORE SCHEMAS
# ----------------------------

class AskRequest(BaseModel):
    question: str
    repo_id: Optional[str] = None  # ✅ NEW (multi-repo support)
    show_sources: bool = False
    show_confidence: bool = False


class AskResponse(BaseModel):
    answer: str
    confidence: Optional[str] = None
    sources: Optional[list] = None


class ReindexRequest(BaseModel):
    repo_id: Optional[str] = None
    repo_url: Optional[str] = None


class GenericResponse(BaseModel):
    status: str
    detail: Optional[str] = None


# ----------------------------
# MCP-ONLY MILESTONE SCHEMAS
# ----------------------------

class ListMilestonesRequest(BaseModel):
    repo_owner: str
    repo_name: str


class MilestoneItem(BaseModel):
    name: str
    status: str
    open_items: int
    closed_items: int


class ListMilestonesResponse(BaseModel):
    milestones: List[MilestoneItem]


# ----------------------------
# MCP-ONLY RISK SCHEMAS
# ----------------------------

class RiskSummaryRequest(BaseModel):
    repo_owner: str
    repo_name: str


class RiskItem(BaseModel):
    title: str
    url: str


class RiskSummaryResponse(BaseModel):
    summary: str
    items: List[RiskItem]
