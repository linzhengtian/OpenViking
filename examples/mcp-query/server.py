#!/usr/bin/env python3
"""
OpenViking MCP Server - Expose RAG query capabilities through Model Context Protocol

Provides MCP tools for:
  - query: Full RAG pipeline (search + LLM generation)
  - search: Semantic search only (no LLM)
  - add_resource: Add documents/URLs to the database

Usage:
  uv run server.py
  uv run server.py --config ./ov.conf --data ./data --port 2033
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import httpx
from pathlib import Path
from typing import Optional, List, Dict, Any
import time
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.recipe import Recipe
from mcp.server.fastmcp import FastMCP, Context

import openviking as ov
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openviking-mcp")

# Global state
_recipe: Optional[Recipe] = None
_config_path: str = "./ov.conf"
_data_path: str = "./data"
_api_key: str = ""
_default_uri: str = ""
_ov_url: str = ""
_ov_agent_id: str = ""
_ov_user: str = ""


def _get_recipe() -> Recipe:
    """Get or create the Recipe (RAG pipeline) instance."""
    global _recipe
    if _recipe is None:
        _recipe = Recipe(config_path=_config_path, data_path=_data_path)
        if _api_key:
            _recipe.api_key = _api_key
    return _recipe


def _build_uri(user: str, agent_id: Optional[str] = None) -> str:
    """Build viking URI path.

    Without agent_id: viking://resources/{user}
    With agent_id:    viking://resources/{user}/{agent_id}
    """
    if agent_id:
        return f"viking://resources/{user}/{agent_id}"
    return f"viking://resources/{user}"


def ovl_search(
    client,
    query: str,
    top_k: int = 3,
    target_uri: Optional[str] = None,
    score_threshold: float = 0.2,
) -> List[Dict[str, Any]]:
    """
    Search for relevant content using semantic search

    Args:
        query: Search query
        top_k: Number of results to return
        target_uri: Optional specific URI to search in. If None, searches all resources.
        score_threshold: Minimum relevance score for search results (default: 0.2)

    Returns:
        List of search results with content and scores
    """
    # print(f"🔍 Searching for: '{query}'")

    # Search all resources or specific target
    # `find` has better performance, but not so smart
    results = client.find(query, target_uri=target_uri, score_threshold=score_threshold)

    # Extract top results
    search_results = []
    for _i, resource in enumerate(
        results.resources[:top_k] + results.memories[:top_k]
    ):  # ignore SKILLs for mvp
        try:
            content = client.read(resource.uri)
            search_results.append(
                {
                    "uri": resource.uri,
                    "score": resource.score,
                    "content": content,
                }
            )
            # print(f"  {i + 1}. {resource.uri} (score: {resource.score:.4f})")

        except Exception as e:
            # Handle directories - read their abstract instead
            if "is a directory" in str(e):
                try:
                    abstract = client.abstract(resource.uri)
                    search_results.append(
                        {
                            "uri": resource.uri,
                            "score": resource.score,
                            "content": f"[Directory Abstract] {abstract}",
                        }
                    )
                    # print(f"  {i + 1}. {resource.uri} (score: {resource.score:.4f}) [directory]")
                except:
                    # Skip if we can't get abstract
                    continue
            else:
                # Skip other errors
                continue

    return search_results


def ovl_query(
    client,
    user_query: str,
    search_top_k: int = 3,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    system_prompt: Optional[str] = None,
    score_threshold: float = 0.2,
    chat_history: Optional[List[Dict[str, str]]] = None,
    target_uri: Optional[str] = None
) -> Dict[str, Any]:
    """
    Full RAG pipeline: search → retrieve → generate

    Args:
        user_query: User's question
        search_top_k: Number of search results to use as context
        temperature: LLM sampling temperature
        max_tokens: Maximum tokens to generate
        system_prompt: Optional system prompt to prepend
        score_threshold: Minimum relevance score for search results (default: 0.2)
        chat_history: Optional list of previous conversation turns for multi-round chat.
                    Each turn should be a dict with 'role' and 'content' keys.
                    Example: [{"role": "user", "content": "previous question"},
                              {"role": "assistant", "content": "previous answer"}]

    Returns:
        Dictionary with answer, context, metadata, and timings
    """
    # Track total time
    start_total = time.perf_counter()

    # Step 1: Search for relevant content (timed)
    start_search = time.perf_counter()
    search_results = ovl_search(
        client, user_query, top_k=search_top_k, score_threshold=score_threshold, target_uri=target_uri
    )
    search_time = time.perf_counter() - start_search

    # Step 2: Build context from search results
    context_text = "no relevant information found, try answer based on existing knowledge."
    if search_results:
        context_text = (
            "Answer should pivoting to the following:\n<context>\n"
            + "\n\n".join(
                [
                    f"[Source {i + 1}] (relevance: {r['score']:.4f})\n{r['content']}"
                    for i, r in enumerate(search_results)
                ]
            )
            + "\n</context>"
        )

    # Step 3: Build messages array for chat completion API
    messages = []

    # Add system message if provided
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    else:
        messages.append(
            {
                "role": "system",
                "content": "Answer questions with plain text. avoid markdown special character",
            }
        )

    # Add chat history if provided (for multi-round conversations)
    if chat_history:
        messages.extend(chat_history)

    # Build current turn prompt with context and question
    current_prompt = f"{context_text}\n"
    current_prompt += f"Question: {user_query}\n\n"

    # Add current user message
    messages.append({"role": "user", "content": current_prompt})

    # Step 4: Call LLM with messages array (timed)
    start_llm = time.perf_counter()
    try:
        if search_results:
            url = f"{_api_base}/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_api_key}"}
            payload = {
                "model": _model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            # print(f"🤖 Calling LLM: {_model}")
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
            answer = result["choices"][0]["message"]["content"]
        else:
            answer = ""
    except:
        answer = ""
    llm_time = time.perf_counter() - start_llm
    # Calculate total time
    total_time = time.perf_counter() - start_total

    # Return full result with timing data
    return {
        "answer": answer,
        "context": search_results,
        "query": user_query,
        "prompt": current_prompt,
        "timings": {
            "search_time": search_time,
            "llm_time": llm_time,
            "total_time": total_time,
        },
    }


def _get_user_from_api_key(api_key: str) -> Optional[str]:
    """Get user info from OpenViking server using API key."""
    global _ov_url
    global _ov_user
    if _ov_user:
        return _ov_user
    if not _ov_url:
        return None

    try:
        url = f"{_ov_url.rstrip('/')}/api/v1/system/status"
        headers = {"X-API-Key": api_key}
        timeout = httpx.Timeout(30.0, connect=10.0)

        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok" and data.get("result"):
                return data["result"].get("user")
            return None
    except Exception as e:
        logger.warning(f"Failed to get user from API key: {e}")
        return None


def _http_request(method: str, path: str, api_key: str, agent_id: Optional[str] = None,
                   json_data: Optional[dict] = None, files: Optional[dict] = None) -> dict:
    """Make HTTP request to OpenViking server with OAuth headers."""
    global _ov_url

    url = f"{_ov_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "X-API-Key": api_key,
        "X-OpenViking-Agent": agent_id or "",
    }

    timeout = httpx.Timeout(300.0, connect=30.0)

    with httpx.Client(timeout=timeout) as client:
        if method.upper() == "GET":
            resp = client.get(url, headers=headers)
        elif method.upper() == "POST":
            resp = client.post(url, headers=headers, json=json_data, files=files)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        resp.raise_for_status()
        return resp.json()


def _extract_headers_from_context(context) -> tuple[Optional[str], Optional[str]]:
    """Extract API key and agent_id from MCP request context headers.

    Returns (api_key, agent_id) extracted from HTTP headers.
    """
    api_key = None
    agent_id = None
    user = None

    try:
        # Try to get headers from request context
        if hasattr(context, 'request_context') and context.request_context:
            request = context.request_context
            # FastMCP stores headers in the request object
            if hasattr(request, 'request') and hasattr(request.request, 'headers'):
                headers = request.request.headers
                api_key = headers.get("x-api-key") or headers.get("X-API-Key")
                agent_id = headers.get("x-openviking-agent") or headers.get("X-OpenViking-Agent")
                user = headers.get("user") or headers.get("X-User")
            elif hasattr(request, 'headers'):
                api_key = request.get_header("x-api-key")
                agent_id = request.get_header("x-openviking-agent")
                user = request.get_header("user")
    except Exception as e:
        logger.debug(f"Could not extract headers from context: {e}")

    return api_key, agent_id, user


def create_server(host: str = "127.0.0.1", port: int = 2033) -> FastMCP:
    """Create and configure the MCP server."""
    mcp = FastMCP(
        name="openviking-mcp",
        instructions=(
            "OpenViking MCP Server provides RAG (Retrieval-Augmented Generation) capabilities. "
            "Use 'query' for full RAG answers, 'search' for semantic search only, "
            "and 'add_resource' to ingest new documents."
        ),
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def query(
        ctx: Context,
        question: str,
        top_k: int = 5,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        score_threshold: float = 0.2,
        system_prompt: str = "",
    ) -> str:
        """
        Ask a question and get an answer using RAG (Retrieval-Augmented Generation).

        Searches the OpenViking database for relevant context, then generates an answer
        using an LLM with the retrieved context.

        Args:
            ctx: MCP context (contains request headers).
            question: The question to ask.
            top_k: Number of search results to use as context (1-20, default: 5).
            temperature: LLM sampling temperature (0.0-1.0, default: 0.7).
            max_tokens: Maximum tokens in the response (default: 2048).
            score_threshold: Minimum relevance score for search results (0.0-1.0, default: 0.2).
            system_prompt: Optional system prompt to guide the LLM response style.
        """
        # Extract headers for HTTP mode
        header_api_key, header_agent_id, user_id = _extract_headers_from_context(ctx)
        effective_api_key = header_api_key or _api_key
        effective_agent_id = header_agent_id or _ov_agent_id

        def _query_sync():
            # HTTP mode: call remote OpenViking server
            if _ov_url and effective_api_key:
                user = _get_user_from_api_key(effective_api_key) if not user_id else user_id
                target_uri = _build_uri(user or "default", effective_agent_id)

                config = {
                    "url": _ov_url,
                    "api_key": effective_api_key,
                    "agent_id": effective_agent_id,
                    "timeout": 120.0,
                }
                client = ov.SyncHTTPClient(**config)
                client.initialize()
                return ovl_query(
                    client,
                    user_query=question,
                    search_top_k=top_k,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    score_threshold=score_threshold,
                    system_prompt=system_prompt or None,
                    target_uri=target_uri
                )
            else:
                # Local mode
                recipe = _get_recipe()
                target_uri = _default_uri or "viking://resources/default"
                return recipe.query(
                    user_query=question,
                    search_top_k=top_k,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    score_threshold=score_threshold,
                    system_prompt=system_prompt or None,
                    target_uri=target_uri,
                )

        result = await asyncio.to_thread(_query_sync)

        # Format response with answer and sources
        output = result["answer"]
        if not output:
            return "No answer found."
        if result["context"]:
            output += "\n\n---\nSources:\n"
            for i, ctx_item in enumerate(result["context"], 1):
                uri_parts = ctx_item["uri"].split("/")
                filename = uri_parts[-1] if uri_parts else ctx_item["uri"]
                output += f"  {i}. {filename} (relevance: {ctx_item['score']:.4f})\n"

        timings = result.get("timings", {})
        if timings:
            output += (
                f"\n[search: {timings.get('search_time', 0):.2f}s, "
                f"llm: {timings.get('llm_time', 0):.2f}s, "
                f"total: {timings.get('total_time', 0):.2f}s]"
            )

        return output

    @mcp.tool()
    async def search(
        ctx: Context,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.2,
    ) -> str:
        """
        Search the OpenViking database for relevant content (no LLM generation).

        Performs semantic search and returns matching documents with relevance scores.
        Use this when you only need to find relevant documents without generating an answer.

        Args:
            ctx: MCP context (contains request headers).
            query: The search query.
            top_k: Number of results to return (1-20, default: 5).
            score_threshold: Minimum relevance score (0.0-1.0, default: 0.2).
        """
        # Extract headers for HTTP mode
        header_api_key, header_agent_id, user_id = _extract_headers_from_context(ctx)
        effective_api_key = header_api_key or _api_key
        effective_agent_id = header_agent_id or _ov_agent_id

        def _search_sync():
            if _ov_url and effective_api_key:
                user = _get_user_from_api_key(effective_api_key) if not user_id else user_id
                target_uri = _build_uri(user or "default", effective_agent_id)

                config = {
                    "url": _ov_url,
                    "api_key": effective_api_key,
                    "agent_id": effective_agent_id,
                    "timeout": 120.0,
                }
                client = ov.SyncHTTPClient(**config)
                client.initialize()
                return ovl_search(
                    client=client,
                    query=query,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    target_uri=target_uri,
                )

            else:
                recipe = _get_recipe()
                target_uri = _default_uri or None
                return recipe.search(
                    query=query,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    target_uri=target_uri,
                )

        results = await asyncio.to_thread(_search_sync)

        if not results:
            return "No relevant results found."

        output_parts = []
        for i, r in enumerate(results, 1):
            preview = r["content"][:500] + "..." if len(r["content"]) > 500 else r["content"]
            output_parts.append(f"[{i}] {r['uri']} (score: {r['score']:.4f})\n{preview}")

        return f"Found {len(results)} results:\n\n" + "\n\n".join(output_parts)

    @mcp.tool()
    async def add_resource(
        ctx: Context,
        resource_path: str,
    ) -> str:
        """
        Add a document, file, directory, or URL to the OpenViking database.

        The resource will be parsed, chunked, and indexed for future search/query operations.
        Supported formats: PDF, Markdown, Text, HTML, and more.
        URLs are automatically downloaded and processed.

        Args:
            ctx: MCP context (contains request headers).
            resource_path: Path to a local file/directory, or a URL to add.
        """
        # Extract headers for HTTP mode
        header_api_key, header_agent_id, user_id = _extract_headers_from_context(ctx)
        effective_api_key = header_api_key or _api_key
        effective_agent_id = header_agent_id or _ov_agent_id

        def _add_sync():
            if _ov_url and effective_api_key:
                # HTTP mode: upload to remote OpenViking server
                user = _get_user_from_api_key(effective_api_key) if not user_id else user_id
                target_uri = _build_uri(user or "default", effective_agent_id)
                config = {
                    "url": _ov_url,
                    "api_key": effective_api_key,
                    "agent_id": effective_agent_id,
                    "timeout": 120.0,
                }
                client = ov.SyncHTTPClient(**config)
            else:
                # Local mode
                config_path = _config_path
                data_path = _data_path

                with open(config_path, "r") as f:
                    config_dict = json.load(f)

                config = OpenVikingConfig.from_dict(config_dict)
                client = ov.SyncOpenViking(path=data_path, config=config)

            try:
                client.initialize()

                path = resource_path
                if not path.startswith("http"):
                    resolved = Path(path).expanduser()
                    if not resolved.exists():
                        return f"Error: File not found: {resolved}"
                    path = str(resolved)
                if _ov_url and effective_api_key:
                    result = client.add_resource(path=path, to=target_uri)
                else:
                    result = client.add_resource(path=path)

                if result and "root_uri" in result:
                    root_uri = result["root_uri"]
                    client.wait_processed(timeout=300)
                    return f"Resource added and indexed: {root_uri}"
                elif result and result.get("status") == "error":
                    errors = result.get("errors", [])[:3]
                    error_msg = "\n".join(f"  - {e}" for e in errors)
                    return (
                        f"Resource had parsing issues:\n{error_msg}\n"
                        "Some content may still be searchable."
                    )
                else:
                    return "Failed to add resource."
            finally:
                client.close()

        return await asyncio.to_thread(_add_sync)

    @mcp.resource("openviking://status")
    def server_status() -> str:
        """Get the current server status and configuration."""
        info = {
            "config_path": _config_path,
            "data_path": _data_path,
            "status": "running",
            "http_mode": bool(_ov_url),
        }
        return json.dumps(info, indent=2)

    return mcp


def parse_args():
    parser = argparse.ArgumentParser(
        description="OpenViking MCP Server - RAG query capabilities via MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with defaults (local mode)
  uv run server.py

  # HTTP mode: connect to remote OpenViking server
  uv run server.py --ov-url https://openviking.example.com \\
                    --api-key sk-xxx \\
                    --agent-id my-agent

  # Custom config and port
  uv run server.py --config ./ov.conf --data ./data --port 9000

  # Use stdio transport (for Claude Desktop integration)
  uv run server.py --transport stdio

  # Connect from Claude CLI (use 127.0.0.1 instead of localhost for Windows compatibility)
  claude mcp add --transport http openviking http://127.0.0.1:2033/mcp

Environment variables:
  OV_CONFIG      Path to config file (default: ./ov.conf)
  OV_DATA        Path to data directory (default: ./data)
  OV_PORT        Server port (default: 2033)
  OV_API_KEY     Default API key for OpenViking server authentication
  OV_AGENT_ID    Default agent ID for resource scoping
  OV_USER        Default user for resource scoping
  OV_URL         OpenViking server HTTP URL (enables HTTP mode)
  OV_DEBUG       Enable debug logging (set to 1)
        """,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("OV_CONFIG", "./ov.conf"),
        help="Path to config file (default: ./ov.conf)",
    )
    parser.add_argument(
        "--data",
        type=str,
        default=os.getenv("OV_DATA", "./data"),
        help="Path to data directory (default: ./data)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("OV_PORT", "2033")),
        help="Port to listen on (default: 2033)",
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="Transport type (default: streamable-http)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("OV_API_KEY", ""),
        help="Default API key for OpenViking server authentication (default: $OV_API_KEY)",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=os.getenv("OV_AGENT_ID", ""),
        help="Default agent ID for resource scoping (default: $OV_AGENT_ID)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=os.getenv("OV_USER", ""),
        help="Default agent ID for resource scoping (default: OV_USER)",
    )
    parser.add_argument(
        "--ov-url",
        type=str,
        default=os.getenv("OV_URL", "http://127.0.0.1:1933"),
        help="OpenViking server HTTP URL (enables HTTP mode, e.g. https://openviking.example.com)",
    )
    parser.add_argument(
        "--default-uri",
        type=str,
        default=os.getenv("OV_DEFAULT_URI", ""),
        help="Default target URI for search scoping (default: search all)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    global _config_path, _data_path, _api_key, _default_uri, _ov_url, _ov_agent_id, _ov_user
    global _api_base, _api_key, _model
    _config_path = args.config
    _data_path = args.data
    _api_key = args.api_key
    _default_uri = args.default_uri
    _ov_url = args.ov_url
    _ov_agent_id = args.agent_id
    _ov_user = args.user

    # LLM
    with open(_config_path, "r") as f:
        vlm_config = json.load(f).get("vlm", {})
        _api_base = vlm_config.get("api_base")
        _api_key = vlm_config.get("api_key")
        _model = vlm_config.get("model")

    if os.getenv("OV_DEBUG") == "1":
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("OpenViking MCP Server starting")
    logger.info(f"  config: {_config_path}")
    logger.info(f"  data:   {_data_path}")
    logger.info(f"  transport: {args.transport}")
    if _ov_url:
        logger.info(f"  openviking server: {_ov_url}")

    mcp = create_server(host=args.host, port=args.port)

    if args.transport == "streamable-http":
        logger.info(f"  endpoint: http://{args.host}:{args.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
