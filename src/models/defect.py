"""缺陷数据模型（对齐真实台账字段）

字段命名直接采用台账原始字段名（中文业务语义保持，命名遵循 Python snake_case），
避免在加载器中做语义错位映射。
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any


class Priority(str, Enum):
    """缺陷优先级（与台账一致，含 UNKNOWN）"""
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    HIGH = "HIGH"        # 台账中部分行用 HIGH/MEDIUM/LOW 替代 P0~P3
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"

    @property
    def is_severe(self) -> bool:
        return self in (Priority.P0, Priority.P1, Priority.HIGH)


class DefectType(str, Enum):
    """缺陷类型（来自台账 defect_type 字段）"""
    CODE_ISSUE = "CODE_ISSUE"
    DESIGN_ISSUE = "DESIGN_ISSUE"
    REQUIREMENT_ISSUE = "REQUIREMENT_ISSUE"
    UNKNOWN = "UNKNOWN"


# ============== 业务字典 ==============
KNOWN_MODULES = {
    "科技缺陷", "科技优化", "业务缺陷", "业务优化", "安全漏洞", "UNKNOWN"
}

KNOWN_DISCOVERY_CHANNELS = {
    "业务反馈", "测试发现", "安全测试", "对账中心", "企微异常监测",
}

KNOWN_FIX_STATUSES = {
    "已投产", "修复中", "确认上线", "待优化", "待修复",
}

KNOWN_ISSUE_OWNERS = {
    "前端", "后端", "前端,后端", "后端,前端", "待定",
}


@dataclass
class Defect:
    """缺陷实体 - 字段完全对齐 2025 渠道营销中台运维台账"""

    # ===== 基本信息 =====
    id: str
    original_index: Optional[int] = None
    occurrence_time: Optional[str] = None
    task_id: Optional[str] = None
    defect_name: Optional[str] = None

    # ===== 分类 =====
    module: str = "UNKNOWN"
    has_loss: Optional[bool] = None
    defect_type: DefectType = DefectType.UNKNOWN
    problem_type: Optional[str] = None
    discovery_channel: Optional[str] = None
    priority: Priority = Priority.UNKNOWN

    # ===== 根因与修复 =====
    root_cause: Optional[str] = None
    fix_method: Optional[str] = None
    fix_status: Optional[str] = None
    fixer: Optional[str] = None
    tester: Optional[str] = None
    planned_release_time: Optional[str] = None
    scope: Optional[str] = None

    # ===== 责任 =====
    issue_owner: Optional[str] = None
    responsibility_ratio: Optional[str] = None
    dev_owner: Optional[str] = None
    test_owner: Optional[str] = None
    responsibility: Optional[str] = None  # 历史字段，与 responsibility_ratio 语义重叠

    # ===== 关联需求 =====
    story_id: Optional[str] = None
    story_name: Optional[str] = None
    story_release_time: Optional[str] = None

    # ===== 测试分析 =====
    test_cause_analysis: Optional[str] = None
    is_automated: Optional[bool] = None
    automation_analysis: Optional[str] = None

    # ===== 来源 =====
    source: Optional[str] = None

    # ===== 扩展字段 =====
    extra: Dict[str, Any] = field(default_factory=dict)

    # ---------- 便捷属性 ----------
    @property
    def is_severe(self) -> bool:
        """是否为严重缺陷（P0/P1/HIGH）"""
        return self.priority.is_severe

    @property
    def is_valid(self) -> bool:
        """是否为有效记录（至少有缺陷名）"""
        return bool(self.defect_name and self.defect_name.strip())

    @property
    def is_security(self) -> bool:
        """是否为安全漏洞"""
        return self.module == "安全漏洞"

    def issue_owner_list(self) -> List[str]:
        """拆分 issue_owner（'前端,后端' → ['前端', '后端']）"""
        if not self.issue_owner:
            return []
        return [s.strip() for s in self.issue_owner.replace("，", ",").split(",") if s.strip()]

    def problem_type_list(self) -> List[str]:
        """拆分 problem_type（'默认值设置错误,前端展示' → 2 个）"""
        if not self.problem_type:
            return []
        return [s.strip() for s in self.problem_type.replace("，", ",").split(",") if s.strip()]

    def dev_has_left(self) -> bool:
        """开发是否已离场"""
        return bool(self.dev_owner and "离场" in self.dev_owner)

    def test_has_left(self) -> bool:
        """测试是否已离场"""
        return bool(self.test_owner and "离场" in self.test_owner)

    # ---------- 序列化 ----------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 枚举转字符串
        d["priority"] = self.priority.value
        d["defect_type"] = self.defect_type.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Defect":
        # 复制以免污染调用方
        data = dict(data)
        # 枚举反序列化
        if "priority" in data and isinstance(data["priority"], str):
            try:
                data["priority"] = Priority(data["priority"])
            except ValueError:
                data["priority"] = Priority.UNKNOWN
        if "defect_type" in data and isinstance(data["defect_type"], str):
            try:
                data["defect_type"] = DefectType(data["defect_type"])
            except ValueError:
                data["defect_type"] = DefectType.UNKNOWN

        # 过滤未知字段进 extra
        known_fields = {f for f in cls.__dataclass_fields__.keys()}
        extra = {k: v for k, v in data.items() if k not in known_fields}
        data = {k: v for k, v in data.items() if k in known_fields}
        if extra:
            data["extra"] = {**data.get("extra", {}), **extra}

        return cls(**data)
