# 隐私说明 / Privacy

本插件是自托管工具，不包含遥测、不向作者或任何第三方回传数据。

- **指标口径数据**：保存在用户自行指定的 JSON 文件（`store_path`），或插件容器内的 `./data/metrics.json`。数据不离开用户环境。
- **模型调用**：`arbitrate` 需要一次 LLM 调用。调用走用户配置的模型凭据（`model_api_base` / `model_api_key` / `model_name`），或走 Dify 自身的模型会话网关；请求内容包含用户问题与已注册口径的文本/SQL 片段。除此之外不发送任何数据。
- **日志**：插件只返回结构化 JSON 结果，不主动落盘对话内容；Dify 平台的运行日志由用户自行管理。
- **API Key**：插件不硬编码任何密钥，也不持久化密钥；密钥仅存在于 Dify 的 provider 配置中。

This plugin ships no telemetry. Metric definitions stay in the store file you
configure; the only outbound request is the optional arbitration call to the
model endpoint you configure yourself (or Dify's own model gateway). No API keys
are hard-coded or persisted by this plugin.