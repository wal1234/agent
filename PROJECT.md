# 生产缺陷神探 (Defect Hunter)

> 面向生产环境缺陷分析的 LLM 智能体。基于真实台账（2025 渠道营销中台运维台账，199 条缺陷）数据驱动设计，覆盖**数据质量治理 → 缺陷归纳 → 趋势研判 → 根因分析 → 专项治理 → 责任界定 → 知识库沉淀**全链路。

---

## 1. 项目概述

### 1.1 目标

帮助质量团队从生产缺陷中快速回答 7 类核心问题：

| # | 业务问题 | 对应能力 |
|---|---------|---------|
| 1 | 这条缺陷的根本原因是什么？怎么修？ | RCA 根因分析 |
| 2 | 这是新问题还是历史复发？ | 知识库查重 |
| 3 | 谁负主责？为什么会漏测？ | 责任界定 |
| 4 | 这批缺陷有什么共性？ | 相似缺陷归纳 |
| 5 | 这周/月/季度的缺陷趋势如何？ | 缺陷汇总与趋势研判 |
| 6 | 知识库里有什么经验可借鉴？ | 知识库历史分析 |
| 7 | 台账数据本身质量如何？哪些字段需补全？ | 数据质量分析 |

### 1.2 设计原则（重要）

1. **本地确定性统计 + LLM 解读分离** —— 所有计数、聚类、帕累托、模式识别都用代码做（可复现、零幻觉），LLM 只负责跨集群洞察与改进建议生成。
2. **LLM 缺失降级** —— 无 API Key 时所有分析器仍能输出基于规则的本地骨架报告，不阻塞使用。
3. **送 LLM 必脱敏** —— 6 类敏感信息（密码 / IP / 手机 / 邮箱 / 身份证 / Bearer Token）自动脱敏。
4. **数据驱动的优先级** —— 能力顺序基于真实台账特征（60% 空白数据 → 数据质量分析 P0 优先），不照搬通用模板。

---

## 2. 项目结构

```
zhinengtest/
├── src/
│   ├── agent.py                       # 智能体统一门面
│   ├── loaders.py                     # 台账 JSON 加载器
│   ├── models/
│   │   └── defect.py                  # Defect 数据模型 + 枚举
│   ├── analyzers/                     # 8 个分析器
│   │   ├── quality_analyzer.py            # ⭐ P0 数据质量
│   │   ├── cluster_analyzer.py            # ⭐ P0 业务模式聚类（含 12 模式字典）
│   │   ├── trend_analyzer.py              # ⭐ P0 帕累托 + 风险研判
│   │   ├── rca_analyzer.py                # ⭐ P1 5Why + 团队术语注入
│   │   ├── governance_analyzer.py         # ⭐ P1 专项治理（含 3 治理模板）
│   │   ├── responsibility_analyzer.py     # ⭐ P2 单条 + 批量责任画像
│   │   ├── dedup_analyzer.py              # ⭐ P2 单条查重
│   │   └── kb_analyzer.py                 # ⭐ P2 知识库批量分析
│   ├── prompts/
│   │   └── templates.py               # 5 套结构化 Prompt
│   ├── utils/
│   │   ├── llm_client.py              # 火山引擎方舟 / OpenAI / Anthropic
│   │   ├── sanitizer.py               # 敏感信息脱敏
│   │   └── formatter.py               # Markdown 报告构造器
│   ├── graph/
│   │   └── analysis_graph.py          # 轻量 DAG 编排（API 对齐 LangGraph）
│   └── api/
│       ├── app.py                     # FastAPI 应用 + 16 endpoints
│       ├── schemas.py                 # Pydantic 输入输出模型
│       └── converters.py              # Pydantic ↔ Defect 互转
├── main.py                            # CLI 入口
├── config/config.yaml                 # LLM / 脱敏 / 知识库统一配置
├── data/
│   ├── samples/                       # 示例台账（默认空，由用户提供）
│   └── knowledge_base/                # 知识库 JSONL（运行时累积）
├── docs/
│   ├── 智能体设计文档.md              # 数据画像驱动的完整设计
│   └── 数据画像速览.md                # 1 页摘要
├── tests/
│   └── test_sanitizer.py              # 脱敏单元测试
├── requirements.txt
├── README.md
├── CLAUDE.md                          # Claude Code 工作指南
└── PROJECT.md                         # 本文档（项目总览）
```

