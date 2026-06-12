"""生产缺陷神探 - 智能体主入口（V1 P0+P1 + P2）"""

from pathlib import Path
from typing import List, Optional, Dict, Any
import yaml

from .models import Defect
from .utils import LLMClient, Sanitizer
from .analyzers import (
    RCAAnalyzer,
    ClusterAnalyzer,
    ResponsibilityAnalyzer,
    DedupAnalyzer,
    TrendAnalyzer,
    QualityAnalyzer,
    GovernanceAnalyzer,
    KBAnalyzer,
)


DEFAULT_CONFIG_PATH = "config/config.yaml"


class DefectHunterAgent:
    """生产缺陷神探智能体"""

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config = self._load_config(config_path)

        # 基础组件
        self.llm = LLMClient.from_config(self.config)
        self.sanitizer = Sanitizer.from_config(self.config)

        kb_cfg = self.config.get("knowledge_base", {})
        kb_path = kb_cfg.get("path", "data/knowledge_base/defects.jsonl")
        kb_threshold = kb_cfg.get("similarity_threshold", 0.3)

        # ===== P0 分析器 =====
        self.quality_analyzer = QualityAnalyzer()
        self.cluster_analyzer = ClusterAnalyzer(self.llm, self.sanitizer)
        self.trend_analyzer = TrendAnalyzer(self.llm, self.sanitizer)

        # ===== P1 分析器 =====
        self.rca_analyzer = RCAAnalyzer(self.llm, self.sanitizer)
        self.governance_analyzer = GovernanceAnalyzer(self.llm, self.sanitizer)

        # ===== P2 分析器 =====
        self.responsibility_analyzer = ResponsibilityAnalyzer(self.llm, self.sanitizer)
        self.dedup_analyzer = DedupAnalyzer(
            self.llm, self.sanitizer,
            kb_path=kb_path, similarity_threshold=kb_threshold,
        )
        self.kb_analyzer = KBAnalyzer(
            self.llm, self.sanitizer,
            kb_path=kb_path, similarity_threshold=kb_threshold,
        )

    def _load_config(self, path: str) -> dict:
        cfg_path = Path(path)
        if not cfg_path.exists():
            return {}
        with cfg_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # ============ P0 能力 ============
    def quality(self, defects: List[Defect]) -> Dict[str, Any]:
        return self.quality_analyzer.analyze(defects)

    def cluster(self, defects: List[Defect]) -> str:
        return self.cluster_analyzer.analyze(defects)

    def trend(self, defects: List[Defect], period: str = "") -> str:
        return self.trend_analyzer.analyze(defects, period=period)

    # ============ P1 能力 ============
    def rca(self, defect: Optional[Defect] = None, raw_log: str = "") -> str:
        return self.rca_analyzer.analyze(defect=defect, raw_log=raw_log)

    def governance(self, defects: List[Defect],
                    pattern_id: Optional[str] = None) -> str:
        return self.governance_analyzer.analyze(defects, pattern_id=pattern_id)

    # ============ P2 能力 ============
    def responsibility(self, defect: Defect, context: str = "") -> str:
        """单条责任界定（保留原能力）"""
        return self.responsibility_analyzer.analyze(defect, context)

    def responsibility_batch(self, defects: List[Defect]) -> str:
        """批量责任画像（P2 新增）"""
        return self.responsibility_analyzer.analyze_batch(defects)

    def dedup(self, new_defect: Defect, top_k: int = 3) -> str:
        return self.dedup_analyzer.analyze(new_defect, top_k=top_k)

    def add_to_kb(self, defect: Defect) -> None:
        self.dedup_analyzer.append_kb(defect)

    # ===== P2: 知识库批量分析 =====
    def kb_import(self, defects: List[Defect]) -> Dict[str, Any]:
        """批量入库"""
        return self.kb_analyzer.import_batch(defects)

    def kb_profile(self) -> str:
        """知识库画像"""
        return self.kb_analyzer.profile()

    def kb_cross_check(self, defects: List[Defect]) -> Dict[str, Any]:
        """批量交叉查重（结构化结果）"""
        return self.kb_analyzer.cross_check(defects)

    def kb_cross_check_report(self, defects: List[Defect]) -> str:
        """批量交叉查重 Markdown 报告"""
        return self.kb_analyzer.cross_check_report(defects)

    def kb_historical_patterns(self) -> str:
        """知识库历史模式归纳"""
        return self.kb_analyzer.historical_patterns()
