# 开发文档

本 Wiki 面向 Daily Intelligence 的维护者和贡献者。用户安装、运行和 Notion 配置见仓库根目录的 `README.md` 与 `references/`。

## 开发入口

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
python -m compileall -q src tests
daily-intel --help
```

## 文档索引

| 页面 | 内容 |
| --- | --- |
| [产品目标与边界](01-产品目标与边界) | 功能契约、非目标、兼容要求 |
| [总体架构](02-总体架构) | 模块职责和依赖方向 |
| [数据与状态模型](03-数据与状态模型) | 核心实体、状态枚举、Artifact 布局 |
| [端到端流程](04-端到端流程) | Prepare、Enrich、Finalize、Evaluation |
| [设计标准](05-设计标准) | 实现约束和变更要求 |
| [可靠性与安全](06-可靠性与安全) | 故障处理、原子写、凭证与外部内容边界 |
| [扩展开发](07-扩展开发) | 来源、Adapter、字段和 Publisher 扩展 |
| [测试、运维与演进](08-测试运维与演进) | 测试分层、发布检查、故障定位 |
| [依赖、配置与注入](09-依赖配置与注入) | 配置来源、优先级和运行时路径 |
| [核心算法与跨模块调用](10-核心算法与跨模块调用) | 规范化、筛选、编译、校验和发布算法 |

## 目录

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | Hermes 执行流程 |
| `configs/` | 来源、输出和 Notion schema 映射 |
| `schemas/` | 报告 JSON Schema |
| `templates/` | Agent 写作输入契约 |
| `src/daily_intelligence/` | Python 实现 |
| `tests/` | 单元、兼容、架构和发布测试 |
| `references/` | 用户配置、运行和编辑规则 |
| `examples/` | 合成测试样例 |

## 代码索引

| 模块 | 职责 |
| --- | --- |
| `cli.py` | 参数解析和命令分发 |
| `config.py`、`runtime.py` | 配置解析、资源定位、数据根校验 |
| `collector.py`、`prefetch.py`、`adapters.py` | 来源采集和候选提取 |
| `verification.py` | 人工验证队列和验证结果合并 |
| `content.py` | 精选正文提取 |
| `context.py`、`semantics.py` | 写作上下文、批次和语义缓存 |
| `workflow.py` | Edition 状态机和阶段编排 |
| `reporting.py` | 草稿编译、证据注入和校验 |
| `reports.py`、`local_output.py` | 报告保存和本地输出 |
| `notion.py` | Notion schema 校验、发布和反馈同步 |
| `state.py`、`storage.py` | 连续状态、Revision、原子写和锁 |

## 权威来源

发生冲突时按以下顺序确认行为：

1. `schemas/report.schema.json` 和 Python 校验器；
2. 当前代码与测试；
3. `SKILL.md` 和 `templates/report-contract.md`；
4. `references/`；
5. Wiki 与 README。

行为变化必须同时更新测试和相关文档。
