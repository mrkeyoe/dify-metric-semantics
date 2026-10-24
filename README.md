# dify-metric-semantics

> Dify Tool Plugin：**语义指标注册 + 口径冲突检测 + 口径仲裁**。
> 让 Agent 在写 Text2SQL 之前先确认「这个指标到底按哪个口径算」，并把执行结果回写成置信度。

同一个业务指标在不同团队/数据源里往往有不同定义——"活跃用户"可能是 7 日登录，也可能是 30 日下单。
Agent 直接猜一个去写 SQL，结果不可比、不可信，而且错了也没人知道。

本插件把这件事拆成三个可组合的动作：

| 工具 | 作用 | 是否调用 LLM | 是否执行 SQL |
| --- | --- | --- | --- |
| `resolve_metric` | 按用户问题里的指标名/别名召回所有已注册口径，标注是否冲突 | 否 | 否 |
| `arbitrate` | 让模型判断冲突口径的语义等价性，输出结构化决策 | 是（可降级） | 否 |
| `record_outcome` | 回写实际使用的口径 + 成功/失败信号，按 EMA 调整 confidence | 否 | 否 |
| `list_metrics` | 列出/检索已注册指标（辅助发现） | 否 | 否 |

本插件**不执行 SQL**，只输出口径决策信号；SQL 由你的 Text2SQL 工具生成。

---

## 工具 Schema

### 1. `resolve_metric`

输入：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 用户原始问题（含指标提法），如"上个月活跃用户数" |
| `metric_hint` | string | 否 | 进一步限定指标（名称/别名），用于缩小候选 |
| `top_k` | number | 否 | 最多候选数，默认 5，范围 1–20 |

输出（JSON，节选）：

```json
{
  "ok": true,
  "query": "上个月活跃用户数",
  "matched": true,
  "conflict": true,
  "candidate_count": 2,
  "candidates": [
    {
      "id": "active_user.7d_login",
      "name": "活跃用户",
      "definition_text": "过去 7 天内至少登录过一次的去重用户数（按 user_id 去重，只统计登录行为）",
      "aliases": ["活跃用户", "日活", "DAU", "active user"],
      "source_tables": ["dwd.user_login_log"],
      "sql_fragment": "SELECT COUNT(DISTINCT user_id) FROM dwd.user_login_log WHERE login_time >= CURRENT_DATE - INTERVAL '7 days'",
      "owner": "增长组",
      "confidence": 0.6,
      "matched_alias": "活跃用户",
      "match_kind": "name_substring",
      "match_score": 0.5
    }
  ],
  "conflicts": ["definition_distance:active_user.7d_login~active_user.30d_order=0.902>0.35"],
  "max_semantic_distance": 0.902386,
  "threshold": 0.35,
  "summary": "conflict detected across [active_user.7d_login, active_user.30d_order]",
  "next_action": "arbitrate",
  "llm_available": true
}
```

`conflict=true` 时，Agent 应先调用 `arbitrate`，不要直接生成 SQL。

### 2. `arbitrate`

输入（三选一提供候选来源）：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `candidate_ids` | string | 否 | 逗号分隔的口径 ID（来自 `resolve_metric`） |
| `candidates_json` | string | 否 | 候选对象数组的 JSON 字符串 |
| `query` | string | 否 | 原问题；没有候选时用它重新解析 |
| `context` | string | 否 | 业务上下文（统计周期、归属团队、历史结论） |
| `confidence_threshold` | number | 否 | 低于该置信度强制人工确认，默认 0.6 |

输出：

```json
{
  "ok": true,
  "resolved": true,
  "chosen_id": "active_user.30d_order",
  "chosen_name": "活跃用户",
  "rationale": "问题问的是“上个月”的活跃用户，30 日下单口径的时间窗口与之一致……",
  "requires_human": false,
  "confidence": 0.78,
  "strategy": "llm_arbitration",
  "llm": "dify-session-model:gpt-4o-mini",
  "conflict": true,
  "prompt_version": "arbitrate-v1",
  "fallback_order": ["active_user.30d_order", "active_user.7d_login"],
  "candidate_count": 2,
  "candidate_source": "candidate_ids",
  "next_action": "record_outcome"
}
```

决策规则：

- `resolved = chosen_id 非空 且 requires_human=false`；
- 模型置信度低于阈值（或未给出置信度）→ `requires_human=true`；
- 模型不可用 / 输出不可解析 / 选择了不存在的 ID / 拒绝选择 → **降级但不崩**，
  返回按 confidence 排序的 `fallback_order` 与 `strategy`（如 `llm_unavailable`），`requires_human=true`。

### 3. `record_outcome`

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `metric_id` | string | 是 | 实际使用的口径 ID |
| `outcome` | select | 否 | `success` / `failure`（二选一，或改用 `signal`） |
| `signal` | number | 否 | 0–1 的数值信号 |
| `note` | string | 否 | 备注（失败原因等），写入审计历史 |

