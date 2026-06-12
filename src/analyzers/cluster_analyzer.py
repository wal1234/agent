"""相似缺陷归纳分析器（P0 增强版）

核心增强：
1. 业务模式识别引擎（基于真实台账已识别的高价值集群）
2. 模块/责任端/根因等多维统计
3. LLM 共性洞察作为可选增强

设计原则：本地模式命中 + LLM 解读相结合，前者保证确定性。
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from ..models import Defect, Priority
from ..utils import LLMClient, Sanitizer, MarkdownFormatter
from ..prompts import PromptTemplates


# ============================================================
# 业务模式字典（基于真实台账归纳）
# ============================================================
# 每个模式包含：name + 关键词列表 + 严重度 + 推荐治理动作
PATTERN_DICT = [
    {
        "id": "copy_new_pattern",
        "name": "复制新增功能技术债",
        "keywords": ["复制新增", "复制新建", "复制"],
        "field_scopes": ["defect_name", "root_cause"],
        "severity": "high",
        "owner_hint": "前端为主",
        "treatment": "将『复制新增』列为前端 Code Review 必查项；补充复制行为自动化用例；统一复制工具函数",
    },
    {
        "id": "concurrency_lock",
        "name": "事务/分布式锁/异步并发缺陷",
        "keywords": ["事务未提交", "feign 调用", "feign调用", "@DistributedLock",
                      "select+update", "锁住", "锁时长", "事务内", "并发情况"],
        "field_scopes": ["defect_name", "root_cause"],
        "severity": "high",
        "owner_hint": "后端",
        "treatment": "制定《事务边界与锁使用规范》；增加并发场景压测；引入静态扫描检测事务内远程调用",
    },
    {
        "id": "security_authz",
        "name": "越权类安全漏洞",
        "keywords": ["权限控制", "越权", "未授权", "权限校验"],
        "field_scopes": ["defect_name", "root_cause", "problem_type"],
        "severity": "critical",
        "owner_hint": "后端",
        "treatment": "所有 query/edit 接口加入 @PermissionCheck 强制注解；日常自测覆盖越权场景；纳入测试用例库",
    },
    {
        "id": "report_export",
        "name": "报表导出脆弱性",
        "keywords": ["报表导出", "导出报错", "枚举异常", "字段错位", "导出文件为空", "导出数据为空"],
        "field_scopes": ["defect_name", "root_cause"],
        "severity": "medium",
        "owner_hint": "后端",
        "treatment": "统一报表导出框架，强制 NPE 守护和枚举兜底；为高频导出接口建立自动化回归",
    },
    {
        "id": "lottery_screen",
        "name": "大屏抽奖系列",
        "keywords": ["大屏抽奖"],
        "field_scopes": ["defect_name"],
        "severity": "medium",
        "owner_hint": "后端+前端",
        "treatment": "对接公共抽奖发奖逻辑前增加抽奖类型兼容性检查；前端开奖按钮做状态机统一管理",
    },
    {
        "id": "points_account",
        "name": "积分账户/积分商城",
        "keywords": ["积分账户", "积分明细", "积分商城", "积分流水", "积分兑换"],
        "field_scopes": ["defect_name"],
        "severity": "high",
        "owner_hint": "后端",
        "treatment": "积分账户与流水强一致性校验前置；积分跑批增加幂等性与回滚测试",
    },
    {
        "id": "non_vendor_fee",
        "name": "非厂商渠道服务费",
        "keywords": ["非厂商", "非厂商渠道", "渠道服务费"],
        "field_scopes": ["defect_name"],
        "severity": "medium",
        "owner_hint": "后端+前端",
        "treatment": "梳理服务费流程的角色/超管/部门关系矩阵；建立多角色场景回归用例",
    },
    {
        "id": "coupon_grant",
        "name": "卡券/权益发放系列",
        "keywords": ["卡券", "发放失败", "权益发放", "权益礼包", "减息券", "膨胀券"],
        "field_scopes": ["defect_name"],
        "severity": "medium",
        "owner_hint": "后端",
        "treatment": "发奖接口统一异常码值规范；区分重复发放与真实失败；建立发奖状态机测试",
    },
    {
        "id": "order_payment",
        "name": "订单/支付/退款状态异常",
        "keywords": ["订单状态", "支付金额为0", "回跳", "回调", "再次退款", "查证"],
        "field_scopes": ["defect_name", "root_cause"],
        "severity": "high",
        "owner_hint": "后端",
        "treatment": "订单状态机做集中管理；支付与退款增加银联返回码值兜底；增强对账中心异常告警",
    },
    {
        "id": "digital_rights",
        "name": "数字权益/直充类",
        "keywords": ["普麦", "飞翰", "悠享", "直充", "数字权益"],
        "field_scopes": ["defect_name", "root_cause"],
        "severity": "medium",
        "owner_hint": "后端",
        "treatment": "第三方权益接入统一适配层；状态查询与回调走分布式锁，避免重复发放",
    },
    {
        "id": "ux_display",
        "name": "前端展示与样式适配",
        "keywords": ["前端展示", "展示错误", "样式优化", "适配机型", "前端未"],
        "field_scopes": ["defect_name", "root_cause", "problem_type"],
        "severity": "low",
        "owner_hint": "前端",
        "treatment": "建立 UI 兼容性测试矩阵；前端在迭代中增加机型回归卡片",
    },
    {
        "id": "null_protection",
        "name": "空指针/未做非空校验",
        "keywords": ["空指针", "未做非空校验", "未对", "未判空", "非空校验"],
        "field_scopes": ["defect_name", "root_cause", "problem_type"],
        "severity": "medium",
        "owner_hint": "后端",
        "treatment": "推广 Optional / @NonNull 编码规范；静态扫描检测高频空指针入口",
    },
]


SEVERITY_ICON = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "⚪",
}


# ============================================================
# 模式匹配引擎
# ============================================================
def _text_for_pattern(defect: Defect, scopes: List[str]) -> str:
    """收集模式匹配范围内的文本"""
    parts = []
    for scope in scopes:
        value = getattr(defect, scope, None)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def detect_patterns(defects: List[Defect]) -> Dict[str, List[Defect]]:
    """对缺陷列表执行模式识别

    Returns:
        {pattern_id: [缺陷列表], ...}
    """
    hits: Dict[str, List[Defect]] = defaultdict(list)
    for d in defects:
        if not d.is_valid:
            continue
        for pattern in PATTERN_DICT:
            text = _text_for_pattern(d, pattern["field_scopes"])
            if not text:
                continue
            if any(kw in text for kw in pattern["keywords"]):
                hits[pattern["id"]].append(d)
    return hits


# ============================================================
# 主分析器
# ============================================================
class ClusterAnalyzer:
    """缺陷聚类分析器"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer):
        self.llm = llm
        self.sanitizer = sanitizer

    def analyze(self, defects: List[Defect]) -> str:
        valid_defects = [d for d in defects if d.is_valid]
        if not valid_defects:
            return "## 缺陷聚类分析报告\n\n暂无有效缺陷数据。"

        # 1. 业务模式识别
        pattern_hits = detect_patterns(valid_defects)

        # 2. 通用维度统计
        stats = self._compute_stats(valid_defects)

        # 3. 渲染本地报告
        local_report = self._render_local_report(valid_defects, pattern_hits, stats)

        # 4. LLM 共性洞察（可选）
        llm_insight = self._llm_insight(pattern_hits)
        if llm_insight:
            local_report += "\n\n---\n\n## LLM 共性洞察\n\n" + llm_insight

        return local_report

    # ---------- 统计 ----------
    def _compute_stats(self, defects: List[Defect]) -> Dict[str, Any]:
        total = len(defects)

        # problem_type 拆词
        pt_counter: Counter = Counter()
        for d in defects:
            for pt in d.problem_type_list():
                pt_counter[pt] += 1

        # issue_owner 拆词
        io_counter: Counter = Counter()
        for d in defects:
            for owner in d.issue_owner_list():
                io_counter[owner] += 1

        # 简单分布
        module_counter = Counter(d.module or "UNKNOWN" for d in defects)
        priority_counter = Counter(d.priority.value for d in defects)
        channel_counter = Counter(d.discovery_channel or "未指定" for d in defects)
        dev_counter = Counter(d.dev_owner or "未指派" for d in defects)
        test_counter = Counter(d.test_owner or "未指派" for d in defects)

        return {
            "total": total,
            "problem_type": pt_counter,
            "issue_owner": io_counter,
            "module": module_counter,
            "priority": priority_counter,
            "discovery_channel": channel_counter,
            "dev_owner": dev_counter,
            "test_owner": test_counter,
        }

    # ---------- 报告渲染 ----------
    def _render_local_report(self, defects: List[Defect],
                              pattern_hits: Dict[str, List[Defect]],
                              stats: Dict[str, Any]) -> str:
        md = MarkdownFormatter()
        md.title("缺陷聚类分析报告", 1)
        total = stats["total"]

        # === 业务模式识别（核心）===
        md.title("1. 业务模式识别（高价值集群）", 2)
        md.text("基于真实台账归纳的业务关键词字典命中结果，按严重度排序：")

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_patterns = sorted(
            [p for p in PATTERN_DICT if pattern_hits.get(p["id"])],
            key=lambda p: (severity_order.get(p["severity"], 9), -len(pattern_hits[p["id"]])),
        )

        if not sorted_patterns:
            md.text("_未识别到任何业务模式集群。_")
        else:
            for p in sorted_patterns:
                hits = pattern_hits[p["id"]]
                icon = SEVERITY_ICON[p["severity"]]
                md.title(f"{icon} 集群 #{p['id']}：{p['name']}（{len(hits)} 条）", 3)

                md.kv_pairs({
                    "严重度":   f"{icon} {p['severity']}",
                    "责任端":   p["owner_hint"],
                    "命中关键词": "、".join(p["keywords"]),
                })

                md.text("**典型缺陷：**")
                rows = []
                for d in hits[:8]:
                    rows.append([
                        d.id,
                        d.module or "-",
                        d.priority.value,
                        self.sanitizer.sanitize(d.defect_name or "")[:50],
                    ])
                md.table(["缺陷ID", "模块", "优先级", "缺陷名称"], rows)
                if len(hits) > 8:
                    md.text(f"_（仅展示前 8 条，共 {len(hits)} 条）_")

                md.text(f"**建议治理动作：** {p['treatment']}")
                md.blank()

        # === 模式覆盖统计 ===
        md.title("2. 模式覆盖统计", 2)
        covered = set()
        for ids in pattern_hits.values():
            covered.update(d.id for d in ids)
        coverage = len(covered) / total if total else 0
        md.kv_pairs({
            "覆盖缺陷数":  f"{len(covered)} / {total}",
            "模式覆盖率":  f"{coverage*100:.1f}%",
            "命中模式数":  len(sorted_patterns),
        })
        if coverage < 0.5:
            md.text("⚠️ **模式覆盖率较低**，建议补充关键词字典或人工 review 未覆盖缺陷")

        # === 通用维度聚类 ===
        md.title("3. 通用维度统计", 2)

        md.title("3.1 按模块分布", 3)
        rows = [[m, c, MarkdownFormatter.percent(c, total)]
                for m, c in stats["module"].most_common()]
        md.table(["模块", "缺陷数", "占比"], rows)

        md.title("3.2 按 problem_type 分布（已拆词）", 3)
        rows = [[pt, c, MarkdownFormatter.percent(c, total)]
                for pt, c in stats["problem_type"].most_common(15)]
        md.table(["问题类型", "缺陷数", "占比"], rows)

        md.title("3.3 按发现渠道分布", 3)
        rows = []
        for ch, c in stats["discovery_channel"].most_common():
            insight = ""
            if ch == "安全测试":
                insight = "⚠️ 安全漏洞均靠安全测试发现，自测零覆盖"
            elif ch == "业务反馈":
                if c / total > 0.3:
                    insight = "⚠️ 业务反馈占比偏高，发布前漏测较严重"
            elif ch == "对账中心":
                insight = "✅ 自动化对账价值显现"
            rows.append([ch, c, MarkdownFormatter.percent(c, total), insight])
        md.table(["发现渠道", "缺陷数", "占比", "洞察"], rows)

        md.title("3.4 按责任端分布（已拆词）", 3)
        rows = [[io, c, MarkdownFormatter.percent(c, total)]
                for io, c in stats["issue_owner"].most_common()]
        md.table(["责任端", "缺陷数", "占比"], rows)

        return md.render()

    # ---------- LLM 共性洞察 ----------
    def _llm_insight(self, pattern_hits: Dict[str, List[Defect]]) -> Optional[str]:
        """让 LLM 在已识别的集群基础上做共性洞察"""
        if not pattern_hits:
            return None

        # 构造简化输入：每个集群一条摘要
        lines = []
        for p in PATTERN_DICT:
            hits = pattern_hits.get(p["id"], [])
            if not hits:
                continue
            sample_names = "; ".join(
                self.sanitizer.sanitize(d.defect_name or "")[:40]
                for d in hits[:3]
            )
            lines.append(
                f"- **{p['name']}**（{len(hits)} 条）："
                f"示例 [{sample_names}]"
            )
        if not lines:
            return None

        prompt = (
            "以下是本期已识别的缺陷集群，请基于这些集群做两件事：\n"
            "1. 从更高维度找出 2-3 个跨集群的共性问题（如：是否反映组织能力缺口/流程缺失/技术栈短板）\n"
            "2. 给出 1-2 条最优先级的改进建议\n\n"
            "**注意：不要重复已经列出的治理动作，要做更高层的归纳。**\n\n"
            "已识别集群：\n" + "\n".join(lines)
        )

        try:
            return self.llm.complete(PromptTemplates.SYSTEM_BASE, prompt)
        except Exception as e:
            return None
