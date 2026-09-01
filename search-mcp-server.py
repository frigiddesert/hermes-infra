#!/usr/bin/env python3
"""MCP Server for search with fallback: SearXNG -> Tavily"""
import os
import json
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("search-mcp")

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8081")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_URL = "https://api.tavily.com/search"

@mcp.tool()
async def search(query: str, max_results: int = 5) -> str:
    """Search with local SearXNG first, fall back to Tavily if needed."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    formatted = [
                        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")[:500]}
                        for r in results[:max_results]
                    ]
                    return json.dumps({"source": "searxng", "results": formatted})
    except Exception:
        pass

    if TAVILY_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    TAVILY_URL,
                    headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                    json={"query": query, "max_results": max_results},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    formatted = [
                        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")[:500]}
                        for r in data.get("results", [])
                    ]
                    return json.dumps({"source": "tavily", "results": formatted})
        except Exception:
            pass

    return json.dumps({"source": "error", "message": "No results found"})

if __name__ == "__main__":
    mcp.run()