输出含 `old_confidence`、`new_confidence`、`delta`、`signal`、`alpha`、`successes`、`failures`。

### 4. `list_metrics`

`query`（关键词过滤）+ `limit`（1–100），返回指标列表与存储健康信息。

---

## 快速开始

### 本地跑测试（无需 Dify SDK）

```bash
python -m unittest discover -s tests -v
```

要求 Python ≥ 3.11；`core/` 只用标准库，测试也只用 `unittest`。

### 本地离线 CLI（不需要 Dify，也不需要真实模型）

```bash
# 注册示例口径（活跃用户 7 日登录 / 30 日下单、GMV）
python -m core seed

# 冲突检测
python -m core resolve "上个月活跃用户数"

# 用内置 stub 响应演示仲裁（不联网）
python -m core arbitrate "上个月活跃用户数" --stub

# 回写一次失败，观察 confidence 下降
python -m core record active_user.7d_login --outcome failure --note "字段缺失"
python -m core list
```

默认数据文件是 `data/metrics.json`，可用 `--store <path>` 或环境变量 `METRIC_SEMANTICS_STORE` 覆盖。

### 在 Dify 中使用

1. 将本目录打包为 Dify 插件包（`manifest.yaml` 位于根目录），安装到 Dify。
2. 在插件 Provider 凭据页可选填写：
   - `store_path`：JSON 数据文件路径（建议指向持久卷）；
   - `model_api_base` / `model_api_key` / `model_name`：OpenAI 兼容接口；
   - 或留空，使用 Dify 自身的模型会话网关（`session.model.llm`）。
3. 在 Agent 的提示词/工作流中按顺序使用：
   `resolve_metric` →（`conflict=true` 时）`arbitrate` → 生成 SQL → 执行 → `record_outcome`。

---

## 设计取舍

### 为什么用 JSON 文件而不是 SQLite

- 数据集很小（几十到几百条口径），不需要索引和事务隔离；
- JSON 可人工 review、可 diff、可直接进 Git，符合"口径是需要评审的业务资产"这一事实；
- 零编译依赖，容器里不需要 sqlite3 之外的东西（虽然标准库自带 sqlite3，但文件更透明）；
- 写入用 **临时文件 + fsync + `os.replace`** 保证原子性，跨进程用 `*.lock` 文件（含陈旧锁回收）。

**代价**：并发写能力弱（同一时刻只有一个进程能持锁），不适合高频写入；它定位是"口径注册表"，不是数据库。

### 冲突判定

对每一对候选：

1. 两边都有 `sql_fragment` 且规范化后**不等价** → 冲突（`sql_fragment_mismatch`）；
2. 口径文本的启发式语义距离 **> 阈值**（默认 0.35）→ 冲突（`definition_distance`）；
3. 数字口径不一致（如 7 天 vs 30 天）→ 距离加惩罚项，通常直接越过阈值（`numeric_scope_mismatch`）。

`source_tables` 不相交只作为提示（`notes`），不算冲突：同一口径可以读不同的表。

语义距离是**启发式**：60% 字符 bigram Jaccard + 40% 词元 Jaccard + 数字口径惩罚。
不用 embedding 的原因：离线可测、零依赖、结果可解释、阈值可调；代价是近义改写可能漏判——
所以真正定论交给 `arbitrate` 的 LLM 判断，启发式只负责"要不要拦截"。

### 为什么仲裁一定要 LLM

"7 日登录"和"30 日下单"在字符层面距离很大，但在业务上可能都叫"活跃用户"；
反过来，"登录用户数"和"登录用户数（去重）"字符距离很小，口径却不同。
规则判不了语义等价性，所以：

- `arbitrate` 用结构化 prompt 要求模型输出**单个 JSON 对象**（`equivalent`、`chosen_id`、`rationale`、`confidence`、`requires_human`、`conflicts`）；
- 解析器容忍 code fence、前后散文、字符串里的花括号；
- 模型给了 0–100 的置信度会自动折算成 0–1；
- 任何异常都走降级路径，返回 `requires_human=true`，绝不编造一个"已定论"。

### 学习闭环（EMA）

```
new_confidence = (1 - alpha) * old_confidence + alpha * signal
signal: 成功 = 1.0，失败 = 0.0（也可传 0–1 的连续信号）
alpha 默认 0.2，结果裁剪到 [0.01, 0.99]
```

只做指数移动平均，不做训练；每次回写都记录 `old/new/signal/alpha/note/ts` 审计历史。
默认参数下，0.60 的口径失败一次降到 0.48——`resolve_metric` 下次就会返回新的置信度。

### 凭据处理

