"""缺陷汇总与趋势分析器（P0 增强版）

新增功能（基于真实台账特征）：
- 按 problem_type 拆词后做帕累托
- 按 discovery_channel / issue_owner / fix_status 分布
- 离职人员风险卡片（dev_owner 含"已离场"）
- 资损事件预警（has_loss=True）
- 时段对比（双 period 对比）
"""

from collections import Counter, defaultdict
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from ..models import Defect, Priority
from ..utils import LLMClient, Sanitizer, MarkdownFormatter
from ..prompts import PromptTemplates


class TrendAnalyzer:
    """缺陷汇总与趋势分析"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer):
        self.llm = llm
        self.sanitizer = sanitizer

    # ---------- 主入口 ----------
    def analyze(self, defects: List[Defect], period: str = "") -> str:
        valid = [d for d in defects if d.is_valid]
        invalid_count = len(defects) - len(valid)

        if not valid:
            return "## 缺陷汇总与趋势分析报告\n\n暂无有效缺陷数据。"

        metrics = self._compute_metrics(valid)
        pareto = self._pareto(metrics["problem_type"])
        patterns = self._detect_patterns(valid, metrics)

        local_report = self._render_local_report(
            valid, invalid_count, metrics, pareto, patterns, period
        )

        # LLM 改进策略（可选）
        llm_strategy = self._llm_strategy(metrics, pareto, patterns, period)
        if llm_strategy:
            local_report += "\n\n---\n\n## LLM 深度研判与改进策略\n\n" + llm_strategy

        return local_report

    # ---------- 多维统计 ----------
    def _compute_metrics(self, defects: List[Defect]) -> Dict[str, Any]:
        total = len(defects)

        # 基础分布
        priority_count = Counter(d.priority.value for d in defects)
        module_count = Counter(d.module or "UNKNOWN" for d in defects)
        channel_count = Counter(d.discovery_channel or "未指定" for d in defects)
        fix_status_count = Counter(d.fix_status or "未指定" for d in defects)

        # problem_type 拆词
        problem_type_count: Counter = Counter()
        for d in defects:
            for pt in d.problem_type_list():
                problem_type_count[pt] += 1

        # issue_owner 拆词
        issue_owner_count: Counter = Counter()
        for d in defects:
            for owner in d.issue_owner_list():
                issue_owner_count[owner] += 1

        # 模块内 P0/P1/HIGH 分布
        module_severity: Dict[str, Counter] = defaultdict(Counter)
        for d in defects:
            module_severity[d.module or "UNKNOWN"][d.priority.value] += 1

        # 时间序列（按月聚合）
        time_series = self._build_time_series(defects)

        # 严重缺陷
        severe = sum(1 for d in defects if d.is_severe)

        # 最不稳定模块
        unstable_module, unstable_score = "", -1.0
        for m, sev in module_severity.items():
            m_total = sum(sev.values())
            severe_in_m = sum(sev.get(p, 0) for p in ("P0", "P1", "HIGH"))
            score = m_total * (severe_in_m / m_total if m_total else 0)
            if score > unstable_score:
                unstable_score = score
                unstable_module = m

        # 资损事件
        loss_defects = [d for d in defects if d.has_loss is True]

        # 离职人员
        dev_left = [d for d in defects if d.dev_has_left()]
        test_left = [d for d in defects if d.test_has_left()]

        # 安全漏洞
        security_defects = [d for d in defects if d.is_security]

        return {
            "total": total,
            "severe": severe,
            "severe_ratio": severe / total if total else 0,
            "priority": priority_count,
            "module": module_count,
            "module_severity": module_severity,
            "problem_type": problem_type_count,
            "issue_owner": issue_owner_count,
            "discovery_channel": channel_count,
            "fix_status": fix_status_count,
            "time_series": time_series,
            "unstable_module": unstable_module,
            "loss_defects": loss_defects,
            "dev_left": dev_left,
            "test_left": test_left,
            "security_defects": security_defects,
        }

    def _build_time_series(self, defects: List[Defect]) -> List[Tuple[str, int]]:
        """按月聚合发生时间"""
        bucket: Counter = Counter()
        for d in defects:
            ts = d.occurrence_time
            if not ts:
                continue
            month = ts[:7]  # YYYY-MM
            bucket[month] += 1
        return sorted(bucket.items())

    # ---------- 帕累托 ----------
    def _pareto(self, counter: Counter) -> List[Tuple[str, int, float, float]]:
        total = sum(counter.values())
        if total == 0:
            return []
        result = []
        cumulative = 0
        for name, cnt in counter.most_common():
            ratio = cnt / total
            cumulative += ratio
            result.append((name, cnt, ratio, cumulative))
        return result

    # ---------- 共性模式 ----------
    def _detect_patterns(self, defects: List[Defect],
                          metrics: Dict[str, Any]) -> List[Dict[str, str]]:
        patterns = []
        total = metrics["total"]

        # 1. 安全漏洞全靠安全测试发现
        sec = metrics["security_defects"]
        if sec:
            sec_from_test = sum(1 for d in sec if d.discovery_channel == "安全测试")
            if sec_from_test == len(sec):
                patterns.append({
                    "name": "安全漏洞 100% 靠安全测试发现",
                    "evidence": f"{len(sec)} 条安全漏洞全部来自安全测试",
                    "level": "高",
                    "insight": "日常自测对越权/XSS 等安全场景覆盖率为零，建议测试用例库纳入安全场景必检",
                })

        # 2. 严重缺陷占比
        if metrics["severe_ratio"] > 0.2:
            patterns.append({
                "name": "严重缺陷占比偏高",
                "evidence": f"P0/P1/HIGH 占比 {metrics['severe_ratio']*100:.1f}%",
                "level": "高",
                "insight": "整体质量呈下滑趋势，建议加强发布卡点与回归测试",
            })

        # 3. 业务反馈占比过高
        channel = metrics["discovery_channel"]
        biz_feedback = channel.get("业务反馈", 0)
        if total > 0 and biz_feedback / total > 0.3:
            patterns.append({
                "name": "业务反馈漏测占比过高",
                "evidence": f"业务反馈 {biz_feedback} 条，占 {biz_feedback/total*100:.1f}%",
                "level": "中",
                "insight": "发布前测试场景覆盖不足，建议补充端到端测试用例",
            })

        # 4. 单模块缺陷集中
        for m, c in metrics["module"].most_common(1):
            if total > 0 and c / total > 0.5 and m != "UNKNOWN":
                patterns.append({
                    "name": f"模块 [{m}] 为缺陷重灾区",
                    "evidence": f"{c}/{total} ({c/total*100:.1f}%)",
                    "level": "高",
                    "insight": "建议对该模块进行架构评审、代码扫描与专项重构",
                })

        # 5. 离职人员风险
        dev_left_count = len(metrics["dev_left"])
        if dev_left_count > 0:
            unique_dev_left = len(set(
                d.dev_owner for d in metrics["dev_left"]
            ))
            patterns.append({
                "name": "离职人员代码维护风险",
                "evidence": f"{dev_left_count} 条缺陷的开发已离场（{unique_dev_left} 个不同人员）",
                "level": "中",
                "insight": "新缺陷复发时无法溯源原作者，建议优先沉淀知识库",
            })

        # 6. 资损事件预警
        loss = metrics["loss_defects"]
        if loss:
            patterns.append({
                "name": "资损事件预警",
                "evidence": f"{len(loss)} 条有资损（has_loss=true）",
                "level": "高",
                "insight": "需要立即根因复盘与资金对账，避免持续扩大",
            })

        return patterns

    # ---------- 报告渲染 ----------
    def _render_local_report(self, defects: List[Defect],
                              invalid: int,
                              metrics: Dict[str, Any],
                              pareto: List,
                              patterns: List,
                              period: str) -> str:
        md = MarkdownFormatter()
        total = metrics["total"]

        md.title("缺陷汇总与趋势分析报告", 1)

        # === 数据概览 ===
        md.title("1. 数据概览", 2)
        md.kv_pairs({
            "统计周期":   period or "未指定",
            "有效缺陷":   f"{total} 条",
            "无效/空白":  f"{invalid} 条",
            "P0/P1 严重": f"{metrics['severe']} 条（{metrics['severe_ratio']*100:.1f}%）",
            "资损事件":   f"{len(metrics['loss_defects'])} 条",
            "安全漏洞":   f"{len(metrics['security_defects'])} 条",
            "最不稳定模块": metrics["unstable_module"] or "无",
        })

        # === 关键风险卡片 ===
        md.title("2. 关键风险预警", 2)
        if metrics["loss_defects"]:
            md.title("🔴 资损事件", 3)
            for d in metrics["loss_defects"]:
                md.text(
                    f"- **{d.id}** [{d.module}] {self.sanitizer.sanitize(d.defect_name or '')}"
                )
                if d.root_cause:
                    md.text(f"  - 根因: {self.sanitizer.sanitize(d.root_cause)[:100]}")
        else:
            md.text("✅ 本期无资损事件")

        # 安全漏洞详情
        sec = metrics["security_defects"]
        if sec:
            md.title("🔴 安全漏洞清单", 3)
            rows = []
            for d in sec:
                rows.append([
                    d.id,
                    d.problem_type or "-",
                    d.discovery_channel or "-",
                    self.sanitizer.sanitize(d.defect_name or "")[:50],
                ])
            md.table(["缺陷ID", "问题类型", "发现渠道", "缺陷名称"], rows)

        # 离职风险
        dev_left = metrics["dev_left"]
        if dev_left:
            md.title("🟡 离职人员代码风险", 3)
            left_persons = Counter(d.dev_owner for d in dev_left)
            md.text(f"涉及 **{len(dev_left)}** 条缺陷由 **{len(left_persons)}** 名已离场开发人员负责：")
            md.bullet_list([
                f"{name}：{count} 条相关缺陷" for name, count in left_persons.most_common()
            ])

        # === 模块分布 ===
        md.title("3. 模块分布分析", 2)
        rows = []
        for m, c in metrics["module"].most_common(10):
            sev = metrics["module_severity"][m]
            p01h = sum(sev.get(p, 0) for p in ("P0", "P1", "HIGH"))
            rows.append([
                m, c, MarkdownFormatter.percent(c, total),
                sev.get("P0", 0) + sev.get("HIGH", 0),
                sev.get("P1", 0),
                sev.get("P2", 0) + sev.get("MEDIUM", 0),
                sev.get("P3", 0) + sev.get("LOW", 0),
                MarkdownFormatter.percent(p01h, c) if c else "-",
            ])
        md.table(
            ["模块", "缺陷数", "占比", "P0/HIGH", "P1", "P2/MED", "P3/LOW", "严重占比"],
            rows,
        )

        # === 帕累托根因分析 ===
        md.title("4. 根因分析（帕累托法则）", 2)
        if pareto:
            rows = [
                [name, cnt, f"{ratio*100:.1f}%", f"{cum*100:.1f}%"]
                for name, cnt, ratio, cum in pareto[:15]
            ]
            md.table(["问题类型", "缺陷数", "占比", "累计占比"], rows)

            # Top 3
            md.title("4.1 Top 3 根因", 3)
            for name, cnt, ratio, cum in pareto[:3]:
                md.text(f"- **{name}** — {cnt} 条 ({ratio*100:.1f}%)")

            # 80% 阈值
            top_n = sum(1 for _, _, _, cum in pareto if cum <= 0.81)
            if top_n:
                md.text(
                    f"\n**帕累托分析**：Top {top_n} 根因即可覆盖 80% 问题，"
                    f"建议优先治理这些类型。"
                )
        else:
            md.text("_无 problem_type 数据_")

        # === 发现渠道 ===
        md.title("5. 发现渠道分析（测试效能洞察）", 2)
        rows = []
        for ch, c in metrics["discovery_channel"].most_common():
            insight = ""
            if ch == "安全测试":
                insight = "⚠️ 安全场景自测覆盖率不足"
            elif ch == "业务反馈" and total > 0 and c / total > 0.3:
                insight = "⚠️ 发布前漏测严重"
            elif ch == "对账中心":
                insight = "✅ 自动化对账价值显现"
            elif ch == "企微异常监测":
                insight = "💡 监控告警建议扩大覆盖"
            rows.append([ch, c, MarkdownFormatter.percent(c, total), insight])
        md.table(["发现渠道", "缺陷数", "占比", "洞察"], rows)

        # === 责任端 ===
        md.title("6. 责任端分布", 2)
        rows = [[io, c, MarkdownFormatter.percent(c, total)]
                for io, c in metrics["issue_owner"].most_common()]
        md.table(["责任端", "缺陷数", "占比"], rows)

        # === 修复状态 ===
        md.title("7. 修复状态跟踪", 2)
        rows = []
        for status, c in metrics["fix_status"].most_common():
            badge = "✅" if status == "已投产" else ("🔄" if status == "修复中" else "📋")
            rows.append([f"{badge} {status}", c, MarkdownFormatter.percent(c, total)])
        md.table(["修复状态", "缺陷数", "占比"], rows)

        # === 时间趋势 ===
        md.title("8. 时间趋势（按月）", 2)
        if metrics["time_series"]:
            rows = []
            prev = None
            for month, c in metrics["time_series"]:
                delta = ""
                if prev is not None:
                    diff = c - prev
                    if diff > 0:
                        delta = f"↑ +{diff}"
                    elif diff < 0:
                        delta = f"↓ {diff}"
                    else:
                        delta = "→ 0"
                rows.append([month, c, delta])
                prev = c
            md.table(["月份", "缺陷数", "环比变化"], rows)
        else:
            md.text("_无 occurrence_time 数据_")

        # === 系统性风险研判 ===
        md.title("9. 系统性风险研判", 2)
        if not patterns:
            md.text("_未识别到显著的共性模式_")
        else:
            for p in patterns:
                md.text(f"### {p['name']}")
                md.kv_pairs({
                    "证据":     p["evidence"],
                    "风险等级": p["level"],
                    "洞察":     p["insight"],
                })

        return md.render()

    # ---------- LLM 策略 ----------
    def _llm_strategy(self, metrics: Dict[str, Any],
                       pareto: List,
                       patterns: List,
                       period: str) -> Optional[str]:
        if not pareto and not patterns:
            return None

        top_root_causes = ", ".join(
            f"{n}({c}, {r*100:.0f}%)" for n, c, r, _ in pareto[:3]
        )
        patterns_summary = "\n".join(
            f"- {p['name']}: {p['evidence']}" for p in patterns
        )

        prompt = (
            f"统计周期：{period or '未指定'}\n\n"
            f"【关键指标】\n"
            f"- 缺陷总数: {metrics['total']}\n"
            f"- 严重占比: {metrics['severe_ratio']*100:.1f}%\n"
            f"- 最不稳定模块: {metrics['unstable_module']}\n"
            f"- Top 3 根因: {top_root_causes}\n\n"
            f"【已识别的系统性风险】\n{patterns_summary}\n\n"
            f"请基于以上数据，输出：\n"
            f"1. 针对 Top 1 根因的流程改进措施（具体可执行）\n"
            f"2. 短期（1-2 周）/ 中期（1-3 月）/ 长期（3 月+）行动计划\n"
            f"3. 关键建议（最紧急 / 最重要 / 最可行 各 1 条）\n\n"
            f"**注意**：不要重复已经列出的风险洞察，要给出真正可落地的下一步动作。"
        )

        try:
            return self.llm.complete(PromptTemplates.SYSTEM_BASE, prompt)
        except Exception:
            return None
