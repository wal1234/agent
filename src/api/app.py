"""FastAPI 应用 - 缺陷神探 V2 服务化入口"""

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware

from ..agent import DefectHunterAgent
from ..graph import build_analysis_graph
from ..loaders import load_taizhang_json
from .converters import taizhang_to_defects, defect_in_to_model
from .schemas import (
    HealthResponse, QualityResponse, MarkdownReport, FullAnalysisResponse,
    AnalysisSummary,
    AnalyzeRequest, TrendRequest, GovernanceRequest,
    RCAFromTaiZhangRequest, RCAFromLogRequest,
    TaiZhangIn,
    # P2
    ResponsibilitySingleRequest, ResponsibilityBatchRequest,
    KBImportRequest, KBCrossCheckRequest,
    KBImportResponse, KBCrossCheckResponse,
)


logger = logging.getLogger("defect_hunter.api")


def create_app(config_path: str = "config/config.yaml") -> FastAPI:
    """创建 FastAPI 应用实例

    Args:
        config_path: 配置文件路径
    """
    app = FastAPI(
        title="生产缺陷神探 API",
        description="高码智能体 V2 - 数据质量 / 聚类 / 趋势 / RCA / 专项治理",
        version="1.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 单例 agent + 编排图（启动时初始化）
    agent = DefectHunterAgent(config_path=config_path)
    graph_runner = build_analysis_graph(agent)
    app.state.agent = agent
    app.state.graph = graph_runner

    # ==================================================
    # 健康检查
    # ==================================================
    @app.get("/api/v1/health", response_model=HealthResponse, tags=["meta"])
    def health():
        return HealthResponse(
            status="ok",
            llm_configured=bool(agent.llm.api_key),
            llm_provider=agent.llm.provider,
            llm_model=agent.llm.model,
        )

    # ==================================================
    # 单项分析能力（P0 + P1）
    # ==================================================
    @app.post("/api/v1/analyze/quality",
                response_model=QualityResponse,
                tags=["analyze"],
                summary="数据质量分析（P0）")
    def analyze_quality(req: AnalyzeRequest):
        defects = taizhang_to_defects(req.taizhang)
        result = agent.quality(defects)
        return QualityResponse(
            summary=result["summary"],
            issues=result["issues"],
            report_md=result["report_md"],
        )

    @app.post("/api/v1/analyze/cluster",
                response_model=MarkdownReport,
                tags=["analyze"],
                summary="缺陷聚类分析（P0）")
    def analyze_cluster(req: AnalyzeRequest):
        defects = taizhang_to_defects(req.taizhang)
        return MarkdownReport(report_md=agent.cluster(defects))

    @app.post("/api/v1/analyze/trend",
                response_model=MarkdownReport,
                tags=["analyze"],
                summary="汇总与趋势分析（P0）")
    def analyze_trend(req: TrendRequest):
        defects = taizhang_to_defects(req.taizhang)
        return MarkdownReport(report_md=agent.trend(defects, period=req.period or ""))

    @app.post("/api/v1/analyze/governance",
                response_model=MarkdownReport,
                tags=["analyze"],
                summary="专项治理报告（P1）")
    def analyze_governance(req: GovernanceRequest):
        defects = taizhang_to_defects(req.taizhang)
        return MarkdownReport(report_md=agent.governance(defects, pattern_id=req.pattern))

    @app.post("/api/v1/analyze/rca",
                response_model=MarkdownReport,
                tags=["analyze"],
                summary="RCA 根因分析（P1）- 从台账中选某条做分析")
    def analyze_rca_from_taizhang(req: RCAFromTaiZhangRequest):
        defects = taizhang_to_defects(req.taizhang)
        target = next((d for d in defects if d.id == req.defect_id), None)
        if not target:
            raise HTTPException(404, f"未找到缺陷 {req.defect_id}")
        return MarkdownReport(
            report_md=agent.rca(defect=target, raw_log=req.raw_log or "")
        )

    @app.post("/api/v1/analyze/rca-log",
                response_model=MarkdownReport,
                tags=["analyze"],
                summary="RCA 根因分析（P1）- 仅基于错误日志")
    def analyze_rca_from_log(req: RCAFromLogRequest):
        if not req.raw_log.strip():
            raise HTTPException(400, "raw_log 不能为空")
        return MarkdownReport(report_md=agent.rca(raw_log=req.raw_log))

    # ==================================================
    # 一键全套分析（P0 + P1 通过 LangGraph 编排）
    # ==================================================
    @app.post("/api/v1/analyze/full",
                response_model=FullAnalysisResponse,
                tags=["analyze"],
                summary="一键执行 P0 + P1 全套分析（编排子图）")
    def analyze_full(req: AnalyzeRequest):
        defects = taizhang_to_defects(req.taizhang)
        if not defects:
            raise HTTPException(400, "台账中无任何缺陷")

        result = graph_runner.invoke({
            "defects": defects,
            "period": req.period or "",
            "governance_pattern": req.governance_pattern,
        })
        final = result["final"]
        return FullAnalysisResponse(
            summary=AnalysisSummary(**final["summary"]),
            reports=final["reports"],
            issues=final["issues"],
        )

    # ==================================================
    # P2: 责任界定
    # ==================================================
    @app.post("/api/v1/analyze/responsibility/single",
                response_model=MarkdownReport,
                tags=["P2"],
                summary="单条责任界定（P2）")
    def responsibility_single(req: ResponsibilitySingleRequest):
        defects = taizhang_to_defects(req.taizhang)
        target = next((d for d in defects if d.id == req.defect_id), None)
        if not target:
            raise HTTPException(404, f"未找到缺陷 {req.defect_id}")
        return MarkdownReport(
            report_md=agent.responsibility(target, context=req.context or "")
        )

    @app.post("/api/v1/analyze/responsibility/batch",
                response_model=MarkdownReport,
                tags=["P2"],
                summary="批量责任画像（P2）- 团队/人员/离职/漏测原因")
    def responsibility_batch(req: ResponsibilityBatchRequest):
        defects = taizhang_to_defects(req.taizhang)
        return MarkdownReport(report_md=agent.responsibility_batch(defects))

    # ==================================================
    # P2: 知识库批量分析
    # ==================================================
    @app.post("/api/v1/kb/import",
                response_model=KBImportResponse,
                tags=["P2"],
                summary="批量入知识库（P2）")
    def kb_import(req: KBImportRequest):
        defects = taizhang_to_defects(req.taizhang)
        result = agent.kb_import(defects)
        return KBImportResponse(**result)

    @app.get("/api/v1/kb/profile",
                response_model=MarkdownReport,
                tags=["P2"],
                summary="知识库画像（P2）")
    def kb_profile():
        return MarkdownReport(report_md=agent.kb_profile())

    @app.post("/api/v1/kb/cross-check",
                response_model=KBCrossCheckResponse,
                tags=["P2"],
                summary="批量交叉查重（P2）- 新批次 vs 历史库")
    def kb_cross_check(req: KBCrossCheckRequest):
        defects = taizhang_to_defects(req.taizhang)
        result = agent.kb_cross_check(defects)
        return KBCrossCheckResponse(**result)

    @app.post("/api/v1/kb/cross-check/report",
                response_model=MarkdownReport,
                tags=["P2"],
                summary="批量查重 Markdown 报告（P2）")
    def kb_cross_check_report(req: KBCrossCheckRequest):
        defects = taizhang_to_defects(req.taizhang)
        return MarkdownReport(report_md=agent.kb_cross_check_report(defects))

    @app.get("/api/v1/kb/historical-patterns",
                response_model=MarkdownReport,
                tags=["P2"],
                summary="知识库历史问题模式归纳（P2）")
    def kb_historical_patterns():
        return MarkdownReport(report_md=agent.kb_historical_patterns())

    # ==================================================
    # 文件上传式调用（便于 curl / Web 上传 JSON 文件）
    # ==================================================
    @app.post("/api/v1/analyze/upload",
                response_model=FullAnalysisResponse,
                tags=["analyze"],
                summary="上传台账 JSON 文件做一键分析")
    async def analyze_upload(
        file: UploadFile = File(..., description="台账 JSON 文件"),
        period: str = Form(""),
        governance_pattern: Optional[str] = Form(None),
    ):
        if not file.filename.endswith(".json"):
            raise HTTPException(400, "仅支持 JSON 文件")
        import json
        try:
            data = json.loads((await file.read()).decode("utf-8"))
        except Exception as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")

        # 兼容两种输入格式（{defects:[...]} 或 [...]）
        if isinstance(data, list):
            taizhang = TaiZhangIn(defects=data)
        elif isinstance(data, dict):
            taizhang = TaiZhangIn(**data)
        else:
            raise HTTPException(400, "JSON 顶层必须是 dict 或 list")

        defects = taizhang_to_defects(taizhang)
        result = graph_runner.invoke({
            "defects": defects,
            "period": period,
            "governance_pattern": governance_pattern,
        })
        final = result["final"]
        return FullAnalysisResponse(
            summary=AnalysisSummary(**final["summary"]),
            reports=final["reports"],
            issues=final["issues"],
        )

    return app


# 默认应用实例（供 uvicorn 直接 import）
app = create_app()
