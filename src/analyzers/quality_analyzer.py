"""数据质量分析器（P0 核心）

输入：缺陷列表
输出：Markdown 报告 + 结构化 quality_issues.json

设计原则：
- 全部基于规则，零 LLM 依赖（确定性 > 创造性）
- 检测规则全部来自真实台账已发现的污染案例
- 同时输出可执行的"补全建议清单"
"""

import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Callable

from ..models import (
    Defect, Priority, DefectType,
    KNOWN_MODULES, KNOWN_DISCOVERY_CHANNELS,
    KNOWN_FIX_STATUSES, KNOWN_ISSUE_OWNERS,
)
from ..utils import MarkdownFormatter


# ============================================================
# 数据质量问题模型
# ============================================================
@dataclass
class QualityIssue:
    """数据质量问题"""
    defect_id: str
    field_name: str
    issue_type: str       # missing / polluted / inconsistent / unknown_value
    severity: str         # critical / serious / moderate / minor
    current_value: Optional[str]
    detail: str
    suggestion: str


# ============================================================
# 污染检测规则
# ============================================================
# 规则签名：rule(defect, value) -> Optional[QualityIssue]
#   返回 None 表示该规则未命中

INVALID_PLACEHOLDERS = {"无", "待确认", "存量", "待定", "存量问题无法确认", "-", "—"}

POLLUTION_RULES = [
    # ===== story_id 字段 =====
    {
        "field": "story_id",
        "name": "story_id_is_date",
        "pattern": r"^\d{4}-\d{2}-\d{2}",
        "severity": "serious",
        "detail": "story_id 写成了日期格式（疑似 story_release_time 串位）",
        "suggestion": "清空或迁移至 story_release_time 字段",
    },
    {
        "field": "story_id",
        "name": "story_id_is_name",
        "pattern": r"^[一-龥]{2,4}$",
        "severity": "serious",
        "detail": "story_id 写成了中文人名",
        "suggestion": "清空 story_id，将人名迁移至对应责任人字段",
    },
    {
        "field": "story_release_time",
        "name": "release_time_is_task_id",
        "pattern": r"^YYZL-",
        "severity": "serious",
        "detail": "story_release_time 写成了 YYZL- 开头的任务 ID",
        "suggestion": "迁移该值至 story_id，并补齐真实发布时间",
    },
    # ===== responsibility / responsibility_ratio 字段 =====
    {
        "field": "responsibility",
        "name": "resp_is_time_format",
        "pattern": r"^\d{1,2}:\d{2}(:\d{2})?$",
        "severity": "serious",
        "detail": "Excel 把 1:0 误识别为时间格式 (如 01:00:00)，应为 1:0 责任比",
        "suggestion": "规范化为 '1:0' 或 '0.5:0.5' 格式，并迁移到 responsibility_ratio",
    },
    {
        "field": "responsibility_ratio",
        "name": "resp_ratio_is_time_format",
        "pattern": r"^\d{1,2}:\d{2}(:\d{2})?$",
        "severity": "serious",
        "detail": "Excel 把 1:0 误识别为时间格式 (如 01:00:00)",
        "suggestion": "规范化为 '1:0' 或 '0.5:0.5' 格式",
    },
]


def _check_pollution(defect: Defect) -> List[QualityIssue]:
    """对单条缺陷应用所有污染规则"""
    issues = []
    for rule in POLLUTION_RULES:
        value = getattr(defect, rule["field"], None)
        if not value:
            continue
        if re.search(rule["pattern"], str(value)):
            issues.append(QualityIssue(
                defect_id=defect.id,
                field_name=rule["field"],
                issue_type="polluted",
                severity=rule["severity"],
                current_value=str(value),
                detail=rule["detail"],
                suggestion=rule["suggestion"],
            ))
    return issues


def _check_placeholders(defect: Defect) -> List[QualityIssue]:
    """检测占位符污染（如 dev_owner="无"）"""
    issues = []
    placeholder_fields = [
        "dev_owner", "test_owner", "responsibility",
        "responsibility_ratio", "fix_method", "scope",
    ]
    for field_name in placeholder_fields:
        value = getattr(defect, field_name, None)
        if value and str(value).strip() in INVALID_PLACEHOLDERS:
            issues.append(QualityIssue(
                defect_id=defect.id,
                field_name=field_name,
                issue_type="unknown_value",
                severity="moderate",
                current_value=str(value),
                detail=f"字段值为占位符 '{value}'，无业务含义",
                suggestion=f"应置 null 或填写具体值",
            ))
    return issues


