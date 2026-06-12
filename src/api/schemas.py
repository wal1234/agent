"""API Pydantic Schema"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ============================================================
# 请求模型
# ============================================================
class DefectIn(BaseModel):
    """单条缺陷输入（与 Defect 模型字段对齐）"""
    id: str
    defect_name: Optional[str] = None
    occurrence_time: Optional[str] = None
    task_id: Optional[str] = None
    module: str = "UNKNOWN"
    has_loss: Optional[bool] = None
    defect_type: str = "UNKNOWN"
    problem_type: Optional[str] = None
    discovery_channel: Optional[str] = None
    priority: str = "UNKNOWN"
    root_cause: Optional[str] = None
    fix_method: Optional[str] = None
    fix_status: Optional[str] = None
    fixer: Optional[str] = None
    tester: Optional[str] = None
    planned_release_time: Optional[str] = None
    scope: Optional[str] = None
    issue_owner: Optional[str] = None
    responsibility_ratio: Optional[str] = None
    dev_owner: Optional[str] = None
    test_owner: Optional[str] = None
    responsibility: Optional[str] = None
    story_id: Optional[str] = None
    story_name: Optional[str] = None
    story_release_time: Optional[str] = None
    test_cause_analysis: Optional[str] = None
    is_automated: Optional[bool] = None
    automation_analysis: Optional[str] = None
    source: Optional[str] = None
    original_index: Optional[int] = None

    class Config:
        extra = "allow"   # 允许额外字段，避免破坏前向兼容


class TaiZhangIn(BaseModel):
    """台账完整输入"""
    metadata: Optional[Dict[str, Any]] = None
    defects: List[DefectIn] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """统一分析请求"""
    period: Optional[str] = ""
    governance_pattern: Optional[str] = None
    taizhang: TaiZhangIn


class TrendRequest(BaseModel):
    period: Optional[str] = ""
    taizhang: TaiZhangIn


class GovernanceRequest(BaseModel):
    pattern: Optional[str] = None
    taizhang: TaiZhangIn


class RCAFromTaiZhangRequest(BaseModel):
    defect_id: str
    raw_log: Optional[str] = ""
    taizhang: TaiZhangIn


class RCAFromLogRequest(BaseModel):
    raw_log: str


# ============================================================
# P2 请求模型
# ============================================================
class ResponsibilitySingleRequest(BaseModel):
    """单条责任界定（指定缺陷 ID）"""
    defect_id: str
    context: Optional[str] = ""
    taizhang: TaiZhangIn


class ResponsibilityBatchRequest(BaseModel):
    """批量责任画像"""
    taizhang: TaiZhangIn


class KBImportRequest(BaseModel):
    """批量入库"""
    taizhang: TaiZhangIn


class KBCrossCheckRequest(BaseModel):
    """批量交叉查重"""
    taizhang: TaiZhangIn


# ============================================================
# 响应模型
# ============================================================
class MarkdownReport(BaseModel):
    """Markdown 报告响应"""
    report_md: str = Field(..., description="Markdown 格式报告")


class QualityResponse(BaseModel):
    summary: Dict[str, Any]
    issues: List[Dict[str, Any]]
    report_md: str


class AnalysisSummary(BaseModel):
    total_defects: int
    valid_defects: int
    completeness: float
    pollution_count: int


class FullAnalysisResponse(BaseModel):
    """一键分析的完整响应"""
    summary: AnalysisSummary
    reports: Dict[str, str]   # quality / cluster / trend / governance
    issues: List[Dict[str, Any]]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "defect-hunter"
    version: str = "1.0.0"
    llm_configured: bool
    llm_provider: str = ""
    llm_model: str = ""


# ============================================================
# P2 响应模型
# ============================================================
class KBImportResponse(BaseModel):
    imported: int
    skipped_invalid: int
    skipped_duplicate: int
    total: int
    kb_size_after: int


class KBCrossCheckSummary(BaseModel):
    total: int
    new: int
    similar: int
    regression: int
    kb_size: int


class KBCrossCheckItem(BaseModel):
    defect_id: str
    defect_name: str
    module: str
    judgement: str
    top_score: float
    matches: List[Dict[str, Any]]


class KBCrossCheckResponse(BaseModel):
    summary: KBCrossCheckSummary
    items: List[KBCrossCheckItem]