- **不硬编码任何 API Key**；
- 优先使用 Provider 凭据（`model_api_base` + `model_api_key` + `model_name`）走 OpenAI 兼容接口；
- 未配置时使用 Dify 运行时注入的模型会话网关（`session.model.llm`）；
- 两者都没有 → 仲裁降级为"需人工确认"。

### 分层

```
core/        纯标准库：模型、匹配、冲突、prompt、解析、存储、EMA、服务、工具处理器
adapters/    仅此处接触 dify_plugin（lazy import）；SDK 缺失时抛 LLMUnavailable
tools/       Dify Tool 薄壳：参数交给 core.tool_handlers，结果转 Dify 消息
provider/    Dify Provider 凭据校验
main.py      Dify 插件入口
```

因此 `python -m unittest` 在**没有 dify_plugin 的机器上也能全绿**。

---

## 目录结构

```
core/                 # 纯标准库核心（可独立 import / 测试）
  models.py           # MetricDefinition / Candidate / ConflictReport / ResolveResult ...
  matching.py         # 名称与别名召回
  conflict.py         # 两两冲突检测
  prompts.py          # 结构化 prompt + 容错 JSON 解析
  arbitration.py      # 仲裁编排与降级策略
  feedback.py         # EMA
  storage.py          # JSON 存储（原子写 + 锁 + 损坏降级）
  service.py          # 对外服务门面
  tool_handlers.py    # Dify 工具的 dict-in/dict-out 处理器
  seeds.py            # 示例口径
  __main__.py         # 离线 CLI
adapters/dify_model.py  # Dify 模型会话 → LLMClient 协议
tools/*.py|yaml         # 4 个 Dify Tool
provider/*.py|yaml      # Dify Provider
main.py, manifest.yaml, _assets/icon.svg
tests/                  # 155 个 unittest
```

---

## 测试与验收

验收标准对应的测试：

| 验收标准 | 测试 |
| --- | --- |
| 注册"活跃用户"两个口径；query 返回冲突信号；arbitrate 输出 chosen + rationale | `tests/test_conflict.py::...test_acceptance_two_active_user_calibers_conflict`、`tests/test_resolve.py::...test_acceptance_query_returns_conflict_signal`、`tests/test_arbitration.py::...test_acceptance_arbitrate_returns_chosen_and_rationale` |
| 回写一次"口径 A 执行失败"后，再次 resolve 时 A 的 confidence 下降 | `tests/test_feedback.py::...test_acceptance_failed_execution_lowers_confidence_in_next_resolve` |

覆盖范围还包括：SQL 规范化等价性、语义距离惩罚、JSON 存储原子写/重载/损坏降级/锁恢复、
prompt 解析容错（code fence、百分比置信度、中文布尔值）、仲裁全部降级路径、工具处理器错误映射、CLI 端到端。

```
$ python -m unittest discover -s tests -v
...
Ran 155 tests in 0.3s

OK
```

---

## 已知限制与诚实声明

1. **Dify 运行时集成未在本机验证。** 本机没有安装 `dify_plugin` SDK，也没有可用的 Dify 实例。
   `core/` 的核心逻辑有完整单元测试；`main.py`、`tools/`、`provider/`、`manifest.yaml` 是按公开的
   Dify 插件目录结构编写的，**未经过真实打包与运行验证**。
2. **待核对项（需要对照 Dify 官方插件模板确认）：**
   - `manifest.yaml` 中 `meta.runner`、`resource.permission.tool`、`privacy` 字段的具体要求；
   - icon 资源路径（本仓库放在 `_assets/icon.svg`，manifest/provider 中写作 `icon.svg`）；
   - `tools/*.yaml` 中 `select` 参数的 `options[].label` 结构；
   - `adapters/dify_model.py` 对 `session.model.llm.invoke` 的调用形状（不同 SDK 版本可能是
     `ModelRequest` 对象或关键字参数）；适配器已做兼容尝试，但未在真机验证。
3. **语义距离是启发式**，不是 embedding；近义改写可能漏判，最终以 `arbitrate` 的 LLM 判断为准。
4. **JSON 存储不是数据库**：并发写能力弱，单文件适合小规模口径注册表；生产环境请把 `store_path`
   指向持久卷，否则容器重启后内存回退的数据会丢失。
5. **没有模型就没有自动仲裁**：未配置模型凭据且 Dify 会话网关不可用时，`arbitrate` 返回
   `requires_human=true` 与排序后的 `fallback_order`，这是有意的安全默认值。
6. **不执行 SQL、不做权限校验**：插件只输出决策信号；数据权限、SQL 安全由你的查询执行层负责。

---

## License

MIT © 2026 mrkeyoe. 见 [LICENSE](LICENSE)。

仓库地址：<https://github.com/mrkeyoe/dify-metric-semantics>