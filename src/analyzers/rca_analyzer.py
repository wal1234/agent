"""RCA 根因分析器（P1 增强版）

核心增强：
1. 团队术语词典注入 — 从台账真实 root_cause 中提炼的"渠道营销团队惯用根因表达模式"
2. Few-shot 样本 — 用已存在的 root_cause 引导 LLM 格式
3. 降级时不再只输出骨架，而是输出本地 5Why 框架
"""

import re
from typing import Optional

from ..models import Defect
from ..utils import LLMClient, Sanitizer, MarkdownFormatter
from ..prompts import PromptTemplates


# ============================================================
# 团队术语词典（从台账真实 root_cause 提炼）
# ============================================================
TEAM_TERMS = {
    "patterns": [
        ("判空缺失",      "未做|<字段>未非空|未判空"),
        ("枚举类型不全",   "枚举转换|枚举异常|枚举中不存在"),
        ("事务未提交",     "事务未提交|事务内feign|feign调用结束回来"),
        ("锁时长不足",     "@DistributedLock|锁住|锁时间|锁时长"),
        ("复制未清空",     "复制.*未|未赋值新的|未清空"),
        ("前端未上送",     "前端未上送|前端的值未|前端校验"),
        ("select+update 非原子",  "先查.*再更新|并发情况.*少更新"),
        ("公用逻辑未兼容", "公用.*逻辑|抽奖组件.*剔除|未考虑.*特殊情况"),
        ("对接/迁移遗漏",  "迁移.*代码|对接.*未|未兼容"),
    ],
}

TEAM_RC_STYLES = [
    # 模板 1：<操作>时，<子操作>未做<处理>，导致<结果>
    "保存草稿时未校验积分奖励的积分数是否非空，导致积分奖励中积分数为空且不可编辑",
    "批量发放失败时会记录发放失败记录，流水号重复，入库时唯一键重复，抛出异常未记录卡券记录",
    # 模板 2：前端...未做...，导致...
    "前端用于返现用户参与活动结果的字段与后端返回结果字段不一致，导致提示异常请重试",
    "前端进行开奖按钮展示时没有对即将结束状态进行展示逻辑的判断，导致不展示开奖按钮",
    # 模板 3：<操作>采用<公用逻辑>，<特殊情况>未考虑
    "大屏抽奖采用公用发奖逻辑，公用发奖逻辑扣除预算和库存时会剔除抽奖组件类型，导致大屏抽奖预算扣除异常",
    # 模板 4：事务内 feign / 锁不够
    "事务内feign调用，feign调用结束回来更新事务内的数据，但事务未提交",
    "跑批查询支付结果和用户进入订单列表查询支付结果未加锁，同时查询结果未支付成功并调用发奖，right判断为重复发奖",
]


def _build_rca_system_prompt(raw_log: str) -> str:
    """构造带有团队术语的 RCA 系统提示"""
    pattern_list = "\n".join(f"- 「{name}」: 匹配关键词 {kw}"
                              for name, kw in TEAM_TERMS["patterns"])
    style_list = "\n".join(f"  「{s}」" for s in TEAM_RC_STYLES[:5])

    return (
        "你是一位熟悉渠道营销中台业务的技术专家，正在做缺陷 RCA 根因分析。\n\n"
        "### 团队根因术语（请优先使用这些表达方式）\n"
        f"{pattern_list}\n\n"
        "### 团队惯用的根因表达风格（模仿下面句式）\n"
        f"{style_list}\n\n"
        "### 根因分析要求\n"
        "- 使用 5Why 法逐层追溯：现象 → 直接技术原因 → 代码问题 → 设计/实现缺陷 → 根本原因\n"
        "- 每个 Why 要有明确的因果关系，不能跳跃\n"
        "- 根因最终应落在『代码未做XX』、『需求未覆盖XX』、『架构未兼容XX』等可操作的级别\n"
        "- 给出**临时止血方案**（立即可执行的回滚/降级/限流等）\n"
        "- 给出**永久修复方案**（代码改动 + 测试补充 + 流程改进）\n"
    )


