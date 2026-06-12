"""责任界定分析器（P2 增强版）

V1（单条）：LLM 对单条缺陷做责任界定 + 漏测原因
V2（批量）：基于全部台账数据做团队/人员两级画像，识别系统性责任分布

核心新增能力：
1. 批量责任画像 - 团队/人员/责任比维度
2. 离职人员风险标记 - 识别知识断层风险
3. 漏测原因启发式规则（确定性，无 LLM）
4. 责任比规范化建议
"""

import re
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional, Tuple

from ..models import Defect
from ..utils import LLMClient, Sanitizer, MarkdownFormatter
from ..prompts import PromptTemplates


# ============================================================
# 漏测原因启发式规则
# ============================================================
# (problem_type 关键字, 推断的漏测原因, 改进建议)
LEAK_HEURISTICS = [
    {
        "problem_keywords": ["权限控制", "越权", "未授权"],
        "channel_keywords": ["安全测试"],
        "leak_reason": "日常自测对越权场景零覆盖，依赖外部安全测试发现",
        "improvement": "测试用例库增加越权场景必检：身份字段替换/删除、跨用户访问、批量遍历",
    },
    {
        "problem_keywords": ["代码逻辑错误"],
        "channel_keywords": ["业务反馈"],
        "leak_reason": "测试用例未覆盖该业务流程，发布前漏测",
        "improvement": "补充端到端业务流程测试用例；建立用例覆盖率门禁",
    },
    {
        "problem_keywords": ["特殊/极端场景未处理", "并发", "事务"],
        "channel_keywords": [],
        "leak_reason": "测试场景偏向正常路径，特殊/极端/并发场景覆盖不足",
        "improvement": "增加边界值测试、压力测试、混沌工程注入",
    },
    {
        "problem_keywords": ["前端展示", "样式"],
        "channel_keywords": ["业务反馈"],
        "leak_reason": "前端兼容性测试矩阵不全（机型/浏览器/分辨率）",
        "improvement": "建立 UI 自动化截图回归 + 多机型测试矩阵",
    },
    {
        "problem_keywords": ["枚举", "未做非空校验", "防重校验"],
        "channel_keywords": [],
        "leak_reason": "代码评审未捕获基础健壮性问题",
        "improvement": "Code Review checklist 增加防御性编程检查项；引入 SonarQube 静态扫描",
    },
    {
        "problem_keywords": ["需求理解不足"],
        "channel_keywords": [],
        "leak_reason": "需求评审环节缺失或不充分",
        "improvement": "强化需求评审：开发/测试同时参与；输出可验收的需求 DoD",
    },
]


def infer_leak_reasons(defect: Defect) -> List[Dict[str, str]]:
    """对单条缺陷推断漏测原因"""
    pt = (defect.problem_type or "").lower()
    ch = defect.discovery_channel or ""
    matches = []
    for rule in LEAK_HEURISTICS:
        # problem_type 命中
        if rule["problem_keywords"] and not any(kw in pt for kw in rule["problem_keywords"]):
            continue
        # channel 命中（若指定了 channel 限定）
        if rule["channel_keywords"] and ch not in rule["channel_keywords"]:
            continue
        matches.append({
            "leak_reason": rule["leak_reason"],
            "improvement": rule["improvement"],
        })
    return matches


# ============================================================
# 责任比规范化
# ============================================================
def normalize_ratio(ratio: Optional[str]) -> Optional[Tuple[float, float, bool]]:
    """将责任比字符串解析为 (dev, test, was_excel_polluted) 三元组

    Returns:
        (dev_ratio, test_ratio, was_polluted)
        was_polluted=True 表示原始值是 Excel 时间误识别（如 01:00:00）

    支持格式：
        "0.5:0.5" / "0.7:0.3" / "1:0" / "01:00:00"（Excel 误识别）/ "无"
    """
    if not ratio or ratio.strip() in ("无", "待确认", "存量", "-", "—"):
        return None
    s = ratio.strip()
    # Excel 时间格式（HH:MM:SS）→ 视为污染但仍按 H:M 解析
    m = re.match(r"^(\d{1,2}):(\d{2}):\d{2}$", s)
    if m:
        return (float(m.group(1)), float(m.group(2)), True)
    # 标准格式 a:b
    m = re.match(r"^([\d.]+):([\d.]+)$", s)
    if m:
        return (float(m.group(1)), float(m.group(2)), False)
    return None


