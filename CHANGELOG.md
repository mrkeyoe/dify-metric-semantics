# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-24

### Added

- 语义指标注册表：每个口径保存 `name`、`aliases`、`definition_text`、`source_tables`、
  `sql_fragment`、`owner`、`confidence`，JSON 持久化（原子写 + 跨进程锁）。
- `resolve_metric` 工具：按名称/别名召回候选口径，输出 `conflict` 标记与冲突原因。
- 冲突检测：SQL 规范化不等价、口径文本语义距离超阈值、数字口径不一致三类判据；
  表不相交仅作为提示。
- `arbitrate` 工具：结构化 prompt 要求 LLM 输出 JSON，解析容错（code fence、前后散文、
  百分比置信度、中文布尔值），置信度低于阈值时 `requires_human=true`；模型不可用/输出
  不可解析/选择非法时降级为排序建议。
- `record_outcome` 工具：按 EMA（默认 alpha=0.2）更新 confidence，记录审计历史。
- `list_metrics` 工具：指标发现与存储健康检查。
- Dify 插件外壳：`manifest.yaml`、`provider/`、`tools/`、`main.py`、
  `adapters/dify_model.py`（lazy import，SDK 缺失时优雅降级）。
- 离线 CLI（`python -m core`）：seed / list / resolve / arbitrate --stub / record / health。
- 155 个标准库 `unittest` 测试，覆盖两条验收标准、冲突检测、EMA 回写、存储健壮性、
  prompt 解析与工具错误映射；GitHub Actions 在 Python 3.11 / 3.12 上运行。

### Known limitations

- Dify 运行时集成（插件打包、Provider 凭据页、模型会话网关）未在本机验证，见 README。
- 语义距离为启发式实现，不使用 embedding。
- JSON 存储定位为小规模口径注册表，并发写能力有限。

[Unreleased]: https://github.com/mrkeyoe/dify-metric-semantics/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mrkeyoe/dify-metric-semantics/releases/tag/v0.1.0