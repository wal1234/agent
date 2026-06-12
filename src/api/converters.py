"""请求 / 模型转换辅助"""

from typing import List

from ..models import Defect
from .schemas import DefectIn, TaiZhangIn


def defect_in_to_model(d: DefectIn) -> Defect:
    """Pydantic 输入 → Defect 数据类"""
    return Defect.from_dict(d.model_dump())


def taizhang_to_defects(t: TaiZhangIn) -> List[Defect]:
    return [defect_in_to_model(d) for d in t.defects]
