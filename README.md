# 生产缺陷神探 (Defect Hunter)

> 基于 LLM 的生产缺陷智能分析高码智能体

## 项目简介

**生产缺陷神探** 是一款面向生产环境缺陷的深度分析智能体，提供从单缺陷根因定位到批量缺陷趋势研判的全链路分析能力。

## 核心能力

| 能力 | 说明 | 模块 |
|------|------|------|
| 🔍 RCA 根因分析 | 基于 5Why 法与故障树定位根本原因 | `rca_analyzer` |
| 🧩 相似缺陷归纳 | 多维度聚类（模块/错误类型/根因/时间） | `cluster_analyzer` |
| ⚖️ 责任界定 | 客观分析引入阶段与漏测原因 | `responsibility_analyzer` |
| 📚 知识库查重 | 识别新问题/相似/复发问题 | `dedup_analyzer` |
| 📊 汇总与趋势 | 帕累托分析、风险研判、改进策略 | `trend_analyzer` |

## 项目结构

```
zhinengtest/
├── README.md                  # 项目说明
├── requirements.txt           # Python 依赖
├── config/
│   └── config.yaml            # 智能体配置
├── src/
│   ├── agent.py               # 智能体主入口（路由分发）
│   ├── analyzers/             # 五大分析能力实现
│   │   ├── rca_analyzer.py
│   │   ├── cluster_analyzer.py
│   │   ├── responsibility_analyzer.py
│   │   ├── dedup_analyzer.py
│   │   └── trend_analyzer.py
│   ├── models/
│   │   └── defect.py          # 缺陷数据模型
│   ├── prompts/               # Prompt 模板
│   │   └── templates.py
│   └── utils/
│       ├── sanitizer.py       # 敏感信息脱敏
│       ├── formatter.py       # Markdown 输出格式化
│       └── llm_client.py      # LLM 调用封装
├── data/
│   ├── knowledge_base/        # 历史缺陷知识库
│   │   └── defects.jsonl
│   └── samples/               # 示例数据
│       ├── sample_log.txt
│       └── sample_defects.csv
├── tests/                     # 单元测试
└── main.py                    # CLI 入口
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 LLM（默认使用火山引擎方舟 + glm-5.1）
export ARK_API_KEY=<你的火山引擎方舟 API Key>

# 3. 运行示例
python main.py rca --log data/samples/sample_log.txt
python main.py cluster --file data/samples/sample_defects.csv
python main.py trend --file data/samples/sample_defects.csv
```

## LLM Provider

默认 **火山引擎方舟（glm-5.1）**。修改 `config/config.yaml` 即可切换：

```yaml
llm:
  provider: "volcengine"          # volcengine / openai / anthropic
  model: "glm-5.1"                # 或 endpoint ID（如 ep-xxxxxx）
  api_key: "${ARK_API_KEY}"
```

| Provider | base_url | 环境变量 | 推荐场景 |
|----------|----------|---------|----------|
| volcengine ⭐ | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` | 国内合规 / 默认 |
| openai | （官方） | `OPENAI_API_KEY` | 海外 / 兼容网关 |
| anthropic | （官方） | `ANTHROPIC_API_KEY` | Claude 系列 |

## 使用示例

### 场景一：单条报错日志的 RCA 分析

```bash
python main.py rca --text "NullPointerException at OrderService.createOrder(OrderService.java:45)"
```

### 场景二：批量缺陷归类

```bash
python main.py cluster --file weekly_defects.csv
```

### 场景三：生成周报（趋势分析）

```bash
python main.py trend --file weekly_defects.csv --output weekly_report.md
```

## 数据安全

- ✅ 自动脱敏密码、密钥、Token、IP 地址
- ✅ 输出报告不泄露真实用户信息
- ✅ 本地知识库存储，不向外部上传敏感日志

## 设计原则

1. **事实导向**：所有结论必须有日志或数据支撑
2. **5Why 深挖**：不满足表面原因，追溯到根本原因
3. **客观中立**：责任分析不带情绪化表达
4. **建设性**：指出问题的同时给出改进建议

## License

MIT
