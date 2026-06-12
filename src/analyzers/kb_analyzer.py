"""知识库批量分析器（P2）

V1（DedupAnalyzer）：单条新缺陷 vs 历史库
V2（KBAnalyzer）：知识库画像 + 批量入库 + 批量交叉查重 + 历史共性归纳

知识库格式：JSONL，每行一条 Defect.to_dict()
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from ..models import Defect
from ..utils import LLMClient, Sanitizer, MarkdownFormatter
from .cluster_analyzer import detect_patterns, PATTERN_DICT
from .dedup_analyzer import DedupAnalyzer


class KBAnalyzer:
    """知识库批量分析器"""

    def __init__(self, llm: LLMClient, sanitizer: Sanitizer,
                 kb_path: str = "data/knowledge_base/defects.jsonl",
                 similarity_threshold: float = 0.3):
        self.llm = llm
        self.sanitizer = sanitizer
        self.kb_path = Path(kb_path)
        self.similarity_threshold = similarity_threshold
        # 复用 DedupAnalyzer 的相似度算法
        self._dedup = DedupAnalyzer(
            llm, sanitizer, kb_path=str(kb_path),
            similarity_threshold=similarity_threshold,
        )

    # ============================================================
    # 知识库加载
    # ============================================================
    def load_kb(self) -> List[Defect]:
        return self._dedup.load_kb()

    # ============================================================
    # 批量入库
    # ============================================================
    def import_batch(self, defects: List[Defect],
                      skip_invalid: bool = True,
                      dedupe_by_id: bool = True) -> Dict[str, Any]:
        """批量将缺陷加入知识库

        Args:
            defects: 待入库缺陷列表
            skip_invalid: 跳过无效记录（defect_name 为空）
            dedupe_by_id: 跳过 KB 中已存在的 ID

        Returns:
            {imported, skipped_invalid, skipped_duplicate, total}
        """
        existing_ids = {d.id for d in self.load_kb()} if dedupe_by_id else set()
        imported, skipped_invalid, skipped_dup = 0, 0, 0

        self.kb_path.parent.mkdir(parents=True, exist_ok=True)
        with self.kb_path.open("a", encoding="utf-8") as f:
            for d in defects:
                if skip_invalid and not d.is_valid:
                    skipped_invalid += 1
                    continue
                if dedupe_by_id and d.id in existing_ids:
                    skipped_dup += 1
                    continue
                f.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")
                imported += 1
                existing_ids.add(d.id)

        return {
            "imported":          imported,
            "skipped_invalid":   skipped_invalid,
            "skipped_duplicate": skipped_dup,
            "total":             len(defects),
            "kb_size_after":     len(existing_ids),
        }

    # ============================================================
    # 知识库画像
    # ============================================================
    def profile(self) -> str:
        """生成知识库画像 Markdown 报告"""
        kb = self.load_kb()
        if not kb:
            return self._empty_kb_report()

        md = MarkdownFormatter()
        md.title("知识库画像", 1)
        md.kv_pairs({
            "知识库路径": str(self.kb_path),
            "总条数":     len(kb),
            "有效条数":   sum(1 for d in kb if d.is_valid),
        })

        # 模块分布
        module_counter = Counter(d.module for d in kb)
        md.title("1. 模块分布", 2)
        md.table(
            ["模块", "条数", "占比"],
            [[m, c, MarkdownFormatter.percent(c, len(kb))]
              for m, c in module_counter.most_common(10)],
        )

        # 优先级分布
        prio_counter = Counter(d.priority.value for d in kb)
        md.title("2. 优先级分布", 2)
        md.table(
            ["优先级", "条数", "占比"],
            [[p, c, MarkdownFormatter.percent(c, len(kb))]
              for p, c in prio_counter.most_common()],
        )

        # 问题类型分布（拆词）
        pt_counter: Counter = Counter()
        for d in kb:
            for pt in d.problem_type_list():
                pt_counter[pt] += 1
        if pt_counter:
            md.title("3. 问题类型分布（Top 10）", 2)
            md.table(
                ["问题类型", "条数"],
                [[pt, c] for pt, c in pt_counter.most_common(10)],
            )

        # 时间跨度
        times = sorted([d.occurrence_time for d in kb if d.occurrence_time])
        if times:
            md.title("4. 时间跨度", 2)
            md.kv_pairs({
                "最早记录": times[0][:10],
                "最晚记录": times[-1][:10],
            })

        # 业务模式覆盖
        valid = [d for d in kb if d.is_valid]
        pattern_hits = detect_patterns(valid)
        if pattern_hits:
            md.title("5. 知识库已沉淀的业务模式", 2)
            md.text("（基于 ClusterAnalyzer 模式字典识别，命中即说明有该领域问题的历史经验）")
            rows = [
                [
                    next(p["name"] for p in PATTERN_DICT if p["id"] == pid),
                    len(hits),
                ]
                for pid, hits in sorted(
                    pattern_hits.items(),
                    key=lambda x: -len(x[1]),
                )
            ]
            md.table(["业务模式", "已知缺陷数"], rows)

        return md.render()

    def _empty_kb_report(self) -> str:
        return (
            "## 知识库画像\n\n"
            f"⚠️ 知识库为空（路径: `{self.kb_path}`）\n\n"
            "建议先用 `import` 命令批量入库。"
        )

    # ============================================================
    # 批量交叉查重
    # ============================================================
    def cross_check(self, new_defects: List[Defect],
                     top_k: int = 3) -> Dict[str, Any]:
        """批量缺陷 vs 历史库交叉查重

        Returns:
            {
                "summary": {...},
                "items": [
                    {
                        "defect_id": ...,
                        "defect_name": ...,
                        "judgement": "新问题/相似/复发",
                        "matches": [{kb_id, similarity, kb_name}, ...]
                    }
                ]
            }
        """
        kb = self.load_kb()
        if not kb:
            return {
                "summary": {"total": len(new_defects), "kb_size": 0},
                "items": [],
                "warning": "知识库为空，无法做查重",
            }

        items = []
        new_count, similar_count, regression_count = 0, 0, 0
        kb_id_set = {d.id for d in kb}

        for d in new_defects:
            if not d.is_valid:
                continue

            scored = sorted(
                [(kd, self._dedup._similarity(d, kd)) for kd in kb],
                key=lambda x: x[1],
                reverse=True,
            )[:top_k]

            top_match = scored[0] if scored else (None, 0)
            top_score = top_match[1]

            # 判定规则
            if top_score >= 0.85 and top_match[0] and top_match[0].id == d.id:
                # 同 ID + 极高相似度 → 复发
                judgement = "复发"
                regression_count += 1
            elif top_score >= 0.7:
                judgement = "相似（高）"
                similar_count += 1
            elif top_score >= self.similarity_threshold:
                judgement = "相似（中）"
                similar_count += 1
            else:
                judgement = "新问题"
                new_count += 1

            items.append({
                "defect_id":   d.id,
                "defect_name": self.sanitizer.sanitize(d.defect_name or "")[:80],
                "module":      d.module,
                "judgement":   judgement,
                "top_score":   round(top_score, 3),
                "matches": [
                    {
                        "kb_id":      kd.id,
                        "similarity": round(s, 3),
                        "kb_name":    self.sanitizer.sanitize(kd.defect_name or "")[:60],
                    }
                    for kd, s in scored if s >= self.similarity_threshold
                ],
            })

        return {
            "summary": {
                "total":           len(items),
                "new":             new_count,
                "similar":         similar_count,
                "regression":      regression_count,
                "kb_size":         len(kb),
            },
            "items": items,
        }

    def cross_check_report(self, new_defects: List[Defect],
                            top_k: int = 3) -> str:
        """批量查重 Markdown 报告"""
        result = self.cross_check(new_defects, top_k)

        md = MarkdownFormatter()
        md.title("批量查重报告", 1)

        if "warning" in result:
            md.text(f"⚠️ {result['warning']}")
            return md.render()

        s = result["summary"]
        md.title("1. 概览", 2)
        md.kv_pairs({
            "新批次缺陷数":   s["total"],
            "知识库已有":     s["kb_size"],
            "🔴 复发":         s["regression"],
            "🟠 相似":         s["similar"],
            "🟡 新问题":       s["new"],
        })

        if s["regression"] > 0:
            md.title("⚠️ 警告", 3)
            md.text(
                f"识别出 **{s['regression']}** 条复发缺陷，"
                "建议立即检查上次修复方案是否在新版本中失效。"
            )

        md.title("2. 详细判定结果", 2)
        rows = []
        for item in result["items"]:
            top_match_str = ""
            if item["matches"]:
                m = item["matches"][0]
                top_match_str = f"{m['kb_id']} ({m['similarity']})"
            rows.append([
                item["defect_id"],
                item["judgement"],
                item["module"],
                f"{item['top_score']}",
                top_match_str,
                item["defect_name"],
            ])
        md.table(
            ["新缺陷ID", "判定", "模块", "Top 相似度", "Top 匹配", "缺陷名称"],
            rows,
        )

        # 复发清单（详细）
        regressions = [it for it in result["items"] if it["judgement"] == "复发"]
        if regressions:
            md.title("3. 复发缺陷详情", 2)
            for r in regressions:
                md.title(f"- {r['defect_id']} - {r['defect_name']}", 3)
                md.text("匹配历史记录：")
                md.bullet_list([
                    f"`{m['kb_id']}` (相似度 {m['similarity']}): {m['kb_name']}"
                    for m in r["matches"]
                ])

        return md.render()

    # ============================================================
    # 历史共性归纳（基于 KB 全集做模式聚类）
    # ============================================================
    def historical_patterns(self) -> str:
        """从知识库中归纳"已知问题模式"清单"""
        kb = self.load_kb()
        valid = [d for d in kb if d.is_valid]
        if not valid:
            return self._empty_kb_report()

        pattern_hits = detect_patterns(valid)

        md = MarkdownFormatter()
        md.title("知识库历史问题模式归纳", 1)
        md.text(
            f"基于知识库 **{len(valid)}** 条有效缺陷，"
            f"识别出 **{len(pattern_hits)}** 个已知问题模式。"
        )

        if not pattern_hits:
            md.text("\n_当前知识库未匹配到任何业务模式字典_")
            return md.render()

        md.title("已知问题模式清单（按缺陷数排序）", 2)
        for pid, hits in sorted(pattern_hits.items(), key=lambda x: -len(x[1])):
            pattern_meta = next(p for p in PATTERN_DICT if p["id"] == pid)
            md.title(f"### {pattern_meta['name']}（{len(hits)} 条）", 3)
            md.kv_pairs({
                "严重度":    pattern_meta["severity"],
                "责任端":    pattern_meta["owner_hint"],
                "治理动作":  pattern_meta["treatment"],
            })
            md.text("**历史缺陷示例：**")
            md.bullet_list([
                f"`{d.id}` - {self.sanitizer.sanitize(d.defect_name or '')[:60]}"
                for d in hits[:5]
            ])
            if len(hits) > 5:
                md.text(f"_（共 {len(hits)} 条，仅展示前 5 条）_")
            md.blank()

        # 治理优先级建议
        md.title("治理优先级建议", 2)
        priorities = sorted(
            pattern_hits.items(),
            key=lambda x: (-len(x[1]),),
        )[:3]
        md.text("基于知识库历史发生频次，建议优先治理：")
        md.bullet_list([
            f"**{next(p['name'] for p in PATTERN_DICT if p['id'] == pid)}** "
            f"（历史 {len(hits)} 条）"
            for pid, hits in priorities
        ])

        return md.render()
