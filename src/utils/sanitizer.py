"""敏感信息脱敏工具

依据 config.yaml 中的 sanitizer 规则对输入文本进行脱敏，
确保不向 LLM 或日志中泄露密码、Token、IP、手机号、身份证等敏感数据。
"""

import re
from typing import List, Dict, Any


class Sanitizer:
    """敏感信息脱敏器"""

    DEFAULT_RULES: List[Dict[str, str]] = [
        {
            "type": "password",
            "pattern": r"(?i)(password|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+",
            "replacement": r"\1=[已脱敏]",
        },
        {
            "type": "ip",
            "pattern": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            "replacement": "X.X.X.X",
        },
        {
            "type": "phone",
            "pattern": r"\b1[3-9]\d{9}\b",
            "replacement": "1XX****XXXX",
        },
        {
            "type": "id_card",
            "pattern": r"\b\d{17}[\dXx]\b",
            "replacement": "[身份证已脱敏]",
        },
        {
            "type": "email",
            "pattern": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
            "replacement": "[邮箱已脱敏]",
        },
        {
            "type": "bearer",
            "pattern": r"(?i)bearer\s+[A-Za-z0-9._\-]+",
            "replacement": "Bearer [已脱敏]",
        },
    ]

    def __init__(self, rules: List[Dict[str, str]] = None, enable: bool = True):
        self.enable = enable
        self.rules = rules if rules is not None else self.DEFAULT_RULES
        # 预编译正则
        self._compiled = [
            (re.compile(r["pattern"]), r["replacement"], r["type"])
            for r in self.rules
        ]

    def sanitize(self, text: str) -> str:
        """脱敏文本"""
        if not self.enable or not text:
            return text
        result = text
        for pattern, replacement, _ in self._compiled:
            result = pattern.sub(replacement, result)
        return result

    def sanitize_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """递归脱敏字典中所有字符串值"""
        if not self.enable:
            return data
        out = {}
        for k, v in data.items():
            if isinstance(v, str):
                out[k] = self.sanitize(v)
            elif isinstance(v, dict):
                out[k] = self.sanitize_dict(v)
            elif isinstance(v, list):
                out[k] = [
                    self.sanitize(i) if isinstance(i, str)
                    else self.sanitize_dict(i) if isinstance(i, dict)
                    else i
                    for i in v
                ]
            else:
                out[k] = v
        return out

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "Sanitizer":
        sanitizer_cfg = cfg.get("sanitizer", {})
        return cls(
            rules=sanitizer_cfg.get("rules"),
            enable=sanitizer_cfg.get("enable", True),
        )