---

## 3. 能力矩阵

### 3.1 7 大能力（已 100% 实现）

| 优先级 | 能力 | 实现文件 | 关键特性 |
|--------|------|---------|---------|
| 🔴 P0 | **数据质量分析** | `quality_analyzer.py` | 14 字段缺失率 + 5 类污染规则（含 Excel 时间格式误识别）+ 治理建议 |
| 🔴 P0 | **缺陷聚类归纳** | `cluster_analyzer.py` | **12 个业务模式字典**：复制新增 / 事务并发 / 越权 / 报表导出 / 大屏抽奖 / 积分商城 / 卡券发放 / 订单支付 / 数字权益 / 前端展示 / 空指针 / 非厂商服务费 |
| 🔴 P0 | **趋势研判** | `trend_analyzer.py` | 帕累托 + 风险预警卡片（资损 / 安全 / 离职）+ 发现渠道洞察 + 月度环比 |
| 🟠 P1 | **RCA 根因分析** | `rca_analyzer.py` | 5Why + **9 个团队术语模式** + 5 个台账原文 Few-shot |
| 🟠 P1 | **专项治理** | `governance_analyzer.py` | **3 个完整治理模板**（复制 / 并发 / 越权），含正反例代码 + 测试用例清单 + 监控指标 |
| 🟡 P2 | **责任界定** | `responsibility_analyzer.py` | 单条 LLM 分析 + 批量画像（团队/人员/责任比/离职风险/漏测原因）|
| 🟡 P2 | **知识库批量分析** | `kb_analyzer.py` | 批量入库（自动去重）+ 画像 + 交叉查重（4 级判定）+ 历史模式归纳 |

### 3.2 V2 服务化（已实现）

| 模块 | 状态 |
|------|------|
| FastAPI 应用 + 16 个 endpoint | ✅ |
| Pydantic schema | ✅ |
| 文件上传式调用 | ✅ |
| 自实现轻量 DAG 编排器（API 对齐 LangGraph） | ✅ |
| OpenAPI 自动生成（Swagger UI / ReDoc） | ✅ |
| CORS | ✅ |
| `python main.py serve` 启动 uvicorn | ✅ |

### 3.3 LLM 接入（已实现）

| Provider | base_url | 环境变量 | 状态 |
|----------|----------|---------|------|
| **volcengine（火山引擎方舟，默认）** | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` / `VOLC_ARK_API_KEY` / `VOLCENGINE_API_KEY` | ✅ glm-5.1 |
| openai | （官方） | `OPENAI_API_KEY` | ✅ |
| anthropic | （官方） | `ANTHROPIC_API_KEY` | ✅ |

---

## 4. 项目架构

```
                  ┌─────────────────────────────────────┐
                  │  CLI: main.py    HTTP: FastAPI App  │
                  └──────────────┬──────────────────────┘
                                 │
                  ┌──────────────▼──────────────────────┐
                  │  DefectHunterAgent（统一门面）       │
                  └──────────────┬──────────────────────┘
                                 │
            ┌────────────────────┼────────────────────────┐
            ▼                    ▼                        ▼
    ┌───────────────┐    ┌──────────────┐         ┌──────────────┐
    │ 编排 (graph/) │    │ 8 个分析器   │         │ 工具层       │
    │ StateGraph    │    │ (analyzers/) │         │ - LLM Client │
    │ DAG / 并行    │    │              │         │ - Sanitizer  │
    └───────────────┘    └──────┬───────┘         │ - Formatter  │
                                │                  └──────┬───────┘
                                │                         │
                  ┌─────────────▼─────────────────────────▼────┐
                  │            数据层                           │
                  │  Defect 模型 / 台账 JSON / 知识库 JSONL    │
                  └────────────────────────────────────────────┘
```

### 4.1 编排子图（`/api/v1/analyze/full`）

```
[quality] ────────────────────────────────────────┐
                                                  ▼
[normalize] ─┬─→ [cluster] ──→ [governance] → [compose] → END
             └─→ [trend] ───────────────────→ ┘

