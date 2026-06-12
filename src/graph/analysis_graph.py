"""分析编排子图

V1 自实现轻量 DAG 编排器，未来可平滑切换至 LangGraph 1.0。
API 形态对齐 LangGraph 的 StateGraph 风格（add_node / add_edge / compile）。

执行图谱（数据流）：

    [load] → [quality] → [normalize] ┬→ [cluster] ┬→ [governance] → [compose]
                                       └→ [trend]   ┘

- quality:    数据质量分析（确定性，无 LLM）
- normalize:  标记有效缺陷
- cluster:    业务模式聚类
- trend:      趋势研判
- governance: 基于 cluster 结果生成专项治理（依赖 cluster）
- compose:    汇总所有节点输出为最终报告字典
"""

from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, List, Optional, Set, TypedDict

from ..models import Defect


# ============================================================
# 状态定义（共享 State，节点读写）
# ============================================================
class AnalysisState(TypedDict, total=False):
    # 输入
    defects: List[Defect]
    period: str
    governance_pattern: Optional[str]   # 指定治理模板，None 则全集

    # 中间结果
    valid_defects: List[Defect]
    quality_result: Dict[str, Any]      # {report_md, issues, summary}

    # 各分析报告
    cluster_report: str
    trend_report: str
    governance_report: str

    # 最终结果
    final: Dict[str, Any]               # 汇总输出


# ============================================================
# 编排器（API 与 LangGraph StateGraph 对齐）
# ============================================================
class StateGraph:
    """轻量 DAG 编排器

    用法：
        graph = StateGraph()
        graph.add_node("foo", fn_foo)
        graph.add_edge("foo", "bar")
        runner = graph.compile()
        result_state = runner.invoke(initial_state)
    """

    START = "__start__"
    END = "__end__"

    def __init__(self):
        self._nodes: Dict[str, Callable[[Dict], Dict]] = {}
        self._edges: Dict[str, Set[str]] = {}      # node -> downstream nodes
        self._reverse_edges: Dict[str, Set[str]] = {}  # node -> upstream

    def add_node(self, name: str, fn: Callable[[Dict], Dict]) -> "StateGraph":
        if name in (self.START, self.END):
            raise ValueError(f"保留节点名: {name}")
        self._nodes[name] = fn
        self._edges.setdefault(name, set())
        self._reverse_edges.setdefault(name, set())
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self._edges.setdefault(src, set()).add(dst)
        self._reverse_edges.setdefault(dst, set()).add(src)
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        return self.add_edge(self.START, name)

    def set_finish_point(self, name: str) -> "StateGraph":
        return self.add_edge(name, self.END)

    def compile(self) -> "GraphRunner":
        return GraphRunner(self)