def _check_unknown_dict_values(defect: Defect) -> List[QualityIssue]:
    """检测枚举字段是否使用了未登记的值"""
    issues = []
    checks = [
        ("module", defect.module, KNOWN_MODULES),
        ("discovery_channel", defect.discovery_channel, KNOWN_DISCOVERY_CHANNELS),
        ("fix_status", defect.fix_status, KNOWN_FIX_STATUSES),
    ]
    for fname, value, dict_set in checks:
        if not value:
            continue
        if value not in dict_set and value != "UNKNOWN":
            issues.append(QualityIssue(
                defect_id=defect.id,
                field_name=fname,
                issue_type="inconsistent",
                severity="minor",
                current_value=str(value),
                detail=f"使用了字典外的值",
                suggestion=f"建议规范为字典内标准值，或将该值正式纳入字典",
            ))
    return issues


# ============================================================
# 关键字段缺失检测
# ============================================================
# (字段名, 严重度, 影响说明)
MISSING_FIELD_RULES = [
    ("defect_name",          "critical", "缺陷无法识别，整条记录不可用"),
    ("occurrence_time",      "critical", "无法做时间趋势分析"),
    ("module",               "serious",  "无法做模块维度统计"),
    ("priority",             "serious",  "无法评估严重程度分布"),
    ("root_cause",           "serious",  "无法做 RCA 查重与共性分析"),
    ("problem_type",         "moderate", "无法做问题类型聚类"),
    ("discovery_channel",    "moderate", "无法识别测试盲区"),
    ("issue_owner",          "moderate", "无法做责任端归集"),
    ("fix_method",           "moderate", "知识库无可借鉴方案"),
    ("dev_owner",            "minor",    "无法追溯开发责任人"),
    ("test_owner",           "minor",    "无法追溯测试责任人"),
    ("responsibility_ratio", "minor",    "无法做责任量化分析"),
    ("is_automated",         "minor",    "无法评估自动化覆盖率"),
    ("fix_status",           "minor",    "无法跟踪修复进度"),
]


def _is_missing(value: Any, field_name: str) -> bool:
    """判断字段是否缺失（含占位符判定）"""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, str) and value.strip() in INVALID_PLACEHOLDERS:
        return True
    # 枚举字段
    if field_name == "priority" and value == Priority.UNKNOWN:
        return True
    if field_name == "defect_type" and value == DefectType.UNKNOWN:
        return True
    if field_name == "module" and value == "UNKNOWN":
        return True
    return False