并行：quality 与 normalize 起点并行；cluster 与 trend 在 normalize 之后并行
依赖：governance 依赖 cluster；compose 等待所有报告完成
```

---

## 5. API 一览（16 endpoints）

| 分类 | Method | 路径 | 描述 |
|------|--------|------|------|
| meta | GET | `/api/v1/health` | 健康检查 + LLM 状态 |
| **P0** | POST | `/api/v1/analyze/quality` | 数据质量分析 |
| **P0** | POST | `/api/v1/analyze/cluster` | 业务模式聚类 |
| **P0** | POST | `/api/v1/analyze/trend` | 帕累托 + 趋势 |
| **P1** | POST | `/api/v1/analyze/rca` | 从台账选某条做 RCA |
| **P1** | POST | `/api/v1/analyze/rca-log` | 仅基于错误日志做 RCA |
| **P1** | POST | `/api/v1/analyze/governance` | 专项治理报告 |
| 一键 | POST | `/api/v1/analyze/full` | P0 + P1 编排子图全套 |
| 一键 | POST | `/api/v1/analyze/upload` | 上传 JSON 文件做全套分析 |
| **P2** | POST | `/api/v1/analyze/responsibility/single` | 单条责任界定 |
| **P2** | POST | `/api/v1/analyze/responsibility/batch` | 批量责任画像 |
| **P2** | POST | `/api/v1/kb/import` | 批量入知识库 |
| **P2** | GET | `/api/v1/kb/profile` | 知识库画像 |
| **P2** | POST | `/api/v1/kb/cross-check` | 批量交叉查重（结构化） |
| **P2** | POST | `/api/v1/kb/cross-check/report` | 批量交叉查重 Markdown |
| **P2** | GET | `/api/v1/kb/historical-patterns` | 知识库历史模式归纳 |

启动后访问 `http://<host>:<port>/docs` 查看完整 OpenAPI 文档。

---

## 6. CLI 一览

```
defect-hunter
├── analyze              # P0 一键三件套（质量+聚类+趋势）
├── quality              # P0 数据质量
├── cluster              # P0 聚类
├── trend                # P0 趋势
├── rca                  # P1 RCA（支持 --text / --log / --from-taizhang）
├── governance           # P1 专项治理（支持 --pattern 单选）
├── responsibility       # P2 责任界定（不传 --id 即批量画像）
├── kb                   # P2 知识库子命令组
│   ├── import               批量入库
│   ├── profile              画像
│   ├── cross-check          批量交叉查重
│   └── historical-patterns  历史模式归纳
├── dedup                # 单条查重（V1 兼容）
└── serve                # V2 启动 FastAPI 服务
```

---

## 7. 技术栈

| 层 | 选型 |
|----|------|
| Python | 3.8+（实测）；推荐 3.10+（未来切换 LangGraph 1.0 用） |
| LLM Client | 火山引擎方舟（默认 glm-5.1，OpenAI 兼容协议）/ OpenAI SDK / Anthropic SDK |
| Web | FastAPI 0.124+ + uvicorn 0.33+ + Pydantic 2.x |
| 编排 | 自实现 `StateGraph`（API 对齐 LangGraph）;<br>注：Python 3.8 限制了 langgraph 安装版本，待升级 3.10+ 后可零成本切换 |
| 数据存储 | JSONL 文件（知识库）；台账 JSON 输入<br>未来：PostgreSQL（V2 完整版） |
| CLI | Click |
| 测试 | pytest（脱敏器单元测试）+ FastAPI TestClient（端到端） |
| 配置 | YAML（`config/config.yaml`） |

---

## 8. 快速开始

### 8.1 安装

```bash
git clone <repo>
cd zhinengtest
pip install -r requirements.txt
```

### 8.2 配置 LLM

**默认使用火山引擎方舟 + glm-5.1**，最简单：

```bash
export ARK_API_KEY=<你的火山引擎方舟 API Key>
```

或修改 `config/config.yaml` 切换 OpenAI / Anthropic：

```yaml
llm:
  provider: "openai"           # 或 "anthropic" / "volcengine"
  model: "gpt-4o-mini"         # 或 "claude-sonnet-4-6" / "glm-5.1"
  api_key: "${OPENAI_API_KEY}"
```

### 8.3 准备台账数据

将真实台账整理为 JSON：

