"""
Search Tool - Deep search functionality

Provides deep search for papers, literature, and technical materials.
"""

from typing import Dict, Any, List, Optional
import httpx
from astrbot.api import logger

from ..core.config import PluginConfig


class SearchTool:
    """Tool for deep search of papers and technical materials."""

    def __init__(self, config: PluginConfig):
        """Initialize the search tool.

        Args:
            config: Plugin configuration
        """
        self.config = config
        self.base_url = config.agent_backend_url
        self.api_key = config.agent_backend_api_key

    async def deep_search(self, query: str, search_type: str = "auto") -> str:
        """Perform deep search.

        Args:
            query: Search keywords
            search_type: Type of search (paper, tech, general, auto)

        Returns:
            Formatted search results
        """
        try:
            # Call agent backend API
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                resp = await client.post(
                    f"{self.base_url}/api/search/deep",
                    headers=headers,
                    json={
                        "query": query,
                        "search_type": search_type,
                        "limit": 10,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                return self._format_results(data, query)

        except httpx.TimeoutException:
            logger.error("Deep search timeout")
            return "❌ 搜索超时，请稍后重试"
        except httpx.HTTPStatusError as e:
            logger.error(f"Deep search HTTP error: {e.response.status_code}")
            return f"❌ 搜索失败: HTTP {e.response.status_code}"
        except Exception as e:
            logger.error(f"Deep search failed: {e}")
            return f"❌ 搜索失败: {str(e)}"

    def _format_results(self, data: Dict[str, Any], query: str) -> str:
        """Format search results for display.

        Args:
            data: API response data
            query: Original query

        Returns:
            Formatted string
        """
        if not data.get("success"):
            error = data.get("error", "未知错误")
            return f"❌ 搜索失败: {error}"

        results = data.get("results", [])

        if not results:
            msg = f"🔍 未找到关于「{query}」的深度资料\n\n"
            msg += "💡 建议:\n"
            msg += "1. 尝试使用更具体的关键词\n"
            msg += "2. 使用英文关键词搜索论文\n"
            msg += "3. 检查拼写是否正确"
            return msg

        msg = f"🔍 深度搜索结果: 「{query}」\n"
        msg += f"找到 {len(results)} 条相关结果\n\n"

        for i, result in enumerate(results[:5], 1):
            title = result.get("title", "未命名")
            source = result.get("source", "")
            url = result.get("url", "")
            summary = result.get("summary", "")

            msg += f"{i}. 📄 {title}\n"
            if source:
                msg += f"   来源: {source}\n"
            if summary:
                # Truncate summary if too long
                if len(summary) > 150:
                    summary = summary[:150] + "..."
                msg += f"   摘要: {summary}\n"
            if url:
                msg += f"   链接: {url}\n"
            msg += "\n"

        if len(results) > 5:
            msg += f"... 还有 {len(results) - 5} 个结果\n"

        return msg

    async def search_papers(self, query: str, limit: int = 10) -> str:
        """Search for academic papers.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            Formatted search results
        """
        return await self.deep_search(query, search_type="paper")

    async def search_tech_docs(self, query: str, limit: int = 10) -> str:
        """Search for technical documentation.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            Formatted search results
        """
        return await self.deep_search(query, search_type="tech")

    async def search_arxiv(self, query: str, limit: int = 5) -> str:
        """Search arXiv for papers.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            Formatted search results
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # arXiv API endpoint
                resp = await client.get(
                    "http://export.arxiv.org/api/query",
                    params={
                        "search_query": f"all:{query}",
                        "start": 0,
                        "max_results": limit,
                        "sortBy": "relevance",
                        "sortOrder": "descending",
                    },
                )
                resp.raise_for_status()

                # Parse XML response (simplified)
                import xml.etree.ElementTree as ET

                root = ET.fromstring(resp.text)

                # Namespace
                ns = {"atom": "http://www.w3.org/2005/Atom"}

                entries = root.findall("atom:entry", ns)

                if not entries:
                    return f"🔍 在 arXiv 上未找到关于「{query}」的论文"

                msg = f"🔬 arXiv 搜索结果: 「{query}」\n"
                msg += f"找到 {len(entries)} 篇相关论文\n\n"

                for i, entry in enumerate(entries[:limit], 1):
                    title = entry.find("atom:title", ns)
                    title_text = title.text.strip() if title is not None else "未命名"

                    authors = entry.findall("atom:author/atom:name", ns)
                    author_text = (
                        ", ".join([a.text for a in authors[:3]]) if authors else "未知"
                    )
                    if len(authors) > 3:
                        author_text += " 等"

                    link = entry.find("atom:id", ns)
                    link_text = link.text if link is not None else ""

                    summary = entry.find("atom:summary", ns)
                    summary_text = summary.text.strip() if summary is not None else ""
                    if len(summary_text) > 100:
                        summary_text = summary_text[:100] + "..."

                    msg += f"{i}. 📄 {title_text}\n"
                    msg += f"   作者: {author_text}\n"
                    if summary_text:
                        msg += f"   摘要: {summary_text}\n"
                    if link_text:
                        msg += f"   链接: {link_text}\n"
                    msg += "\n"

                return msg

        except Exception as e:
            logger.error(f"arXiv search failed: {e}")
            return f"❌ arXiv 搜索失败: {str(e)}"

    async def search_github(self, query: str, limit: int = 5) -> str:
        """Search GitHub for repositories.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            Formatted search results
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://api.github.com/search/repositories",
                    params={
                        "q": query,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": limit,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                items = data.get("items", [])

                if not items:
                    return f"🔍 在 GitHub 上未找到关于「{query}」的仓库"

                msg = f"🐙 GitHub 搜索结果: 「{query}」\n"
                msg += f"找到 {data.get('total_count', 0)} 个相关仓库\n\n"

                for i, repo in enumerate(items[:limit], 1):
                    name = repo.get("full_name", "未知")
                    desc = repo.get("description", "")
                    stars = repo.get("stargazers_count", 0)
                    language = repo.get("language", "未知")
                    url = repo.get("html_url", "")

                    msg += f"{i}. 📦 {name}\n"
                    msg += f"   ⭐ {stars} | 语言: {language}\n"
                    if desc:
                        if len(desc) > 100:
                            desc = desc[:100] + "..."
                        msg += f"   {desc}\n"
                    msg += f"   {url}\n\n"

                return msg

        except Exception as e:
            logger.error(f"GitHub search failed: {e}")
            return f"❌ GitHub 搜索失败: {str(e)}"