# ============================================================
# 主分析器
# ============================================================
class QualityAnalyzer:
    """数据质量分析器"""

    SEVERITY_ICON = {
        "critical": "🔴",
        "serious":  "🟠",
        "moderate": "🟡",
        "minor":    "⚪",
    }

    SEVERITY_LABEL = {
        "critical": "致命",
        "serious":  "严重",
        "moderate": "中等",
        "minor":    "轻微",
    }

    def __init__(self):
        pass

    # ---------- 主入口 ----------
    def analyze(self, defects: List[Defect]) -> Dict[str, Any]:
        """执行数据质量分析

        Returns:
            {
                "report_md": Markdown 文本,
                "issues":    List[Dict] - 结构化质量问题清单,
                "summary":   Dict - 关键指标摘要
            }
        """
        if not defects:
            return {
                "report_md": "## 数据质量分析报告\n\n暂无数据。",
                "issues": [],
                "summary": {"total": 0},
            }

        # 1. 全字段缺失率扫描
        missing_stats = self._scan_missing_fields(defects)

        # 2. 污染检测（逐条扫描）
        pollution_issues: List[QualityIssue] = []
        for d in defects:
            pollution_issues.extend(_check_pollution(d))
            pollution_issues.extend(_check_placeholders(d))
            pollution_issues.extend(_check_unknown_dict_values(d))

        # 3. 整体摘要
        summary = self._build_summary(defects, missing_stats, pollution_issues)

        # 4. 渲染 Markdown
        report_md = self._render_report(defects, missing_stats, pollution_issues, summary)

        return {
            "report_md": report_md,
            "issues": [asdict(i) for i in pollution_issues],
            "summary": summary,
        }

    # ---------- 缺失率扫描 ----------
    def _scan_missing_fields(self, defects: List[Defect]) -> List[Dict[str, Any]]:
        total = len(defects)
        results = []
        for field_name, severity, impact in MISSING_FIELD_RULES:
            missing_count = sum(
                1 for d in defects
                if _is_missing(getattr(d, field_name, None), field_name)
            )
            results.append({
                "field": field_name,
                "severity": severity,
                "impact": impact,
                "missing": missing_count,
                "total": total,
                "ratio": missing_count / total if total else 0,
            })
        return results

    # ---------- 摘要 ----------
    def _build_summary(self, defects: List[Defect],
                        missing_stats: List[Dict],
                        pollution_issues: List[QualityIssue]) -> Dict[str, Any]:
        total = len(defects)
        valid = sum(1 for d in defects if d.is_valid)
        empty = total - valid

        # 全空白记录数（defect_name 为空）
        # 关键字段缺失率均值
        critical_fields = [s for s in missing_stats if s["severity"] == "critical"]
        avg_critical_missing = (
            sum(s["ratio"] for s in critical_fields) / len(critical_fields)
            if critical_fields else 0
        )

        # 完整性评分（简单：100 - critical缺失率*100）
        completeness_score = max(0, 100 - avg_critical_missing * 100)

        return {
            "total": total,
            "valid_records": valid,
            "empty_records": empty,
            "empty_ratio": empty / total if total else 0,
            "pollution_count": len(pollution_issues),
            "critical_missing_avg_ratio": avg_critical_missing,
            "completeness_score": round(completeness_score, 1),
        }

    # ---------- 报告渲染 ----------
    def _render_report(self, defects: List[Defect],
                        missing_stats: List[Dict],
                        pollution_issues: List[QualityIssue],
                        summary: Dict) -> str:
        md = MarkdownFormatter()
        md.title("数据质量分析报告", 1)

        # === 数据概览 ===
        md.title("1. 数据概览", 2)
        md.kv_pairs({
            "缺陷总数":        summary["total"],
            "有效记录（含缺陷名）": f"{summary['valid_records']} 条 ({(1-summary['empty_ratio'])*100:.1f}%)",
            "空白记录":        f"{summary['empty_records']} 条 ({summary['empty_ratio']*100:.1f}%)",
            "字段污染问题数":  summary["pollution_count"],
            "完整性评分":      f"{summary['completeness_score']} / 100",
        })

        # 评分说明
        score = summary["completeness_score"]
        if score < 50:
            level = "🔴 极差 - 数据质量是当前最大风险，所有上层分析结果不可信"
        elif score < 70:
            level = "🟠 较差 - 应优先治理关键字段缺失"
        elif score < 90:
            level = "🟡 中等 - 部分字段需要补全"
        else:
            level = "🟢 良好 - 数据质量可支撑分析"
        md.text(f"**评估**: {level}")

        # === 字段缺失率 ===
        md.title("2. 字段缺失率扫描", 2)
        rows = []
        for s in missing_stats:
            icon = self.SEVERITY_ICON[s["severity"]]
            label = self.SEVERITY_LABEL[s["severity"]]
            rows.append([
                s["field"],
                f"{s['missing']}/{s['total']}",
                f"{s['ratio']*100:.1f}%",
                f"{icon} {label}",
                s["impact"],
            ])
        md.table(["字段", "缺失数", "缺失率", "严重度", "影响"], rows)

        # === 字段污染问题 ===
        md.title("3. 字段污染检测", 2)
        if not pollution_issues:
            md.text("✅ 未发现字段污染问题。")
        else:
            # 按字段分组统计
            field_counter = Counter(i.field_name for i in pollution_issues)
            md.text(f"共发现 **{len(pollution_issues)}** 个污染问题，涉及 **{len(field_counter)}** 个字段。")
            md.blank()

            md.title("3.1 按字段汇总", 3)
            rows = [[fname, count] for fname, count in field_counter.most_common()]
            md.table(["字段", "问题数"], rows)

            md.title("3.2 详细清单（按严重度排序）", 3)
            # 按严重度排序
            severity_order = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
            sorted_issues = sorted(
                pollution_issues,
                key=lambda i: (severity_order.get(i.severity, 9), i.defect_id)
            )
            rows = []
            for i in sorted_issues[:50]:  # 限制 50 行避免过长
                icon = self.SEVERITY_ICON[i.severity]
                rows.append([
                    i.defect_id,
                    i.field_name,
                    f"{icon} {self.SEVERITY_LABEL[i.severity]}",
                    f"`{i.current_value[:30] if i.current_value else ''}`",
                    i.detail,
                    i.suggestion,
                ])
            md.table(
                ["缺陷ID", "字段", "严重度", "当前值", "问题描述", "修复建议"],
                rows,
            )
            if len(sorted_issues) > 50:
                md.text(f"_（仅展示前 50 条，共 {len(sorted_issues)} 条）_")

        # === 补全建议（按优先级）===
        md.title("4. 治理建议（按优先级）", 2)
        md.title("4.1 立即整改（影响分析准确度）", 3)
        immediate = self._suggest_immediate(pollution_issues, missing_stats)
        if immediate:
            md.bullet_list(immediate)
        else:
            md.text("_无立即整改项_")

        md.title("4.2 治理性补全（提升知识库价值）", 3)
        long_term = self._suggest_long_term(missing_stats, summary)
        if long_term:
            md.bullet_list(long_term)

        # === 数据质量趋势改进路径 ===
        md.title("5. 改进路径", 2)
        md.text("**短期（1-2 周）**：")
        md.bullet_list([
            "推动 QA 团队处理『立即整改』清单中的污染字段",
            "建立台账模板字段下拉约束，避免新增脏数据",
        ])
        md.text("**中期（1-3 个月）**：")
        md.bullet_list([
            "补齐 fix_method、root_cause 字段（覆盖率目标 ≥ 70%）",
            "建立测试用例库联动，自动反向回填 is_automated 字段",
        ])
        md.text("**长期（3 个月以上）**：")
        md.bullet_list([
            "字段填写率纳入团队 KPI",
            "迁移台账至 PostgreSQL，强 schema 校验代替 Excel 散填",
        ])

        return md.render()

    # ---------- 治理建议生成 ----------
    def _suggest_immediate(self, pollution_issues: List[QualityIssue],
                            missing_stats: List[Dict]) -> List[str]:
        suggestions = []

        # 1. 按字段聚合污染问题
        field_groups: Dict[str, List[QualityIssue]] = {}
        for i in pollution_issues:
            field_groups.setdefault(i.field_name, []).append(i)

        for fname, issues in field_groups.items():
            critical_serious = [i for i in issues if i.severity in ("critical", "serious")]
            if critical_serious:
                ids = ", ".join(i.defect_id for i in critical_serious[:5])
                if len(critical_serious) > 5:
                    ids += f" 等 {len(critical_serious)} 条"
                top_suggestion = critical_serious[0].suggestion
                suggestions.append(
                    f"⚠️ **{fname}** 字段污染: {ids} → {top_suggestion}"
                )

        # 2. 致命缺失字段
        for s in missing_stats:
            if s["severity"] == "critical" and s["ratio"] > 0.3:
                suggestions.append(
                    f"⚠️ **{s['field']}** 缺失率 {s['ratio']*100:.0f}%（致命）：{s['impact']}"
                )
        return suggestions

    def _suggest_long_term(self, missing_stats: List[Dict],
                            summary: Dict) -> List[str]:
        suggestions = []

        # 0% 填写率字段
        for s in missing_stats:
            if s["ratio"] >= 0.99:
                suggestions.append(
                    f"📝 **{s['field']}** 填写率 {(1-s['ratio'])*100:.0f}%，建议追溯填补（影响：{s['impact']}）"
                )

        # 严重字段缺失率较高
        for s in missing_stats:
            if s["severity"] == "serious" and 0.3 <= s["ratio"] < 0.99:
                suggestions.append(
                    f"📝 **{s['field']}** 缺失率 {s['ratio']*100:.0f}%，建议补齐至 70% 以上"
                )

        return suggestions