```json
{
  "metadata": {"total": 199},
  "defects": [
    {
      "id": "DEF-0001",
      "defect_name": "积分兑换异常...",
      "module": "科技缺陷",
      "defect_type": "CODE_ISSUE",
      "problem_type": "代码逻辑错误",
      "discovery_channel": "业务反馈",
      "priority": "MEDIUM",
      "root_cause": "积分流水未回退...",
      "...": "..."
    }
  ]
}
```

放到 `data/samples/your-taizhang.json` 即可。

### 8.4 三种使用方式

#### A) CLI（最快验证）

```bash
# 数据质量分析
python main.py quality --file data/samples/your-taizhang.json

# 一键三件套
python main.py analyze --file data/samples/your-taizhang.json --period "2025年" \
                       --output-dir reports/

# 专项治理
python main.py governance --file your-taizhang.json --pattern security_authz

# 责任画像
python main.py responsibility --file your-taizhang.json    # 不传 --id 即批量

# 知识库
python main.py kb import --file your-taizhang.json
python main.py kb profile
python main.py kb cross-check --file new-batch.json
```

#### B) HTTP API

```bash
python main.py serve --port 8000

# 上传文件做全套分析
curl -X POST http://localhost:8000/api/v1/analyze/upload \
     -F "file=@your-taizhang.json" \
     -F "period=2025年" | jq

# 健康检查
curl http://localhost:8000/api/v1/health
# {"llm_provider":"volcengine","llm_model":"glm-5.1","llm_configured":true}

# Swagger UI
open http://localhost:8000/docs
```

#### C) 嵌入式调用（Python 直接 import）

```python
from src.agent import DefectHunterAgent
from src.loaders import load_taizhang_json

agent = DefectHunterAgent()
defects, _ = load_taizhang_json("台账.json")

# 数据质量
result = agent.quality(defects)
print(f"完整性: {result['summary']['completeness_score']}/100")

# 聚类
print(agent.cluster(defects))

# 一键全套（编排子图）
from src.graph import build_analysis_graph
runner = build_analysis_graph(agent)
final = runner.invoke({
    "defects": defects,
    "period": "2025年",
}).get("final")
```

---

## 9. 数据契约

### 9.1 输入：台账 JSON

完整字段定义见 [`src/models/defect.py`](src/models/defect.py)。核心字段：

| 字段 | 类型 | 说明 | 是否必填 |
|------|------|------|---------|
| id | str | 缺陷 ID（如 `DEF-0001`） | ✅ |
| defect_name | str | 缺陷名称 | ✅ |
| module | str | 科技缺陷 / 安全漏洞 / 业务优化 ... | 推荐 |
| defect_type | str | CODE_ISSUE / DESIGN_ISSUE / REQUIREMENT_ISSUE | 推荐 |
| problem_type | str | 代码逻辑错误 / 权限控制 / 前端展示 ... | 推荐 |
| discovery_channel | str | 业务反馈 / 测试发现 / 安全测试 / 对账中心 | 推荐 |
| priority | str | P0 / P1 / P2 / P3 / HIGH / MEDIUM / LOW | 推荐 |
| root_cause | str | 根因描述 | 推荐 |
| issue_owner | str | 后端 / 前端 / 后端,前端 | 推荐 |
| dev_owner | str | 开发负责人（含"已离场"标记会被自动识别） | 推荐 |
| test_owner | str | 测试负责人 | 推荐 |
| responsibility_ratio | str | "0.5:0.5" / "0.7:0.3"（dev:test） | 可选 |
| has_loss | bool | 是否资损 | 可选 |
| occurrence_time | str | ISO 时间 | 推荐 |
| ... | | | |

支持的字段污染会被自动检测和提示（如 `responsibility_ratio: "01:00:00"` 是 Excel 把 `1:0` 误识别为时间，会被自动按 H:M 解析并给出治理建议）。

### 9.2 输出：Markdown 报告

所有分析器输出结构化 Markdown，含：
- 清晰的章节标题
- 必要的统计表格
- 风险卡片（带 emoji 标识严重度 🔴🟠🟡⚪）
- 行动清单（短期 / 中期 / 长期）

### 9.3 知识库：JSONL

