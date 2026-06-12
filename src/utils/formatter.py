"""Markdown 输出格式化工具

为各分析器提供统一的 Markdown 输出能力：标题、表格、代码块、徽章等。
"""

from typing import List, Dict, Any, Optional


class MarkdownFormatter:
    """Markdown 报告构造器"""

    def __init__(self):
        self._buffer: List[str] = []

    # ---------- 基础元素 ----------
    def title(self, text: str, level: int = 2) -> "MarkdownFormatter":
        self._buffer.append(f"{'#' * level} {text}")
        self._buffer.append("")
        return self

    def text(self, text: str) -> "MarkdownFormatter":
        self._buffer.append(text)
        self._buffer.append("")
        return self

    def bold(self, text: str) -> str:
        return f"**{text}**"

    def code(self, text: str, lang: str = "") -> "MarkdownFormatter":
        self._buffer.append(f"```{lang}")
        self._buffer.append(text)
        self._buffer.append("```")
        self._buffer.append("")
        return self

    def bullet_list(self, items: List[str]) -> "MarkdownFormatter":
        for item in items:
            self._buffer.append(f"- {item}")
        self._buffer.append("")
        return self

    def checklist(self, items: List[str]) -> "MarkdownFormatter":
        for item in items:
            self._buffer.append(f"- [ ] {item}")
        self._buffer.append("")
        return self

    def table(self, headers: List[str], rows: List[List[Any]]) -> "MarkdownFormatter":
        self._buffer.append("| " + " | ".join(headers) + " |")
        self._buffer.append("|" + "|".join(["------"] * len(headers)) + "|")
        for row in rows:
            self._buffer.append("| " + " | ".join(str(c) for c in row) + " |")
        self._buffer.append("")
        return self

    def kv_pairs(self, pairs: Dict[str, Any]) -> "MarkdownFormatter":
        for k, v in pairs.items():
            self._buffer.append(f"- **{k}**: {v}")
        self._buffer.append("")
        return self

    def blank(self) -> "MarkdownFormatter":
        self._buffer.append("")
        return self

    def render(self) -> str:
        return "\n".join(self._buffer).rstrip() + "\n"

    @staticmethod
    def percent(numerator: int, denominator: int) -> str:
        """格式化为百分比字符串"""
        if denominator == 0:
            return "0%"
        return f"{numerator * 100 / denominator:.1f}%"