class GraphRunner:
    """图执行器：拓扑排序 + 并行可独立分支"""

    def __init__(self, graph: StateGraph):
        self.graph = graph
        self._validate()

    def _validate(self) -> None:
        """检测环 + 孤立节点"""
        nodes = set(self.graph._nodes.keys())
        # 简单环检测：DFS
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in nodes}

        def dfs(n: str):
            if color.get(n, BLACK) == GRAY:
                raise ValueError(f"图中存在环，涉及节点: {n}")
            if color.get(n, BLACK) == BLACK:
                return
            color[n] = GRAY
            for d in self.graph._edges.get(n, set()):
                if d != StateGraph.END:
                    dfs(d)
            color[n] = BLACK

        for n in nodes:
            if color[n] == WHITE:
                dfs(n)

    def invoke(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """同步执行整张图，返回最终 state"""
        state = dict(initial_state)
        executed: Set[str] = set()
        # 拓扑遍历
        # 起始节点：由 START 直接连出
        starts = self.graph._edges.get(StateGraph.START, set())

        # 简单做法：按"前驱全部完成"的可执行集滚动
        pending = set(self.graph._nodes.keys())
        while pending:
            # 找出可执行节点（前驱全部已执行 + 至少一个前驱是 START 或已执行节点）
            ready = []
            for n in pending:
                preds = self.graph._reverse_edges.get(n, set())
                # 移除 START 视为前驱
                real_preds = {p for p in preds if p != StateGraph.START}
                if real_preds.issubset(executed):
                    ready.append(n)
            if not ready:
                raise RuntimeError(f"图无法继续执行，剩余节点: {pending}")

            # 并行执行 ready 中互不依赖的节点
            if len(ready) == 1:
                state.update(self.graph._nodes[ready[0]](state) or {})
                executed.add(ready[0])
                pending.remove(ready[0])
            else:
                with ThreadPoolExecutor(max_workers=len(ready)) as exe:
                    futures: Dict[str, Future] = {
                        n: exe.submit(self.graph._nodes[n], dict(state))
                        for n in ready
                    }
                    for n, fut in futures.items():
                        result = fut.result() or {}
                        state.update(result)
                        executed.add(n)
                        pending.remove(n)
        return state


# ============================================================
# 工厂函数：构造完整分析图
# ============================================================
def build_analysis_graph(agent) -> GraphRunner:
    """基于 DefectHunterAgent 构造分析图

    Args:
        agent: DefectHunterAgent 实例
    """

    # ---------- 节点定义 ----------
    def node_quality(state: AnalysisState) -> Dict[str, Any]:
        defects = state["defects"]
        result = agent.quality(defects)
        return {"quality_result": result}

    def node_normalize(state: AnalysisState) -> Dict[str, Any]:
        defects = state["defects"]
        valid = [d for d in defects if d.is_valid]
        return {"valid_defects": valid}

    def node_cluster(state: AnalysisState) -> Dict[str, Any]:
        defects = state.get("valid_defects") or state["defects"]
        report = agent.cluster(defects)
        return {"cluster_report": report}

    def node_trend(state: AnalysisState) -> Dict[str, Any]:
        defects = state.get("valid_defects") or state["defects"]
        period = state.get("period", "")
        report = agent.trend(defects, period=period)
        return {"trend_report": report}

    def node_governance(state: AnalysisState) -> Dict[str, Any]:
        defects = state.get("valid_defects") or state["defects"]
        pattern = state.get("governance_pattern")
        report = agent.governance(defects, pattern_id=pattern)
        return {"governance_report": report}

    def node_compose(state: AnalysisState) -> Dict[str, Any]:
        q = state.get("quality_result", {})
        final = {
            "summary": {
                "total_defects":   len(state.get("defects", [])),
                "valid_defects":   len(state.get("valid_defects", [])),
                "completeness":    q.get("summary", {}).get("completeness_score", 0),
                "pollution_count": q.get("summary", {}).get("pollution_count", 0),
            },
            "reports": {
                "quality":    q.get("report_md", ""),
                "cluster":    state.get("cluster_report", ""),
                "trend":      state.get("trend_report", ""),
                "governance": state.get("governance_report", ""),
            },
            "issues": q.get("issues", []),
        }
        return {"final": final}

    # ---------- 图组装 ----------
    graph = StateGraph()
    graph.add_node("quality", node_quality)
    graph.add_node("normalize", node_normalize)
    graph.add_node("cluster", node_cluster)
    graph.add_node("trend", node_trend)
    graph.add_node("governance", node_governance)
    graph.add_node("compose", node_compose)

    # 边：quality 与 normalize 并行 → cluster/trend 并行（依赖 normalize）
    #    → governance 依赖 cluster → compose 收尾
    graph.set_entry_point("quality")
    graph.set_entry_point("normalize")
    graph.add_edge("normalize", "cluster")
    graph.add_edge("normalize", "trend")
    graph.add_edge("cluster", "governance")
    graph.add_edge("quality", "compose")
    graph.add_edge("trend", "compose")
    graph.add_edge("governance", "compose")
    graph.set_finish_point("compose")

    return graph.compile()
