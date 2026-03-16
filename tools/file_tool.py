"""
File Tool - File search functionality

Provides file search in group files and COS storage.
"""

from typing import Dict, Any, List, Optional
import httpx
from astrbot.api import logger

from ..core.config import PluginConfig


class FileTool:
    """Tool for searching files in group and COS."""

    def __init__(self, config: PluginConfig):
        """Initialize the file tool.

        Args:
            config: Plugin configuration
        """
        self.config = config
        self.base_url = config.agent_backend_url
        self.api_key = config.agent_backend_api_key

    async def search(self, query: str, file_type: str = "auto") -> str:
        """Search for files.

        Args:
            query: Search keywords
            file_type: File type filter (pdf, ppt, doc, auto)

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
                    f"{self.base_url}/api/files/search",
                    headers=headers,
                    json={
                        "query": query,
                        "file_type": file_type,
                        "limit": 10,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                return self._format_results(data, query)

        except httpx.TimeoutException:
            logger.error("File search timeout")
            return "❌ 搜索超时，请稍后重试"
        except httpx.HTTPStatusError as e:
            logger.error(f"File search HTTP error: {e.response.status_code}")
            return f"❌ 搜索失败: HTTP {e.response.status_code}"
        except Exception as e:
            logger.error(f"File search failed: {e}")
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

        files = data.get("files", [])

        if not files:
            msg = f"🔍 未找到包含「{query}」的文件\n\n"
            msg += "💡 建议:\n"
            msg += "1. 尝试使用更通用的关键词\n"
            msg += "2. 检查拼写是否正确\n"
            msg += "3. 使用 /hit scan 扫描最新文件"
            return msg

        msg = f"🔍 找到 {len(files)} 个相关文件:\n\n"

        for i, file in enumerate(files[:8], 1):
            name = file.get("name", "未命名")
            file_type = file.get("type", "未知")
            size = self._format_size(file.get("size", 0))
            course = file.get("course", "")

            msg += f"{i}. 📄 {name}\n"
            msg += f"   类型: {file_type} | 大小: {size}"
            if course:
                msg += f" | 课程: {course}"
            msg += "\n"

            # Add download link if available
            url = file.get("url", "")
            if url:
                msg += f"   链接: {url}\n"
            msg += "\n"

        if len(files) > 8:
            msg += f"... 还有 {len(files) - 8} 个结果\n"

        return msg

    def _format_size(self, size_bytes: int) -> str:
        """Format file size for display.

        Args:
            size_bytes: Size in bytes

        Returns:
            Formatted size string
        """
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    async def get_file_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get file information by ID.

        Args:
            file_id: File ID

        Returns:
            File information dictionary or None
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                resp = await client.get(
                    f"{self.base_url}/api/files/{file_id}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("success"):
                    return data.get("file")
                return None

        except Exception as e:
            logger.error(f"Get file by ID failed: {e}")
            return None

    async def list_files_by_course(self, course_code: str) -> str:
        """List files for a specific course.

        Args:
            course_code: Course code

        Returns:
            Formatted file list
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                resp = await client.get(
                    f"{self.base_url}/api/courses/{course_code}/files",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                return self._format_course_files(data, course_code)

        except Exception as e:
            logger.error(f"List course files failed: {e}")
            return f"❌ 获取课程文件失败: {str(e)}"

    def _format_course_files(self, data: Dict[str, Any], course_code: str) -> str:
        """Format course files for display.

        Args:
            data: API response data
            course_code: Course code

        Returns:
            Formatted string
        """
        if not data.get("success"):
            return f"❌ 未找到课程 {course_code} 的文件"

        files = data.get("files", [])

        if not files:
            return f"📁 课程 {course_code} 暂无文件\n\n💡 使用 /hit contribute 上传资料"

        msg = f"📁 课程 {course_code} 的文件 ({len(files)}个):\n\n"

        # Group by file type
        files_by_type: Dict[str, List[Dict]] = {}
        for file in files:
            file_type = file.get("type", "其他")
            if file_type not in files_by_type:
                files_by_type[file_type] = []
            files_by_type[file_type].append(file)

        for file_type, type_files in files_by_type.items():
            msg += f"【{file_type}】({len(type_files)}个)\n"
            for file in type_files[:5]:
                name = file.get("name", "未命名")
                size = self._format_size(file.get("size", 0))
                msg += f"  • {name} ({size})\n"
            if len(type_files) > 5:
                msg += f"  ... 还有 {len(type_files) - 5} 个\n"
            msg += "\n"

        return msg
