# 示例

[简体中文](README.md) | [English](README.en.md)

本目录从两个互补角度展示产品。

## 报告展厅

以下 HTML 存档展示产品在实际信息规模下的阅读体验：

| 版本 | 采集规模 | 编辑结果 |
| --- | ---: | ---: |
| [2026-07-24 晨报 r3](reports/2026-07-24-morning-r3.html) | 24 个来源 · 197 条更新 | 10 个重点事件 |
| [2026-07-25 晨报 r1](reports/2026-07-25-morning-r1.html) | 29 个来源 · 235 条更新 | 10 个重点事件 |

它们是保留的 schema 1.5 报告，因此仍使用早期 **Daily Intelligence** 抬头。当前
schema 2.0 版本已增加跨视角综合，并使用 **迹简情报台 · SignalTrail** 品牌。下载 HTML 后在本地
浏览器打开，即可查看完整交互阅读体验。

## 合成测试数据

`sample_input.json` 与 `sample_report.json` 用于自动化测试、Schema 校验和输出渲染。

- 人物、机构、事件、日期与分析均为合成内容。
- `news.example`、`wire.example` 是保留示例域名，不对应真实媒体。
- 测试数据是工程输入，不是事实来源或编辑模板。
- 修改 `sample_report.json` 后，需要运行报告、架构与 Skill 测试。
