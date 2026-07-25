# 开发文档

本 Wiki 面向 Daily Intelligence 的维护者和贡献者，描述当前代码契约、模块边界、状态模型和扩展方式。用户安装、运行和真实 HTML 示例见仓库根目录的 `README.md`；操作手册与平台配置见 `references/`。

## 当前系统一览

| 能力 | 当前实现 |
| --- | --- |
| 来源 | 32 个核心来源 + 51 个发现来源 |
| 常驻监控 | RSS/Atom、条件缓存、静态 HTML、来源健康和跨来源聚类，`token_usage: 0` |
| 正式日报 | `morning`、`evening` 两个 edition；`zh-CN`、`en` 两种输出语言；七个固定内容栏目 |
| 写作协议 | schema 2.0；Brief 分批写作，主模型只处理紧凑分析包 |
| 研判 | 地缘政治、AI 研究/工程、股票市场 + 跨视角综合 |
| 前台交付 | JSON、Markdown、HTML、本地索引和桌面 HTML |
| 后台收尾 | PDF、可选 Notion、独立 Evaluation |
| 事实源 | 不可变 JSON/Markdown；HTML/PDF/Notion 为可重建投影 |

## 文档地图

| 页面 | 回答的问题 |
| --- | --- |
| [产品目标与边界](01-产品目标与边界) | 系统必须做什么、明确不做什么 |
| [总体架构](02-总体架构) | 监控、写作、Artifact 与发布层如何分工 |
| [数据与状态模型](03-数据与状态模型) | Source、Content、Run、Authoring、Report、Tail 的状态是什么 |
| [端到端流程](04-端到端流程) | 一版日报从监控快照到桌面 HTML 和后台评估如何运行 |
| [设计标准](05-设计标准) | 新代码必须遵守哪些实现和兼容约束 |
| [可靠性与安全](06-可靠性与安全) | 失败、并发、外部内容、浏览器和凭证如何处理 |
| [扩展开发](07-扩展开发) | 如何增加来源、Adapter、栏目、字段和 Publisher |
| [测试与发布](08-测试运维与演进) | 变更应补哪些测试，如何冒烟和发布 |
| [依赖与配置](09-依赖配置与注入) | 配置、环境变量和路径优先级如何解析 |
| [核心算法](10-核心算法与跨模块调用) | URL、聚类、Context、写作装配、校验和投影的调用链 |

## 仓库目录

```text
daily-intelligence-skill/
├─ SKILL.md                 # Hermes 的精简过程入口
├─ README.md                # 用户能力、安装和真实示例
├─ configs/                 # 核心来源、发现来源、Notion 映射
├─ schemas/                 # 报告 JSON Schema
├─ templates/               # 模型写作契约
├─ references/              # 运行、安装、编辑和发布细节
├─ assets/monitor/          # 本地情报台静态资源
├─ assets/readme/           # README 使用的稳定静态图片
├─ examples/reports/        # 去除本机路径的实际 HTML 示例
├─ src/daily_intelligence/  # Python 实现
├─ tests/                   # 回归与集成测试
└─ wiki/                    # 本开发文档
```

## 代码索引

| 领域 | 主要模块 |
| --- | --- |
| 配置、语言、路径和绑定 | `config.py`、`localization.py`、`runtime.py` |
| 采集与访问状态 | `access.py`、`adapters.py`、`prefetch.py`、`collector.py` |
| 零 Token 监控 | `feeds.py`、`monitor.py`、`clustering.py`、`dashboard.py` |
| 正文与图片 | `content.py`、`media.py` |
| Context 与写作 | `context.py`、`authoring.py` |
| 编译与事实校验 | `reporting.py`、`taxonomy.py` |
| 持久化与投影 | `reports.py`、`local_output.py`、`storage.py` |
| 连续性 | `semantics.py`、`state.py` |
| Notion | `notion.py` |
| 编排与 CLI | `workflow.py`、`cli.py` |

## 权威来源顺序

实现与文档不一致时，按以下顺序判断：

1. `schemas/report.schema.json`、状态枚举和实际校验代码；
2. 自动测试；
3. `templates/report-contract.md` 与 `SKILL.md`；
4. `references/` 和本 Wiki；
5. `README.md` 与 Release Notes。

发现不一致时应在同一变更中修正文档和测试，不把过时说明保留为“兼容解释”。

## 开发入口

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
python -m compileall -q src tests
daily-intel --help
```

运行真实任务前先阅读 [可靠性与安全](06-可靠性与安全)，尤其是数据根、浏览器 Profile、运行时 `data/` 和认证内容的边界。
