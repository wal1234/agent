"""缺陷查重分析器（V1，适配新 Defect 字段）

策略：
1. 关键词匹配（业务关键词 + module）
2. Jaccard 相似度粗筛
3. LLM 终判
"""

import json
import re
from pathlib import Path
from typing import List, Tuple

from ..models import Defect
from ..utils import LLMClient, Sanitizer
from ..prompts import PromptTemplates


class DedupAnalyzer:
    """缺陷查重器"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer,
                 kb_path: str = "data/knowledge_base/defects.jsonl",
                 similarity_threshold: float = 0.3):
        self.llm = llm
        self.sanitizer = sanitizer
        self.kb_path = kb_path
        self.similarity_threshold = similarity_threshold

    # ---------- 知识库 ----------
    def load_kb(self) -> List[Defect]:
        path = Path(self.kb_path)
        if not path.exists():
            return []
        defects = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    defects.append(Defect.from_dict(json.loads(line)))
                except Exception:
                    continue
        return defects

    def append_kb(self, defect: Defect) -> None:
        path = Path(self.kb_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(defect.to_dict(), ensure_ascii=False) + "\n")

    # ---------- 相似度 ----------
    @staticmethod
    def _tokens(text: str) -> set:
        if not text:
            return set()
        return {
            w.lower()
            for w in re.split(r"[\s,.;:()（）【】\[\]{}\-_/、]+", text)
            if len(w) > 1
        }

    def _similarity(self, a: Defect, b: Defect) -> float:
        text_a = " ".join(filter(None, [a.defect_name, a.root_cause, a.module, a.problem_type]))
        text_b = " ".join(filter(None, [b.defect_name, b.root_cause, b.module, b.problem_type]))
        ta, tb = self._tokens(text_a), self._tokens(text_b)
        if not ta or not tb:
            return 0.0
        inter = len(ta & tb)
        union = len(ta | tb)
        base = inter / union if union else 0.0
        # 关键字段加成
        if a.module and a.module == b.module:
            base += 0.10
        if a.problem_type and a.problem_type == b.problem_type:
            base += 0.10
        return min(base, 1.0)

    # ---------- 主流程 ----------
    def analyze(self, new_defect: Defect, top_k: int = 3) -> str:
        kb = self.load_kb()
        if not kb:
            return self._render_empty_kb(new_defect)

        scored: List[Tuple[Defect, float]] = [
            (d, self._similarity(new_defect, d)) for d in kb
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        candidates = [(d, s) for d, s in scored[:top_k] if s >= self.similarity_threshold]

        if not candidates:
            return self._render_new_problem(new_defect, scored[:1])

        prompt = PromptTemplates.DEDUP_USER.format(
            new_defect=self._render_defect(new_defect),
            candidates="\n\n".join(
                f"### 候选 {i+1} (相似度: {s:.2f})\n{self._render_defect(d)}"
                for i, (d, s) in enumerate(candidates)
            ),
        )
        try:
            return self.llm.complete(PromptTemplates.SYSTEM_BASE, prompt)
        except Exception as e:
            return self._render_local_only(new_defect, candidates, str(e))

    def _render_defect(self, d: Defect) -> str:
        return (
            f"- **ID**: {d.id}\n"
            f"- **缺陷名称**: {self.sanitizer.sanitize(d.defect_name or '')}\n"
            f"- **模块**: {d.module}\n"
            f"- **问题类型**: {d.problem_type or '-'}\n"
            f"- **根因**: {self.sanitizer.sanitize(d.root_cause or '')[:200]}\n"
            f"- **修复状态**: {d.fix_status or '-'}"
        )

    def _render_empty_kb(self, d: Defect) -> str:
        return (
            "## 缺陷查重报告\n\n"
            "### 判定: **新问题**（知识库为空）\n\n"
            f"### 新缺陷特征\n{self._render_defect(d)}\n\n"
            "### 建议\n- 将本次缺陷归档至知识库"
        )

    def _render_new_problem(self, d: Defect,
                             nearest: List[Tuple[Defect, float]]) -> str:
        info = "\n".join(
            f"- {n[0].id} / {n[0].defect_name} (相似度 {n[1]:.2f})"
            for n in nearest
        ) or "（无）"
        return (
            "## 缺陷查重报告\n\n"
            f"### 判定: **新问题**（相似度低于阈值 {self.similarity_threshold}）\n\n"
            f"### 最接近的历史记录\n{info}\n\n"
            f"### 新缺陷特征\n{self._render_defect(d)}"
        )

    def _render_local_only(self, d: Defect,
                            candidates: List[Tuple[Defect, float]],
                            err: str) -> str:
        rows = "\n".join(
            f"- {c.id} / {c.defect_name} (相似度 {s:.2f})"
            for c, s in candidates
        )
        return (
            "## 缺陷查重报告（本地相似度模式）\n\n"
            f"### 新缺陷\n{self._render_defect(d)}\n\n"
            f"### 命中候选\n{rows}\n\n"
            f"_LLM 终判失败：{err}_"
        )