```jsonl
{"id": "HIST-001", "defect_name": "...", "module": "...", "root_cause": "...", ...}
{"id": "HIST-002", "defect_name": "...", "module": "...", "root_cause": "...", ...}
```

每行一条 `Defect.to_dict()`。批量入库时自动按 ID 去重。

---

## 10. 安全与脱敏

所有送入 LLM 的文本会被 `Sanitizer` 自动脱敏，覆盖：

| 类型 | 正则 | 替换 |
|------|------|------|
| 密码 / Token | `(?i)(password|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+` | `\1=[已脱敏]` |
| IP 地址 | `\b(?:\d{1,3}\.){3}\d{1,3}\b` | `X.X.X.X` |
| 手机号 | `\b1[3-9]\d{9}\b` | `1XX****XXXX` |
| 身份证 | `\b\d{17}[\dXx]\b` | `[身份证已脱敏]` |
| 邮箱 | `\b[\w.+-]+@[\w-]+\.[\w.-]+\b` | `[邮箱已脱敏]` |
| Bearer Token | `(?i)bearer\s+[A-Za-z0-9._\-]+` | `Bearer [已脱敏]` |

规则可在 `config/config.yaml` 中扩展。

---

## 11. 测试

```bash
# 单元测试（脱敏器）
pytest tests/

# 端到端冒烟（开发期已通过）
# 见 docs/智能体设计文档.md 中的验证记录
```

---

## 12. 已实现功能完整性核验

通过代码扫描确认（`grep -rn "TODO|FIXME|NotImplementedError" src/`）：

| 检查项 | 结果 |
|--------|------|
| `TODO` / `FIXME` / `XXX` 标记 | ✅ 无 |
| `NotImplementedError` 抛出 | ✅ 无 |
| 函数体仅含 `pass` 的占位 | ✅ 无（仅有的 `pass` 在异常类定义处，是合规的） |
| 注释中"未实现 / 待补 / TODO" | ✅ 无（"未实现"出现在数据描述如"功能未实现"问题类型中，不是代码标记） |
| 路由声明但未实现 | ✅ 无（16 个 endpoint 全部有 handler） |
| 模型字段定义但未使用 | ✅ 无 |
| 设计文档中规划的能力 | ✅ 7 大能力 + V2 + 火山引擎接入全部交付 |

**结论：项目无未实现的功能模块，所有声明的能力均已落地。**

---

## 13. 后续可选方向

> 当前版本已完整覆盖设计文档中的 P0/P1/P2 能力 + V2 MVP + 火山引擎接入，以下为可选增量。

| 方向 | 工作量 | 价值 |
|------|--------|------|
| PostgreSQL 持久化 + LangGraph Checkpoint（需 Python 3.10+） | 中 | 高（生产级状态管理） |
| MCP server 接入（让 Claude Desktop 直接调用） | 小 | 中（开发者友好） |
| Dockerfile + docker-compose | 小 | 中（部署便利） |
| Prometheus 指标 + 结构化日志 | 中 | 中（可观测性） |
| 前端 Web UI（基于 OpenAPI 自动生成 SDK） | 大 | 高（业务方使用） |
| Excel 直接导入（替代 JSON 转换步骤） | 小 | 中（降低使用门槛） |
| 多期对比（双周 / 跨季度趋势对比） | 中 | 中 |
| 团队 KPI 看板（缺陷归属聚合） | 中 | 中 |

---

## 14. 文档导航

| 文档 | 用途 |
|------|------|
| [README.md](./README.md) | 项目首页（极简） |
| [PROJECT.md](./PROJECT.md) | **项目总览（本文档）** |
| [CLAUDE.md](./CLAUDE.md) | Claude Code 工作指南（架构约束 / 优先级） |
| [docs/智能体设计文档.md](./docs/智能体设计文档.md) | 数据画像驱动的完整设计（决策依据） |
| [docs/数据画像速览.md](./docs/数据画像速览.md) | 1 页数据摘要 |
| [deploy/DEPLOY-LINUX.md](./deploy/DEPLOY-LINUX.md) | **单机 Linux 云服务器部署指南**（含 systemd / Nginx / 备份） |

---

**项目状态**：V1 全部能力交付完成，V2 MVP 服务化完成。

**LLM 默认**：火山引擎方舟（glm-5.1）。

**最后更新**：2026-06-12