# ============================================================
# 主分析器
# ============================================================
class ResponsibilityAnalyzer:
    """责任界定分析器（V2）"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer):
        self.llm = llm
        self.sanitizer = sanitizer

    # ============ V1: 单条责任分析 ============
    def analyze(self, defect: Defect, context: str = "") -> str:
        """对单条缺陷做责任界定（保留原能力）"""
        defect_info = self._build_defect_info(defect)
        clean_context = self.sanitizer.sanitize(context) if context else "（未提供）"
        leak_hints = infer_leak_reasons(defect)
        if leak_hints:
            clean_context += "\n\n【启发式漏测原因推断】\n" + "\n".join(
                f"- {h['leak_reason']}：{h['improvement']}" for h in leak_hints
            )

        prompt = PromptTemplates.RESPONSIBILITY_USER.format(
            defect_info=defect_info,
            context=clean_context,
        )
        try:
            return self.llm.complete(PromptTemplates.SYSTEM_BASE, prompt)
        except Exception as e:
            return self._single_fallback(defect, leak_hints, str(e))

    # ============ V2: 批量责任画像 ============
    def analyze_batch(self, defects: List[Defect]) -> str:
        """批量责任画像 - 不依赖 LLM 的确定性输出"""
        valid = [d for d in defects if d.is_valid]
        if not valid:
            return "## 责任界定批量画像\n\n暂无有效缺陷。"

        stats = self._compute_batch_stats(valid)
        return self._render_batch_report(valid, stats)

    # ---------- 批量统计 ----------
    def _compute_batch_stats(self, defects: List[Defect]) -> Dict[str, Any]:
        total = len(defects)

        # 责任端
        owner_counter: Counter = Counter()
        for d in defects:
            for o in d.issue_owner_list():
                owner_counter[o] += 1

        # 开发/测试人员
        dev_counter = Counter(d.dev_owner or "未指派" for d in defects)
        test_counter = Counter(d.test_owner or "未指派" for d in defects)

        # 离职风险
        dev_left_defects = [d for d in defects if d.dev_has_left()]
        test_left_defects = [d for d in defects if d.test_has_left()]
        unique_left_devs = sorted({d.dev_owner for d in dev_left_defects if d.dev_owner})
        unique_left_tests = sorted({d.test_owner for d in test_left_defects if d.test_owner})

        # 责任比规范化
        ratio_distribution: Counter = Counter()
        ratio_polluted: List[Defect] = []          # 完全无法解析的脏值
        ratio_excel_polluted: List[Defect] = []    # Excel 时间误识别（可自动修复）
        for d in defects:
            raw = d.responsibility_ratio or d.responsibility
            parsed = normalize_ratio(raw)
            if parsed is None and raw:
                ratio_polluted.append(d)
            elif parsed:
                dev_r, test_r, was_excel = parsed
                ratio_distribution[f"{dev_r}:{test_r}"] += 1
                if was_excel:
                    ratio_excel_polluted.append(d)

        # 漏测原因聚合（启发式）
        leak_counter: Counter = Counter()
        improvement_set: set = set()
        for d in defects:
            for hint in infer_leak_reasons(d):
                leak_counter[hint["leak_reason"]] += 1
                improvement_set.add(hint["improvement"])

        # 模块 × 责任端交叉
        module_owner_matrix: Dict[str, Counter] = defaultdict(Counter)
        for d in defects:
            for o in d.issue_owner_list():
                module_owner_matrix[d.module][o] += 1

        return {
            "total":                total,
            "owner_counter":        owner_counter,
            "dev_counter":          dev_counter,
            "test_counter":         test_counter,
            "dev_left_defects":     dev_left_defects,
            "test_left_defects":    test_left_defects,
            "unique_left_devs":     unique_left_devs,
            "unique_left_tests":    unique_left_tests,
            "ratio_distribution":   ratio_distribution,
            "ratio_polluted":       ratio_polluted,
            "ratio_excel_polluted": ratio_excel_polluted,
            "leak_counter":         leak_counter,
            "improvement_set":      improvement_set,
            "module_owner_matrix":  module_owner_matrix,
        }

    # ---------- 批量报告 ----------
    def _render_batch_report(self, defects: List[Defect],
                              stats: Dict[str, Any]) -> str:
        md = MarkdownFormatter()
        total = stats["total"]
        md.title("责任界定批量画像", 1)

        # === 1. 责任端分布 ===
        md.title("1. 责任端整体分布", 2)
        rows = [[o, c, MarkdownFormatter.percent(c, total)]
                for o, c in stats["owner_counter"].most_common()]
        md.table(["责任端", "缺陷数", "占比"], rows)

        # === 2. 开发人员 Top 10 ===
        md.title("2. 开发人员负责缺陷数 Top 10", 2)
        rows = []
        for name, c in stats["dev_counter"].most_common(10):
            tag = "🟡 已离场" if "离场" in name else ""
            rows.append([name, c, MarkdownFormatter.percent(c, total), tag])
        md.table(["开发人员", "缺陷数", "占比", "状态"], rows)

        # === 3. 测试人员 Top 10 ===
        md.title("3. 测试人员负责缺陷数 Top 10", 2)
        rows = []
        for name, c in stats["test_counter"].most_common(10):
            tag = "🟡 已离场" if "离场" in name else ""
            rows.append([name, c, MarkdownFormatter.percent(c, total), tag])
        md.table(["测试人员", "缺陷数", "占比", "状态"], rows)

        # === 4. 离职人员风险 ===
        md.title("4. 离职人员代码维护风险", 2)
        if stats["dev_left_defects"]:
            md.text(
                f"⚠️ 共 **{len(stats['dev_left_defects'])}** 条缺陷的开发已离场，"
                f"涉及 **{len(stats['unique_left_devs'])}** 位开发人员："
            )
            md.bullet_list([
                f"`{name}` - 负责 "
                f"{sum(1 for d in stats['dev_left_defects'] if d.dev_owner == name)} 条缺陷"
                for name in stats["unique_left_devs"]
            ])
            md.text("**建议**：")
            md.bullet_list([
                "对这些缺陷做知识库沉淀（root_cause + fix_method 完整入库）",
                "代码所有权重新分配并完成 KT",
                "新缺陷复发时优先在知识库查重，避免重复定位",
            ])
        else:
            md.text("✅ 无离职开发人员相关缺陷")

        if stats["test_left_defects"]:
            md.text(
                f"\n⚠️ 共 **{len(stats['test_left_defects'])}** 条缺陷的测试已离场，"
                f"涉及测试人员：{', '.join(f'`{n}`' for n in stats['unique_left_tests'])}"
            )

        # === 5. 责任比分布 ===
        md.title("5. 责任比分布", 2)
        if stats["ratio_distribution"]:
            rows = [[r, c, MarkdownFormatter.percent(c, total)]
                    for r, c in stats["ratio_distribution"].most_common()]
            md.table(["责任比 (dev:test)", "缺陷数", "占比"], rows)
        else:
            md.text("_无规范化责任比数据_")

        if stats["ratio_polluted"]:
            md.title("5.1 责任比格式异常（需治理）", 3)
            md.text(f"⚠️ 共 **{len(stats['ratio_polluted'])}** 条缺陷的责任比格式异常：")
            rows = [
                [d.id, d.responsibility_ratio or d.responsibility or ""]
                for d in stats["ratio_polluted"][:10]
            ]
            md.table(["缺陷ID", "当前值"], rows)
            md.text("**建议**：规范为 `0.5:0.5` 或 `0.7:0.3` 格式（dev:test 责任占比）")

        if stats["ratio_excel_polluted"]:
            md.title("5.2 Excel 时间格式误识别（已自动修复但需治理）", 3)
            md.text(
                f"⚠️ 共 **{len(stats['ratio_excel_polluted'])}** 条责任比被 Excel 误识别为时间："
            )
            rows = [
                [d.id,
                 d.responsibility_ratio or d.responsibility or "",
                 "已自动按 H:M 解析"]
                for d in stats["ratio_excel_polluted"][:10]
            ]
            md.table(["缺陷ID", "原始值", "处理"], rows)
            md.text("**建议**：在台账模板中将责任比列设置为文本格式，避免 Excel 自动转换")

        # === 6. 漏测原因聚合 ===
        md.title("6. 漏测原因聚合（启发式推断）", 2)
        if stats["leak_counter"]:
            rows = [[reason, c, MarkdownFormatter.percent(c, total)]
                    for reason, c in stats["leak_counter"].most_common()]
            md.table(["漏测原因", "命中缺陷数", "占比"], rows)

            md.title("6.1 改进建议汇总", 3)
            md.bullet_list(sorted(stats["improvement_set"]))
        else:
            md.text("_无足够数据推断漏测原因_")

        # === 7. 模块 × 责任端交叉 ===
        md.title("7. 模块 × 责任端交叉", 2)
        modules = list(stats["module_owner_matrix"].keys())[:8]
        if modules:
            all_owners = sorted({
                o for m in modules for o in stats["module_owner_matrix"][m]
            })
            headers = ["模块"] + all_owners
            rows = []
            for m in modules:
                row = [m]
                for o in all_owners:
                    row.append(stats["module_owner_matrix"][m].get(o, 0))
                rows.append(row)
            md.table(headers, rows)

        return md.render()

    # ---------- 单条降级 ----------
    def _build_defect_info(self, defect: Defect) -> str:
        rows = [
            f"- **ID**: {defect.id}",
            f"- **缺陷名称**: {self.sanitizer.sanitize(defect.defect_name or '')}",
            f"- **模块**: {defect.module}",
            f"- **优先级**: {defect.priority.value}",
            f"- **问题类型**: {defect.problem_type or '未指定'}",
            f"- **责任端**: {defect.issue_owner or '未指定'}",
            f"- **开发**: {defect.dev_owner or '未指派'}",
            f"- **测试**: {defect.test_owner or '未指派'}",
            f"- **责任比**: {defect.responsibility_ratio or defect.responsibility or '未划分'}",
            f"- **发现渠道**: {defect.discovery_channel or '未指定'}",
        ]
        if defect.root_cause:
            rows.append(f"- **根因**: {self.sanitizer.sanitize(defect.root_cause)[:300]}")
        return "\n".join(rows)

    def _single_fallback(self, defect: Defect,
                          leak_hints: List[Dict],
                          err: str) -> str:
        md = MarkdownFormatter()
        md.title("责任界定报告（无 LLM 降级版）", 2)
        md.title("缺陷信息", 3)
        md.text(self._build_defect_info(defect))

        md.title("初步判定", 3)
        if defect.responsibility_ratio:
            parsed = normalize_ratio(defect.responsibility_ratio)
            if parsed:
                dev_r, test_r, was_excel = parsed
                md.kv_pairs({
                    "开发责任比": f"{dev_r}",
                    "测试责任比": f"{test_r}",
                    "主责方":     "开发" if dev_r > test_r else ("测试" if test_r > dev_r else "双方等责"),
                })
                if was_excel:
                    md.text(
                        f"⚠️ 原始值 `{defect.responsibility_ratio}` 是 Excel 时间格式误识别，"
                        "已按 H:M 自动解析，建议在台账中规范化"
                    )
            else:
                md.text(f"⚠️ 责任比格式异常：`{defect.responsibility_ratio}`，需规范化")
        else:
            md.text("⚠️ 责任比未填写，建议人工评定")

        if leak_hints:
            md.title("漏测原因推断", 3)
            for h in leak_hints:
                md.text(f"- **{h['leak_reason']}** → {h['improvement']}")

        md.title("说明", 3)
        md.text(f"_LLM 调用失败：{err}_\n配置 LLM API Key 后可获得完整责任界定与改进建议。")
        return md.render()
