# OpenViking MCP Server

MCP (Model Context Protocol) HTTP server that exposes OpenViking RAG capabilities as tools.

## Tools

| Tool | Description |
|------|-------------|
| `query` | Full RAG pipeline — search + LLM answer generation |
| `search` | Semantic search only, returns matching documents with scores |
| `add_resource` | Add files, directories, or URLs to the database |

## Quick Start

```bash
# Setup config
cp ov.conf.example ov.conf
# Edit ov.conf with your API keys

# Install dependencies
uv sync

# Start the server (streamable HTTP on port 2033)
uv run server.py
```

The server will be available at `http://127.0.0.1:2033/mcp`.

## Two Running Modes

### Local Mode (default)

The server reads a local config file and accesses the database directly.

```bash
uv run server.py --config ./ov.conf --data ./data
```

### HTTP Mode (connect to remote OpenViking server)

Pass `--ov-url` to enable HTTP mode — the MCP server acts as a proxy, forwarding all RAG operations to a remote OpenViking server.

```bash
uv run server.py \
  --ov-url https://openviking.example.com \
  --api-key sk-xxx \
  --agent-id my-agent
```

The remote server must expose an OpenViking HTTP API. Authenticated via `X-API-Key` header; agent/user scoping is via `X-OpenViking-Agent` and `X-User`.

## Tool Parameters

### `query` — Full RAG pipeline

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `question` | string | required | The question to ask |
| `top_k` | int | 5 | Number of search results to use as context (1–20) |
| `temperature` | float | 0.7 | LLM sampling temperature (0.0–1.0) |
| `max_tokens` | int | 2048 | Maximum tokens in the response |
| `score_threshold` | float | 0.2 | Minimum relevance score for search (0.0–1.0) |
| `system_prompt` | string | "" | Optional system prompt to guide LLM response style |

**Response:** Text answer followed by source list and timing breakdown:

```
The answer is...

---
Sources:
  1. file.md (relevance: 0.8234)
  2. doc.pdf (relevance: 0.7512)

[search: 0.12s, llm: 1.45s, total: 1.57s]
```

### `search` — Semantic search only

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | The search query |
| `top_k` | int | 5 | Number of results to return (1–20) |
| `score_threshold` | float | 0.2 | Minimum relevance score (0.0–1.0) |

**Response:**

```
Found 2 results:

[1] viking://resources/user/file.md (score: 0.8234)
First 500 characters of matched content...

[2] viking://resources/user/doc.pdf (score: 0.7512)
...
```

### `add_resource` — Ingest documents

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resource_path` | string | required | Path to a local file/directory, or a URL to add |

Supports: PDF, Markdown, Text, HTML, and more. URLs are automatically downloaded and processed.

**Response:** `"Resource added and indexed: viking://resources/user/..."`

## HTTP Endpoint

```
http://127.0.0.1:2033/mcp   (streamable-http, default)
http://127.0.0.1:2033/mcp   (when --transport stdio, use stdio instead)
```

> Note: Windows users should use `127.0.0.1` instead of `localhost` when connecting via Claude CLI.

## Connect from Claude

```bash
# Add as MCP server in Claude CLI
claude mcp add --transport http openviking http://127.0.0.1:2033/mcp
```

Or add to `.mcp.json`:

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

## All Options

```
uv run server.py [OPTIONS]

  --config PATH         Config file path (default: ./ov.conf, env: OV_CONFIG)
  --data PATH           Data directory path (default: ./data, env: OV_DATA)
  --host HOST           Bind address (default: 127.0.0.1)
  --port PORT           Listen port (default: 2033, env: OV_PORT)
  --transport TYPE      streamable-http | stdio (default: streamable-http)

  # HTTP mode (connect to remote OpenViking server)
  --ov-url URL          Remote OpenViking server URL (env: OV_URL)
  --api-key KEY         API key for authentication (env: OV_API_KEY)
  --agent-id ID         Default agent ID for resource scoping (env: OV_AGENT_ID)
  --user USER           Default user for resource scoping (env: OV_USER)
  --default-uri URI     Default target URI for search scoping (env: OV_DEFAULT_URI)

  # Debug
  --debug               Enable debug logging (env: OV_DEBUG=1)
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OV_CONFIG` | Config file path |
| `OV_DATA` | Data directory path |
| `OV_PORT` | Server port |
| `OV_URL` | Remote OpenViking server URL (enables HTTP mode) |
| `OV_API_KEY` | API key for remote server auth |
| `OV_AGENT_ID` | Default agent ID |
| `OV_USER` | Default user for resource scoping |
| `OV_DEFAULT_URI` | Default target URI for search |
| `OV_DEBUG` | Set to `1` to enable debug logging |

## Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
# Connect to http://127.0.0.1:2033/mcp
```

