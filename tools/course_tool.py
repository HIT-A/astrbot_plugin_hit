"""
Course Tool - Course search functionality

Provides course search via the main agent backend.
"""

from typing import Dict, Any, Optional
import httpx
from astrbot.api import logger

from ..core.config import PluginConfig


class CourseTool:
    """Tool for searching course information."""

    def __init__(self, config: PluginConfig):
        """Initialize the course tool.

        Args:
            config: Plugin configuration
        """
        self.config = config
        self.base_url = config.agent_backend_url
        self.api_key = config.agent_backend_api_key

    async def search(self, query: str, search_type: str = "auto") -> str:
        """Search for courses.

        Args:
            query: Course code, name, or teacher name
            search_type: Type of search (course, teacher, auto)

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
                    f"{self.base_url}/api/courses/search",
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
            logger.error("Course search timeout")
            return "❌ 搜索超时，请稍后重试"
        except httpx.HTTPStatusError as e:
            logger.error(f"Course search HTTP error: {e.response.status_code}")
            return f"❌ 搜索失败: HTTP {e.response.status_code}"
        except Exception as e:
            logger.error(f"Course search failed: {e}")
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

        courses = data.get("courses", [])

        if not courses:
            msg = f"🔍 未找到包含「{query}」的课程\n\n"
            msg += "💡 您可以通过以下方式贡献:\n"
            msg += "1. 使用 /hit contribute 提交课程信息\n"
            msg += "2. 分享课程资料"
            return msg

        msg = f"🔍 找到 {len(courses)} 个相关课程:\n\n"

        for i, course in enumerate(courses[:5], 1):
            code = course.get("code", "未知")
            name = course.get("name", "未知")
            teacher = course.get("teacher", "")
            rating = course.get("rating", 0)

            msg += f"{i}. {code} - {name}"
            if teacher:
                msg += f" ({teacher})"
            if rating:
                msg += f" ⭐{rating:.1f}"
            msg += "\n"

        if len(courses) > 5:
            msg += f"\n... 还有 {len(courses) - 5} 个结果"

        msg += "\n\n💡 使用 /hit course <课程代码> 查看详情"
        return msg

    async def get_course_detail(self, course_code: str) -> str:
        """Get detailed information about a course.

        Args:
            course_code: Course code

        Returns:
            Formatted course details
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                resp = await client.get(
                    f"{self.base_url}/api/courses/{course_code}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                return self._format_detail(data)

        except Exception as e:
            logger.error(f"Get course detail failed: {e}")
            return f"❌ 获取课程详情失败: {str(e)}"

    def _format_detail(self, data: Dict[str, Any]) -> str:
        """Format course details for display.

        Args:
            data: Course data

        Returns:
            Formatted string
        """
        if not data.get("success"):
            return f"❌ 未找到该课程"

        course = data.get("course", {})

        msg = f"📚 {course.get('code', '未知')} - {course.get('name', '未知')}\n\n"

        if course.get("teacher"):
            msg += f"👨‍🏫 教师: {course['teacher']}\n"

        if course.get("credits"):
            msg += f"📊 学分: {course['credits']}\n"

        if course.get("semester"):
            msg += f"📅 学期: {course['semester']}\n"

        if course.get("rating"):
            msg += f"⭐ 评分: {course['rating']:.1f}/5.0\n"

        if course.get("description"):
            msg += f"\n📝 课程简介:\n{course['description']}\n"

        reviews = course.get("reviews", [])
        if reviews:
            msg += f"\n💬 学生评价 ({len(reviews)}条):\n"
            for review in reviews[:3]:
                content = review.get("content", "")
                if len(content) > 100:
                    content = content[:100] + "..."
                msg += f"  • {content}\n"

        files = course.get("files", [])
        if files:
            msg += f"\n📁 相关资料 ({len(files)}个):\n"
            for file in files[:5]:
                msg += f"  • {file.get('name', '未命名')}\n"

        return msg