# ============================================================
# 主分析器
# ============================================================
class RCAAnalyzer:
    """根因分析器"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer):
        self.llm = llm
        self.sanitizer = sanitizer

    @staticmethod
    def extract_error_type(log: str) -> str:
        if not log:
            return ""
        # Java exception
        m = re.search(r"([A-Z][A-Za-z0-9_$.]*Exception|[A-Z][A-Za-z0-9_$.]*Error)", log)
        if m:
            return m.group(1)
        # HTTP status
        m = re.search(r"\b(5\d{2}|4\d{2})\b", log)
        if m:
            return f"HTTP {m.group(1)}"
        return "未识别"

    @staticmethod
    def extract_module(log: str) -> str:
        m = re.search(r"(?:at\s+)?([\w.]+?)(?:Service|Controller|Manager)", log)
        if m:
            return m.group(1).split(".")[-1]
        return ""

    def analyze(self, defect: Optional[Defect] = None,
                raw_log: str = "") -> str:
        """执行 RCA 分析

        Args:
            defect: 台账缺陷对象（有 root_cause / defect_name 等字段）
            raw_log: 原始日志 / 堆栈文本（用于技术分析）
        """
        log_text = raw_log or ""
        clean_log = self.sanitizer.sanitize(log_text)
        error_type = self.extract_error_type(clean_log)
        module = (defect.module if defect else "") or self.extract_module(clean_log)

        defect_info = self._build_defect_info(defect, error_type, module)
        system_prompt = _build_rca_system_prompt(clean_log)

        prompt = PromptTemplates.RCA_USER.format(
            defect_info=defect_info,
            raw_log=clean_log or "（未提供日志）",
        )
        try:
            return self.llm.complete(system_prompt, prompt)
        except Exception as e:
            return self._fallback(defect, error_type, module, clean_log, str(e))

    def _build_defect_info(self, defect: Optional[Defect],
                            error_type: str, module: str) -> str:
        if defect and defect.is_valid:
            info = {
                "ID":            defect.id,
                "缺陷名称":      self.sanitizer.sanitize(defect.defect_name or ""),
                "模块":          defect.module,
                "优先级":        defect.priority.value,
                "问题类型":      defect.problem_type or "未指定",
                "发现渠道":      defect.discovery_channel or "未记录",
                "根因（已有的）": self.sanitizer.sanitize(defect.root_cause or "无负责人填写，需要分析"),
            }
            if defect.scope:
                info["影响范围"] = defect.scope
            if defect.fix_status:
                info["修复状态"] = defect.fix_status
        else:
            info = {
                "错误类型":      error_type or "未识别",
                "涉及模块":      module or "未识别",
            }
        return "\n".join(f"- **{k}**: {v}" for k, v in info.items())

    def _fallback(self, defect: Optional[Defect], error_type: str,
                   module: str, log: str, err: str) -> str:
        """降级模式：输出本地 5Why 框架（不依赖 LLM）"""
        md = MarkdownFormatter()
        md.title("缺陷分析报告（无 LLM 降级版）", 2)

        md.title("问题摘要", 3)
        md.kv_pairs({
            "错误类型":  error_type or "未识别",
            "涉及模块":  module or "未识别",
            "影响模块":  defect.module if defect and defect.is_valid else "未知",
        })

        if defect and defect.is_valid:
            md.title("已知信息", 3)
            md.kv_pairs({
                "缺陷": f"{defect.id} - {self.sanitizer.sanitize(defect.defect_name or '')}",
                "已有根因": self.sanitizer.sanitize(defect.root_cause or "无")[:200],
            })

        md.title("5Why 分析框架（人工补充）", 3)
        md.text("因 LLM 未配置，请技术负责人按以下框架分析：")
        md.table(
            ["Layer", "自问", "回答"],
            [
                ["1", "为什么出现此错误？",        "(填写直接错误现象)"],
                ["2", "为什么会出现这个直接原因？", "(填写技术原因)"],
                ["3", "为什么这个技术原因未被预防？", "(填写代码/设计缺陷)"],
                ["4", "为什么这个设计缺陷存在？",    "(填写流程/规范缺失)"],
                ["5", "根本原因",          "(填写最根本的可操作改进项)"],
            ],
        )

        md.title("原始输入（已脱敏）", 3)
        md.code(log[:1000] if log else "（无）", "text")

        md.title("说明", 3)
        md.text(f"LLM 调用失败：{err}\n设置 LLM API Key 后可获得自动 RCA 分析。")

        return md.render()