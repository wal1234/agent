"""台账 JSON 加载器

支持加载真实台账 JSON 格式（结构形如）：
    {
      "metadata": {...},
      "defects": [ {字段对齐 Defect 模型} ... ]
    }

也支持 [Defect 数组] 直接传入。
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Tuple, Union

from .models import Defect


def load_taizhang_json(path: Union[str, Path]) -> Tuple[List[Defect], Dict[str, Any]]:
    """从 JSON 文件加载台账

    Returns:
        (defects, metadata)
        - defects: 完整 Defect 列表（含 UNKNOWN 空白记录，由后续质量分析器识别）
        - metadata: 台账原始 metadata 字典（可能为空 {}）
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"台账文件不存在: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # 支持两种格式：{"defects": [...]} 或 [...]
    if isinstance(data, list):
        raw_defects = data
        metadata = {}
    elif isinstance(data, dict):
        raw_defects = data.get("defects", [])
        metadata = data.get("metadata", {})
    else:
        raise ValueError(f"不支持的 JSON 顶层结构: {type(data).__name__}")

    defects = []
    for idx, row in enumerate(raw_defects):
        try:
            defects.append(Defect.from_dict(row))
        except Exception as e:
            # 不丢条目，仅打印警告（实际生产可接入 logger）
            print(f"⚠️  第 {idx} 条加载失败: {e}")
    return defects, metadata


def load_defects(path: Union[str, Path]) -> List[Defect]:
    """便捷方法：只返回 defects 列表"""
    defects, _ = load_taizhang_json(path)
    return defects
