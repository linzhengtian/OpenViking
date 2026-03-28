# OpenViking MCP Server

MCP（Model Context Protocol）HTTP 服务器，将 OpenViking RAG 能力作为工具暴露。

## 工具

| 工具 | 说明 |
|------|------|
| `query` | 完整 RAG 流程 — 搜索 + LLM 答案生成 |
| `search` | 仅语义搜索，返回匹配文档及相似度分数 |
| `add_resource` | 添加文件、目录或 URL 到数据库 |

## 快速开始

```bash
# 设置配置
cp ov.conf.example ov.conf
# 编辑 ov.conf，填入你的 API Key

# 安装依赖
uv sync

# 启动服务器（端口 2033 的可流式 HTTP）
uv run server.py
```

服务器将在 `http://127.0.0.1:2033/mcp` 可用。

## 两种运行模式

### Local 模式（默认）

服务器读取本地配置文件，直接访问本地数据库。

```bash
uv run server.py --config ./ov.conf --data ./data
```

### HTTP 模式（连接远程 OpenViking 服务器）

传入 `--ov-url` 启用 HTTP 模式 — MCP 服务器作为代理，将所有 RAG 操作转发到远程 OpenViking 服务器。

```bash
uv run server.py \
  --ov-url https://openviking.example.com \
  --api-key sk-xxx \
  --agent-id my-agent
```

远程服务器需暴露 OpenViking HTTP API。认证通过 `X-API-Key` header，agent/user 隔离通过 `X-OpenViking-Agent` 和 `X-User` header。

## 工具参数说明

### `query` — 完整 RAG 流程

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `question` | string | 必填 | 要提问的问题 |
| `top_k` | int | 5 | 用于构建上下文的搜索结果数量（1–20）|
| `temperature` | float | 0.7 | LLM 采样温度（0.0–1.0）|
| `max_tokens` | int | 2048 | 响应最大 token 数 |
| `score_threshold` | float | 0.2 | 搜索结果最低相似度分数（0.0–1.0）|
| `system_prompt` | string | "" | 可选的系统提示词，引导 LLM 回复风格 |

**响应格式：** 文本答案 + 来源列表 + 时间统计：

```
这是答案...

---
Sources:
  1. file.md (relevance: 0.8234)
  2. doc.pdf (relevance: 0.7512)

[search: 0.12s, llm: 1.45s, total: 1.57s]
```

### `search` — 仅语义搜索

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | string | 必填 | 搜索查询语句 |
| `top_k` | int | 5 | 返回结果数量（1–20）|
| `score_threshold` | float | 0.2 | 最低相似度分数（0.0–1.0）|

**响应格式：**

```
Found 2 results:

[1] viking://resources/user/file.md (score: 0.8234)
匹配文档的前 500 个字符...

[2] viking://resources/user/doc.pdf (score: 0.7512)
...
```

### `add_resource` — 导入文档

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resource_path` | string | 必填 | 本地文件/目录路径，或要添加的 URL |

支持格式：PDF、Markdown、Text、HTML 等。URL 会自动下载并处理。

**响应：** `"Resource added and indexed: viking://resources/user/..."`

## HTTP 端点

```
http://127.0.0.1:2033/mcp   (streamable-http，默认)
                              （使用 --transport stdio 时走 stdio 协议）
```

> 注意：Windows 用户从 Claude CLI 连接时，应使用 `127.0.0.1` 而非 `localhost`。

## 从 Claude 连接

```bash
# 在 Claude CLI 中添加 MCP 服务器
claude mcp add --transport http openviking http://127.0.0.1:2033/mcp
```

或添加到 `.mcp.json`：

```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "http://127.0.0.1:2033/mcp"
    }
  }
}
```

## 全部选项

```
uv run server.py [OPTIONS]

  --config PATH         配置文件路径（默认：./ov.conf，环境变量：OV_CONFIG）
  --data PATH           数据目录路径（默认：./data，环境变量：OV_DATA）
  --host HOST           绑定地址（默认：127.0.0.1）
  --port PORT           监听端口（默认：2033，环境变量：OV_PORT）
  --transport TYPE      streamable-http | stdio（默认：streamable-http）

  # HTTP 模式（连接远程 OpenViking 服务器）
  --ov-url URL          远程 OpenViking 服务器地址（环境变量：OV_URL）
  --api-key KEY         API Key 认证（环境变量：OV_API_KEY）
  --agent-id ID         默认 agent ID，用于资源隔离（环境变量：OV_AGENT_ID）
  --user USER           默认用户，用于资源隔离（环境变量：OV_USER）
  --default-uri URI     搜索时默认目标 URI（环境变量：OV_DEFAULT_URI）

  # 调试
  --debug               启用调试日志（环境变量：OV_DEBUG=1）
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `OV_CONFIG` | 配置文件路径 |
| `OV_DATA` | 数据目录路径 |
| `OV_PORT` | 服务器监听端口 |
| `OV_URL` | 远程 OpenViking 服务器地址（设置后启用 HTTP 模式）|
| `OV_API_KEY` | 远程服务器认证 API Key |
| `OV_AGENT_ID` | 默认 agent ID |
| `OV_USER` | 默认用户，用于资源隔离 |
| `OV_DEFAULT_URI` | 搜索默认目标 URI |
| `OV_DEBUG` | 设为 `1` 启用调试日志 |

## 使用 MCP Inspector 测试

```bash
npx @modelcontextprotocol/inspector
# 连接到 http://127.0.0.1:2033/mcp
```