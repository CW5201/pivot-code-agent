"""WebSearchTool —— 使用百度搜索网络。"""

from typing import Any

from pivotcode.tools.base import Tool, ToolResult, ToolUseContext

_TIMEOUT_SECONDS = 15


class WebSearchTool(Tool):
    """使用百度搜索网络。"""

    @property
    def name(self) -> str:
        return "WebSearch"

    @property
    def description(self) -> str:
        return (
            "Search the web using Baidu. Returns search results with titles, "
            "URLs, and snippets.\n\n"
            "Usage notes:\n"
            "- Use this to find current information, documentation, or answers\n"
            "- Results are limited to 5 items\n"
            "- For fetching full page content, use WebFetch tool after searching"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
            },
            "required": ["query"],
        }

    def permission_level(self, args: dict[str, Any]) -> str:
        return "read"

    async def call(self, args: dict[str, Any], context: ToolUseContext) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(
                data="Error: 'query' parameter is required.",
                is_error=True,
            )

        try:
            import httpx
        except ImportError:
            return ToolResult(
                data="Error: httpx is not installed. Run 'pip install httpx'.",
                is_error=True,
            )

        try:
            # 使用百度搜索
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_TIMEOUT_SECONDS),
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    "https://www.baidu.com/s",
                    params={"wd": query, "rn": "5"},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    },
                )

            if response.status_code != 200:
                return ToolResult(
                    data=f"Error: search failed (HTTP {response.status_code})",
                    is_error=True,
                )

            # 解析简单的 HTML 结果
            import re
            html = response.text

            # 提取搜索结果
            results = []
            # 从百度结果中提取标题与 URL 配对
            title_pattern = r'<h3[^>]*>.*?<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>'
            matches = re.findall(title_pattern, html, re.DOTALL | re.IGNORECASE)

            for url, title in matches[:5]:
                # 去除标题中的 HTML 标签
                clean_title = re.sub(r'<[^>]+>', '', title).strip()
                if clean_title:
                    results.append(f"• {clean_title}\n  URL: {url}")

            if not results:
                # 回退：仅提供搜索 URL
                return ToolResult(
                    data=(
                        f"Search query: {query}\n\n"
                        f"Open in browser: https://www.baidu.com/s?wd={query.replace(' ', '+')}"
                    ),
                )

            return ToolResult(
                data=f"Search results for: {query}\n\n" + "\n\n".join(results)
            )

        except httpx.TimeoutException:
            return ToolResult(
                data=(
                    f"Search timed out for: {query}\n\n"
                    f"Open in browser: https://www.baidu.com/s?wd={query.replace(' ', '+')}"
                ),
            )
        except Exception as exc:
            return ToolResult(
                data=f"Error during search: {exc}",
                is_error=True,
            )
