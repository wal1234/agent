"""专项治理报告生成器（P1）

针对 ClusterAnalyzer 识别的高价值业务模式集群，生成可落地的"专项治理报告"。

每份报告包含：
1. 集群所有缺陷清单（统计 + 详情）
2. 共性根因总结
3. 推荐修复模式（含代码示例 + 反例）
4. 规范文档建议
5. 自动化测试用例补充清单
6. 后续监控指标
7. 行动计划（短/中/长期）

使用 LLM 做深度治理建议；无 LLM 时输出基于内置治理模板的本地报告。
"""

from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional

from ..models import Defect
from ..utils import LLMClient, Sanitizer, MarkdownFormatter
from ..prompts import PromptTemplates
from .cluster_analyzer import PATTERN_DICT, detect_patterns


# ============================================================
# 内置治理模板（针对真实台账已识别的高价值集群）
# ============================================================
GOVERNANCE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    # -------- 复制新增功能技术债 --------
    "copy_new_pattern": {
        "title": "复制新增功能专项治理方案",
        "summary": "复制新增类缺陷反复出现，本质是缺乏统一的复制工具与校验机制。",
        "root_pattern": [
            "前端复制时未清空原数据（如奖品数量、提醒消息）",
            "复制时未对画布/组件做重新校验",
            "复制链断裂（活动A复制B，再复制C，编辑A会误删B/C的组件）",
            "复制反显字段未赋值（如膨胀券、提醒消息）",
        ],
        "fix_pattern": {
            "anti": (
                "// ❌ 反例：直接 deepClone 后修改 id\n"
                "const newDraft = JSON.parse(JSON.stringify(source));\n"
                "newDraft.id = generateId();\n"
                "saveDraft(newDraft);  // 复制了源对象的 ID 引用、关联组件 ID"
            ),
            "good": (
                "// ✅ 推荐：使用统一的 cloneActivity 工具\n"
                "import { cloneActivity } from '@/utils/activity';\n"
                "const draft = cloneActivity(source, {\n"
                "  resetFields: ['awardQuantity', 'expireMessage'],\n"
                "  regenerateIds: ['components.*.id'],\n"
                "  validateBeforeSave: true,  // 强制重新校验奖品数量等\n"
                "});"
            ),
        },
        "spec_actions": [
            "新建《活动复制工具规范.md》：统一 cloneActivity 工具函数",
            "前端 ESLint 规则禁用 deepClone 后直接保存的写法",
            "复制后必须触发同 saveDraft 校验链路（不允许跳过）",
        ],
        "test_cases": [
            "测试用例：复制活动后，奖品数量字段必须重置为 0 或要求重填",
            "测试用例：A→B→C 复制链，编辑 A 后 B/C 组件应独立保留",
            "测试用例：复制含膨胀券/减息券的活动，反显字段不丢失",
            "测试用例：所有活动类型的复制都触发 schema 校验，缺字段时阻止保存",
        ],
        "monitor_metrics": [
            "前端埋点：cloneActivity 调用次数 / 失败次数",
            "后管端：复制活动后 24h 内的修改/删除事件计数",
        ],
        "owner_hint": "前端 Lead + 测试 Lead",
    },

    # -------- 事务/分布式锁/并发 --------
    "concurrency_lock": {
        "title": "事务与分布式锁专项治理方案",
        "summary": "事务边界混乱 + 锁时长不足 = 资金类高危并发缺陷。",
        "root_pattern": [
            "事务内 feign 远程调用：feign 返回时事务未提交，导致数据不一致",
            "@DistributedLock 默认 5s，实际回调处理 25s 锁失效",
            "select + update 非原子：并发情况下少更新",
            "feign 调用线程池不足导致更新丢失",
            "支付/发奖未加锁，重复调用 right 接口被判重",
        ],
        "fix_pattern": {
            "anti": (
                "// ❌ 反例：事务内 feign 调用\n"
                "@Transactional\n"
                "public void process(Order order) {\n"
                "    Result r = paymentFeign.pay(order);  // 远程调用\n"
                "    order.setStatus(r.getStatus());\n"
                "    orderMapper.update(order);  // 事务尚未提交，外部已感知不到\n"
                "}"
            ),
            "good": (
                "// ✅ 推荐：远程调用置于事务外 + 显式锁时长\n"
                "Result r = paymentFeign.pay(order);  // 远程调用先做\n"
                "\n"
                "@DistributedLock(key=\"#order.id\", leaseTime=30, unit=SECONDS)\n"
                "@Transactional(rollbackFor = Exception.class)\n"
                "public void updateOrder(Order order, Result r) {\n"
                "    order.setStatus(r.getStatus());\n"
                "    orderMapper.update(order);\n"
                "}\n"
                "\n"
                "// ✅ select+update 改为原子 SQL 或乐观锁\n"
                "UPDATE proposal SET written_amount = written_amount + #{delta},\n"
                "                   version = version + 1\n"
                "WHERE id = #{id} AND version = #{version}"
            ),
        },
        "spec_actions": [
            "制定《事务边界与远程调用规范.md》：禁止事务内 feign 调用",
            "@DistributedLock 默认 leaseTime 改为 30s，关键场景显式指定",
            "对账类 update 一律改为原子 SQL（INCR/DECR 或乐观锁）",
            "增加 ArchUnit / SonarQube 静态规则检测事务内远程调用",
        ],
        "test_cases": [
            "并发压测：500 QPS 同时核销同张卡券，核销总额必须等于核销次数",
            "压测：支付回调超时 30s，分布式锁不应失效",
            "压测：feign 线程池打满时，业务应熔断而非丢失更新",
            "故障注入：模拟 feign 调用超时，订单状态最终一致性验证",
        ],
        "monitor_metrics": [
            "Prometheus：@DistributedLock 持锁时长 P99",
            "对账中心：每日新增对账差异条数 → 应趋近于零",
            "应用层：feign 调用平均 RT / 线程池占用率",
        ],
        "owner_hint": "后端架构组 + DBA + 运维",
    },

    # -------- 越权安全漏洞 --------
    "security_authz": {
        "title": "越权类安全漏洞专项治理方案",
        "summary": "所有越权漏洞均由外部安全测试发现，日常自测零覆盖，需建立强制权限校验机制。",
        "root_pattern": [
            "查询/编辑接口未校验请求资源归属（如直接信任请求中的手机号/订单号）",
            "请求头删减后绕过权限校验（M0/M1 查询）",
            "公共上传接口被移动端复用，未做文件类型安全处理（导致 XSS）",
            "公共接口未做超管/普通用户区分",
        ],
        "fix_pattern": {
            "anti": (
                "// ❌ 反例：直接信任请求中的手机号\n"
                "@PostMapping(\"/queryPoints\")\n"
                "public List<Points> query(@RequestBody Req req) {\n"
                "    return pointsService.query(req.getPhone());\n"
                "    // 攻击者删除 phone 字段就能查全量\n"
                "}"
            ),
            "good": (
                "// ✅ 推荐：强制从认证上下文取用户身份，并校验资源归属\n"
                "@PostMapping(\"/queryPoints\")\n"
                "@PermissionCheck(scope = SELF_ONLY)\n"
                "public List<Points> query(@RequestBody Req req) {\n"
                "    Long currentUserId = SecurityContext.getCurrentUserId();\n"
                "    // 请求参数中的 phone/userId 必须等于当前用户\n"
                "    Asserts.equals(req.getUserId(), currentUserId, \"越权\");\n"
                "    return pointsService.query(currentUserId);\n"
                "}"
            ),
        },
        "spec_actions": [
            "制定《接口权限校验规范.md》：所有 query/edit 接口必须有 @PermissionCheck 或 SecurityContext 校验",
            "建立越权场景测试用例库（替换/删除身份字段、跨用户访问、批量遍历）",
            "上传接口拆分：后管使用 /ftpUpload，移动端使用 /clientUpload（白名单更严）",
            "安全门户：所有新增接口须经过自动化越权扫描后方可发布",
        ],
        "test_cases": [
            "测试用例：用户 A 登录后查询用户 B 的订单 → 必须 403",
            "测试用例：删除请求 header 中的 token → 必须 401，不能放过",
            "测试用例：批量改变请求中的 phone/userId 字段，必须始终拦截",
            "测试用例：上传 .pdf / .html 文件 → 移动端接口必须拒绝",
            "回归测试：使用 OWASP ZAP / Burp 做自动化越权扫描",
        ],
        "monitor_metrics": [
            "安全网关：@PermissionCheck 注解覆盖率 → 目标 100%",
            "WAF：越权拦截事件趋势",
            "新增接口安全扫描通过率",
        ],
        "owner_hint": "后端 Lead + 安全测试团队",
    },
}


SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}


# ============================================================
# 主分析器
# ============================================================
class GovernanceAnalyzer:
    """专项治理报告生成器"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer):
        self.llm = llm
        self.sanitizer = sanitizer

    # ---------- 主入口 ----------
    def analyze(self, defects: List[Defect],
                pattern_id: Optional[str] = None) -> str:
        """生成专项治理报告

        Args:
            defects: 缺陷列表
            pattern_id: 指定治理模式 ID（如 'copy_new_pattern'）。
                        不指定则生成所有命中模板的报告
        """
        valid_defects = [d for d in defects if d.is_valid]
        if not valid_defects:
            return "## 专项治理报告\n\n暂无有效缺陷数据。"

        # 1. 识别命中模式
        pattern_hits = detect_patterns(valid_defects)

        # 2. 选择要生成的治理模板
        if pattern_id:
            if pattern_id not in GOVERNANCE_TEMPLATES:
                return f"## 错误\n\n未知治理模式 ID: `{pattern_id}`\n\n可用模板: {list(GOVERNANCE_TEMPLATES.keys())}"
            target_ids = [pattern_id]
        else:
            # 只为有内置模板且本期有命中的模式生成
            target_ids = [
                pid for pid in GOVERNANCE_TEMPLATES.keys()
                if pattern_hits.get(pid)
            ]
            if not target_ids:
                return self._render_no_targets(pattern_hits)

        # 3. 逐个模式生成
        md = MarkdownFormatter()
        md.title("专项治理报告", 1)
        md.text(
            f"基于本期 {len(valid_defects)} 条有效缺陷，识别出 "
            f"**{len(target_ids)}** 个需要专项治理的高价值集群。"
        )

        toc = [f"- [{GOVERNANCE_TEMPLATES[pid]['title']}](#{pid})"
               for pid in target_ids]
        md.title("目录", 2)
        md.bullet_list(toc)

        for pid in target_ids:
            hits = pattern_hits.get(pid, [])
            section = self._render_one_governance(pid, hits)
            md.text(f"\n---\n\n<a id=\"{pid}\"></a>")
            md.text(section)

        return md.render()

    # ---------- 单个治理报告 ----------
    def _render_one_governance(self, pattern_id: str,
                                hits: List[Defect]) -> str:
        tpl = GOVERNANCE_TEMPLATES[pattern_id]
        pattern_meta = next(p for p in PATTERN_DICT if p["id"] == pattern_id)

        md = MarkdownFormatter()
        md.title(tpl["title"], 2)
        md.text(f"**集群摘要**：{tpl['summary']}")

        # === 1. 集群统计 ===
        md.title("一、集群统计", 3)
        modules = Counter(d.module for d in hits)
        priorities = Counter(d.priority.value for d in hits)
        channels = Counter(d.discovery_channel or "未指定" for d in hits)

        md.kv_pairs({
            "缺陷总数":     len(hits),
            "涉及模块":     "、".join(f"{m}({c})" for m, c in modules.most_common()),
            "优先级分布":    "、".join(f"{p}({c})" for p, c in priorities.most_common()),
            "主要发现渠道": ", ".join(f"{c}({n})" for c, n in channels.most_common(3)),
            "建议负责人":   tpl["owner_hint"],
        })

        # === 2. 缺陷清单 ===
        md.title("二、缺陷清单", 3)
        rows = []
        for d in hits:
            rows.append([
                d.id,
                d.priority.value,
                d.module,
                self.sanitizer.sanitize(d.defect_name or "")[:60],
                d.fix_status or "-",
            ])
        md.table(["缺陷ID", "优先级", "模块", "缺陷名称", "修复状态"], rows)

        # === 3. 共性根因 ===
        md.title("三、共性根因总结", 3)
        md.bullet_list(tpl["root_pattern"])

        # === 4. 修复模式 ===
        md.title("四、推荐修复模式", 3)
        md.text("**反例（应避免）**：")
        md.code(tpl["fix_pattern"]["anti"], "java")
        md.text("**正例（推荐）**：")
        md.code(tpl["fix_pattern"]["good"], "java")

        # === 5. 规范动作 ===
        md.title("五、规范与流程改进", 3)
        md.bullet_list(tpl["spec_actions"])

        # === 6. 自动化测试用例补充 ===
        md.title("六、自动化测试用例补充清单", 3)
        md.checklist(tpl["test_cases"])

        # === 7. 监控指标 ===
        md.title("七、后续监控指标", 3)
        md.bullet_list(tpl["monitor_metrics"])

        # === 8. 行动计划 ===
        md.title("八、行动计划", 3)
        md.text("**短期（1-2 周）**")
        md.checklist([
            f"修复本期 {len(hits)} 条缺陷中状态仍为'修复中/待优化'的项",
            "落地规范第一项（最容易上手的那条）",
            "测试用例库补入前 3 条用例",
        ])
        md.text("**中期（1-3 个月）**")
        md.checklist([
            "推动所有规范动作落地",
            "完成全部自动化测试用例补充",
            "上线监控指标看板",
        ])
        md.text("**长期（3 个月以上）**")
        md.checklist([
            "将该集群相关缺陷计数纳入团队 KPI",
            "回归追踪：本集群新缺陷月环比下降 30%+",
        ])

        # === 9. LLM 增强建议（可选）===
        llm_extra = self._llm_enhancement(pattern_id, hits)
        if llm_extra:
            md.title("九、LLM 深度建议", 3)
            md.text(llm_extra)

        return md.render()

    # ---------- 无命中场景 ----------
    def _render_no_targets(self, pattern_hits: Dict[str, List[Defect]]) -> str:
        md = MarkdownFormatter()
        md.title("专项治理报告", 1)
        md.text("✅ 本期未命中任何内置治理模板的高价值集群。")
        md.text("内置模板支持以下高价值集群：")
        md.bullet_list([
            f"`{pid}` - {tpl['title']}"
            for pid, tpl in GOVERNANCE_TEMPLATES.items()
        ])
        if pattern_hits:
            md.text("当前已识别集群（无内置治理模板）：")
            md.bullet_list([
                f"{pid}: {len(hits)} 条"
                for pid, hits in pattern_hits.items()
            ])
        return md.render()

    # ---------- LLM 增强 ----------
    def _llm_enhancement(self, pattern_id: str,
                          hits: List[Defect]) -> Optional[str]:
        """让 LLM 基于具体缺陷案例做补充建议"""
        if not hits:
            return None
        tpl = GOVERNANCE_TEMPLATES[pattern_id]

        # 提取最具代表性的 3 条缺陷的根因
        samples = []
        for d in hits[:3]:
            if d.root_cause:
                samples.append(
                    f"- **{d.id}**: {self.sanitizer.sanitize(d.defect_name or '')[:50]}\n"
                    f"  根因: {self.sanitizer.sanitize(d.root_cause)[:200]}"
                )
        if not samples:
            return None

        prompt = (
            f"以下是 [{tpl['title']}] 集群的 {len(hits)} 条缺陷中最有代表性的几条：\n\n"
            + "\n".join(samples)
            + f"\n\n现有治理方案已涵盖：\n"
            f"- 共性根因：{'; '.join(tpl['root_pattern'][:3])}\n"
            f"- 规范动作：{'; '.join(tpl['spec_actions'][:3])}\n\n"
            "请基于这些**真实缺陷案例**，给出 2-3 条**超出现有方案**的"
            "补充建议。要求：\n"
            "1. 必须针对这些具体根因，不要泛泛而谈\n"
            "2. 给出可操作的下一步行动\n"
            "3. 不要重复已有的规范动作"
        )

        try:
            system = (
                "你是渠道营销中台的技术架构师，正在为质量治理给出实操建议。"
                "回答简洁有力，使用 Markdown 列表。"
            )
            return self.llm.complete(system, prompt)
        except Exception:
            return None
