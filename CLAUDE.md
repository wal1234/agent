# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**生产缺陷神探 (Defect Hunter)** —— 一款面向生产环境缺陷分析的 LLM 智能体。基于真实台账（`2025年渠道营销中台运维台账.xlsx`，199 条缺陷数据）驱动设计。

> **当前版本**：V1 全部 7 大能力 + V2 服务化 MVP 完成，默认 LLM 为火山引擎方舟（glm-5.1）。
>
> 完整状态见 [`PROJECT.md`](./PROJECT.md)。设计依据见 `docs/智能体设计文档.md` 与 `docs/数据画像速览.md`。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 LLM API Key（默认火山引擎方舟，可切换 OpenAI/Anthropic）
export ARK_API_KEY=<火山引擎方舟 API Key>     # 推荐
# 或
export OPENAI_API_KEY=sk-xxx
export ANTHROPIC_API_KEY=sk-ant-xxx

# CLI 五大能力
python main.py rca   --log data/samples/sample_log.txt
python main.py cluster --file data/samples/sample_defects.csv
python main.py trend   --file data/samples/sample_defects.csv --period "2025-01 ~ 2025-12"
python main.py dedup   --file new_defect.csv --add-to-kb
python main.py responsibility --file data/samples/sample_defects.csv --id BUG-001

# 输出落盘
python main.py trend --file xxx.csv --output reports/weekly.md

# 单测
pytest tests/                            # 全量
pytest tests/test_sanitizer.py -v        # 单文件
pytest tests/test_sanitizer.py::test_password_masked  # 单用例
```

## 架构核心

### 分层与依赖方向

```
main.py (Click CLI)
   │
   ▼
src/agent.py  DefectHunterAgent  ── 五大分析能力的统一门面（API 不要绕过它）
   │
   ▼
src/analyzers/*.py  五个独立分析器
   │           ↓ 都依赖：
   ├─ src/utils/llm_client.py   LLM 调用（Anthropic / OpenAI 双 Provider，懒加载 SDK）
   ├─ src/utils/sanitizer.py    敏感信息脱敏（必须在送入 LLM 前调用）
   ├─ src/utils/formatter.py    Markdown 报告构造器
   ├─ src/prompts/templates.py  五套 Prompt 模板（系统人设 + 各能力 user prompt）
   └─ src/models/defect.py      Defect 实体 + 四个枚举（Priority/Stage/RootCauseType/Status）
```

### 关键设计约束

1. **本地确定性统计 + LLM 解读**：所有分析器先做本地 Counter/聚类/帕累托等可复算的统计，再把统计结果交给 LLM 做共性解读。**不要让 LLM 单独完成统计工作**——它做不准确，且违背设计原则。`cluster_analyzer.py` 和 `trend_analyzer.py` 是该模式的样板。

2. **LLM 缺失降级**：所有 LLM 调用必须能在无 API Key 时优雅降级（输出本地骨架报告 + 提示）。`llm_client.complete()` 已实现这一行为，分析器内 try/except 后调用各自的 `_fallback_*` 方法。**不要让流程在无 Key 时崩溃**。

3. **送 LLM 前必脱敏**：`Sanitizer` 内置 6 类规则（密码/IP/手机/邮箱/身份证/Bearer Token），新增字段进入 prompt 前调用 `self.sanitizer.sanitize(text)`。

4. **知识库格式 = JSONL**：`data/knowledge_base/defects.jsonl` 一行一条 `Defect.to_dict()`。`DedupAnalyzer` 用 Jaccard + 关键字段加成做粗筛，再交 LLM 终判。**不要引入向量数据库**——当前数据量（< 200）不够，且会拖慢启动。

### 数据模型注意

- `Defect.priority` 是 `Priority` 枚举（P0/P1/P2/P3），CSV 加载时 `main.py:load_defects_from_csv` 会做枚举转换。新加字段时记得在 `Defect.to_dict()` / `Defect.from_dict()` 两侧同步处理枚举序列化。
- `Defect.is_severe` 是判断 P0/P1 的便捷属性，在统计中频繁使用。

## 真实台账驱动的优先级（重要）

`docs/智能体设计文档.md` 中明确：基于 199 条真实数据的特征，能力优先级与 Skill 通用描述**不同**：

| 优先级 | 能力 | 实现 | 关键依据 |
|--------|------|------|----------|
| 🔴 P0 | **数据质量分析** | `quality_analyzer.py` | 60% 数据空白，必须先治理 |
| 🔴 P0 | 缺陷查重 & 聚类 | `cluster_analyzer.py`（含 12 业务模式字典）+ `dedup_analyzer.py` | 15+ 条同根因技术债 |
| 🔴 P0 | 趋势分析（含帕累托） | `trend_analyzer.py` | 业务方刚需 |
| 🟠 P1 | RCA 根因分析 | `rca_analyzer.py`（含团队术语注入） | 80 条历史样本可注入 Prompt |
| 🟠 P1 | 专项治理（3 个模板） | `governance_analyzer.py` | 三大集群已识别 |
| 🟡 P2 | 责任界定 | `responsibility_analyzer.py`（单条 + 批量画像） | 字段污染已可自动检测 |
| 🟡 P2 | 知识库批量分析 | `kb_analyzer.py` | 含批量入库 / 画像 / 交叉查重 / 历史模式 |

**所有 7 大能力均已实现**，新开发主要是增量改动而非补全。

**做新开发前先读** `docs/智能体设计文档.md` 与 `PROJECT.md`。

## 技术栈基线

| 层 | 当前实现 | 设计目标（V2+） |
|----|----------|-----------------|
| Python | 3.8+（实际） | 3.12 |
| Web | **FastAPI 0.124+ + uvicorn 0.33+ + Pydantic 2.x（已落地，16 endpoints）** | 同 |
| 编排 | **自实现轻量 StateGraph（API 对齐 LangGraph）** | LangGraph 1.0（需 Python 3.10+） |
| DB | JSONL 文件 | PostgreSQL + psycopg 3（含 LangGraph checkpoint） |
| LLM | Anthropic / OpenAI 兼容 / **火山引擎方舟（默认 glm-5.1）** | 同 |

**当前 V2 服务化已完成 MVP**（FastAPI + 编排子图 + 16 endpoints），**未引入 PostgreSQL**——这是 M7+ 的事，需 Python 升级后接入。

## 配置文件

`config/config.yaml` 是单一事实来源：
- LLM provider/model（默认 `volcengine` + `glm-5.1`，base_url 自动指向 `https://ark.cn-beijing.volces.com/api/v3`）
- 脱敏规则（覆盖 6 类敏感信息）
- 知识库路径与相似度阈值
- 优先级/阶段/根因类型枚举字典

修改 LLM 行为优先改 yaml，不要硬编码在分析器里。
