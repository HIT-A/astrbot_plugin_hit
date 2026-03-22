"""
Agent 集成模块 - 将 agent-backend Skills 封装为 astrbot FunctionTools

实现方案：
1. 将 agent-backend 的 32 个 skills 封装为 astrbot FunctionTool
2. 使用 context.tool_loop_agent() 实现多轮 Agent 对话
3. 用户自然语言查询，Agent 自动选择合适的 tool 调用
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
from astrbot import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .agent_client import AgentClient


class HITAgentTools:
    """HIT Agent Tools - 将 agent-backend skills 封装为 astrbot FunctionTools"""

    def __init__(self, agent_client: AgentClient, file_scanner=None, plugin=None):
        self.agent_client = agent_client
        self.file_scanner = file_scanner
        self.plugin = plugin

    def _unwrap_double_nested(self, output: Any) -> Any:
        """修复后端双重嵌套问题"""
        if not isinstance(output, dict):
            return output
        if output.get("ok") is not True:
            return output
        inner = output.get("output")
        if not isinstance(inner, dict):
            return output
        inner_ok = inner.get("ok")
        innermost = inner.get("output")
        if inner_ok is True and isinstance(innermost, dict):
            innermost_keys = set(innermost.keys())
            data_keys = innermost_keys - {"ok", "output"}
            if data_keys:
                return innermost
        return inner

    async def _call_skill(self, skill_name: str, input_data: Dict[str, Any]) -> str:
        """调用 agent-backend skill 并返回格式化结果"""
        try:
            resp = await self.agent_client.invoke_skill(skill_name, input_data)
            if not resp.success:
                error = resp.error or {}
                return f"调用失败: {error.get('message', '未知错误')}"

            output = resp.output or {}

            # 如果返回 job_id，说明是异步任务，需要轮询
            if isinstance(output, dict) and "job_id" in output:
                job_id = output.get("job_id")
                try:
                    job_result = await self.agent_client.wait_for_job(
                        job_id, timeout=60.0
                    )
                    return json.dumps(job_result, ensure_ascii=False, indent=2)
                except TimeoutError:
                    return "操作超时，请稍后重试"
                except RuntimeError as e:
                    return f"操作失败: {e}"

            # 同步返回，直接格式化
            return json.dumps(output, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Skill {skill_name} 调用失败: {e}")
            return f"调用失败: {str(e)}"

    # ========== 课程相关 Tools ==========

    async def search_courses(self, event, keyword: str, limit: int = 10) -> str:
        """搜索课程

        Args:
            event: 事件对象（astrbot传入）
            keyword: 搜索关键词（课程名称、代码、教师等）
            limit: 返回结果数量限制
        """
        return await self._call_skill(
            "courses.search", {"keyword": keyword, "limit": limit}
        )

    async def get_course_detail(
        self, event, campus: str, course_code: str, include_toml: bool = False
    ) -> str:
        """获取课程详细信息

        Args:
            campus: 校区（shenzhen/harbin/weihai）
            course_code: 课程代码
            include_toml: 是否包含 TOML 配置
        """
        return await self._call_skill(
            "course.read",
            {
                "campus": campus,
                "course_code": course_code,
                "include_toml": include_toml,
            },
        )

    async def search_teacher(self, event, name: str) -> str:
        """搜索教师信息

        Args:
            name: 教师姓名
        """
        return await self._call_skill("hit.teacher", {"name": name})

    # ========== 搜索相关 Tools ==========

    async def unified_search(
        self, event, query: str, sources: Optional[List[str]] = None, top_k: int = 10
    ) -> str:
        """统一搜索

        Args:
            query: 搜索关键词
            sources: 数据源列表（rag/brave/arxiv/github/cos/course/course_read/hit_teacher）
            top_k: 返回结果数量
        """
        if sources is None:
            sources = ["rag", "brave", "arxiv", "github"]
        return await self._call_skill(
            "search", {"query": query, "sources": sources, "top_k": top_k}
        )

    async def rag_query(self, event, query: str, top_k: int = 5) -> str:
        """RAG 知识库查询

        Args:
            query: 查询内容
            top_k: 返回结果数量
        """
        return await self._call_skill("rag.query", {"query": query, "top_k": top_k})

    # ========== 文件相关 Tools ==========

    async def upload_file(
        self, event, key: str, content_base64: str, content_type: str = "text/plain"
    ) -> str:
        """上传文件到 COS

        Args:
            key: 文件路径key
            content_base64: 文件内容的base64编码
            content_type: 文件MIME类型
        """
        return await self._call_skill(
            "files.upload",
            {
                "key": key,
                "content_base64": content_base64,
                "content_type": content_type,
            },
        )

    async def upload_and_ingest(
        self,
        event,
        file_name: str,
        content_base64: str,
        content_type: str = "text/plain",
        uploader_name: str = "",
    ) -> str:
        """上传文件到COS并自动入库（链条式操作）

        Args:
            file_name: 文件名
            content_base64: 文件内容的base64编码
            content_type: 文件MIME类型
            uploader_name: 上传者名称（可选）
        """
        import hashlib
        import base64

        content_bytes = base64.b64decode(content_base64)
        file_hash = hashlib.md5(content_bytes).hexdigest()
        ext = ""
        if "." in file_name:
            ext = file_name.rsplit(".", 1)[-1].lower()
        key_suffix = f".{ext}" if ext else ""
        object_key = (
            f"group-files/{file_hash[:2]}/{file_hash[2:4]}/{file_hash}{key_suffix}"
        )

        upload_resp = await self._call_skill(
            "files.upload",
            {
                "key": object_key,
                "content_base64": content_base64,
                "content_type": content_type,
                "auto_ingest_rag": True,  # 新增：直接入库 Qdrant
            },
        )

        if "失败" in upload_resp or "错误" in upload_resp:
            return f"上传失败: {upload_resp}"

        cos_url = f"key://{object_key}"
        rag_chunks = 0
        skipped = False

        try:
            import json

            upload_result = json.loads(upload_resp)
            if isinstance(upload_result, dict):
                if upload_result.get("access_url"):
                    cos_url = upload_result["access_url"]
                elif upload_result.get("url"):
                    cos_url = upload_result["url"]
                skipped = upload_result.get("skipped", False)
                rag_chunks = upload_result.get("rag_chunks", 0)
        except:
            pass

        if skipped:
            return f"文件已存在（去重），跳过入库。COS: {cos_url}"

        return f"上传成功: {cos_url}\nRAG入库: {rag_chunks} chunks"

    async def download_file(self, event, key: str, format: str = "text") -> str:
        """下载文件

        Args:
            key: 文件路径key
            format: 返回格式（text/url）
        """
        return await self._call_skill("files.download", {"key": key, "format": format})

    async def list_cos_files(self, event, prefix: str = "") -> str:
        """列出 COS 文件

        Args:
            prefix: 文件路径前缀过滤
        """
        return await self._call_skill("cos.list_files", {"prefix": prefix})

    # ========== PR 相关 Tools ==========

    async def preview_pr(
        self, event, campus: str, course_code: str, ops: List[Dict]
    ) -> str:
        """预览课程 PR 改动

        Args:
            campus: 校区
            course_code: 课程代码
            ops: 操作列表
        """
        return await self._call_skill(
            "pr.preview", {"campus": campus, "course_code": course_code, "ops": ops}
        )

    async def submit_pr(
        self,
        event,
        campus: str,
        course_code: str,
        ops: List[Dict],
        target_org: str = "HITSZ-OpenAuto",
        idempotency_key: str = "",
    ) -> str:
        """提交课程 PR

        Args:
            campus: 校区
            course_code: 课程代码
            ops: 操作列表
            idempotency_key: 幂等键
        """
        input_data = {"campus": campus, "course_code": course_code, "ops": ops}
        if idempotency_key:
            input_data["idempotency_key"] = idempotency_key
        return await self._call_skill("pr.submit", input_data)

    async def lookup_pr(self, event, org: str, repo: str, number: int) -> str:
        """查询 PR 状态

        Args:
            org: GitHub 组织
            repo: 仓库名
            number: PR 编号
        """
        return await self._call_skill(
            "pr.lookup", {"org": org, "repo": repo, "number": number}
        )

    # ========== 爬虫相关 Tools ==========

    async def crawl_page(self, event, url: str) -> str:
        """抓取单个网页

        Args:
            url: 网页URL
        """
        return await self._call_skill("crawl4ai.page", {"url": url})

    async def crawl_site(self, event, start_url: str, max_pages: int = 10) -> str:
        """抓取整个网站

        Args:
            start_url: 起始URL
            max_pages: 最大页面数
        """
        return await self._call_skill(
            "crawl4ai.site", {"start_url": start_url, "max_pages": max_pages}
        )

    # ========== MCP 相关 Tools ==========

    async def list_mcp_servers(self, event) -> str:
        """列出可用的 MCP 服务器"""
        return await self._call_skill("mcp.list_servers", {})

    async def list_mcp_tools(self, event, server: str) -> str:
        """列出 MCP 服务器的工具

        Args:
            server: 服务器名称
        """
        return await self._call_skill("mcp.list_tools", {"server": server})

    async def call_mcp_tool(
        self, event, server: str, tool: str, arguments: Dict
    ) -> str:
        """调用 MCP 工具

        Args:
            server: 服务器名称
            tool: 工具名称
            arguments: 工具参数
        """
        return await self._call_skill(
            "mcp.call_tool", {"server": server, "tool": tool, "arguments": arguments}
        )

    # ========== GitHub 相关 Tools ==========

    async def github_batch_download(self, event, repos: List[Dict]) -> str:
        """批量下载 GitHub 仓库文件

        Args:
            repos: 仓库列表 [{"org": "org", "repo": "repo", "ref": "main"}]
        """
        return await self._call_skill("github.batch_download", {"repos": repos})

    async def document_convert(self, event, content_base64: str, filename: str) -> str:
        """文档格式转换

        Args:
            content_base64: 文件内容的base64编码
            filename: 文件名
        """
        return await self._call_skill(
            "document.convert", {"content_base64": content_base64, "filename": filename}
        )

    # ========== COS 存储相关 Tools ==========

    async def cos_save_file(
        self,
        event,
        key: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """保存文件到COS存储

        Args:
            key: 文件路径key
            content_base64: 文件内容的base64编码
            content_type: 文件MIME类型
        """
        return await self._call_skill(
            "cos.save_file",
            {
                "key": key,
                "content_base64": content_base64,
                "content_type": content_type,
            },
        )

    async def cos_delete_file(self, event, key: str) -> str:
        """删除COS存储中的文件

        Args:
            key: 文件路径key
        """
        return await self._call_skill("cos.delete_file", {"key": key})

    async def cos_get_presigned_url(
        self, event, key: str, expires_minutes: int = 60
    ) -> str:
        """获取COS文件的预签名下载URL

        Args:
            key: 文件路径key
            expires_minutes: URL有效期（分钟）
        """
        return await self._call_skill(
            "cos.get_presigned_url", {"key": key, "expires_minutes": expires_minutes}
        )

    async def cos_get_quota(self, event) -> str:
        """获取COS存储配额信息"""
        return await self._call_skill("cos.get_quota", {})

    # ========== 爬虫状态 Tools ==========

    async def crawl_status(self, event, job_id: str = "") -> str:
        """获取爬虫任务状态

        Args:
            job_id: 任务ID（为空则返回所有活跃任务）
        """
        return await self._call_skill("crawl4ai.status", {"job_id": job_id})

    # ========== RAG 相关 Tools ==========

    async def rag_ingest(
        self,
        event,
        source_type: str,
        source_name: str,
        content: str,
        target_repo: str = "HIT-A/HITA_RagData",
        auto_ingest_rag: bool = True,
    ) -> str:
        """RAG数据入库（直接入库到向量数据库）

        Args:
            source_type: 数据源类型（manual/github/url）
            source_name: 数据源名称
            content: 内容
            target_repo: 目标仓库
            auto_ingest_rag: 是否自动入RAG
        """
        return await self._call_skill(
            "rag.ingest",
            {
                "source_type": source_type,
                "source_name": source_name,
                "content": content,
                "target_repo": target_repo,
                "auto_ingest_rag": auto_ingest_rag,
            },
        )

    async def rag_sync_to_repo(
        self, event, target_repo: str = "HIT-A/HITA_RagData"
    ) -> str:
        """同步RAG数据到GitHub仓库

        Args:
            target_repo: 目标仓库
        """
        return await self._call_skill("rag.sync_to_repo", {"target_repo": target_repo})

    async def rag_intake_manual_folder(
        self, event, folder_path: str, target_repo: str = "HIT-A/HITA_RagData"
    ) -> str:
        """从本地文件夹手动导入RAG数据

        Args:
            folder_path: 文件夹路径
            target_repo: 目标仓库
        """
        return await self._call_skill(
            "rag.intake_manual_folder",
            {"folder_path": folder_path, "target_repo": target_repo},
        )

    # ========== 批量教师搜索 ==========

    async def teacher_batch_search(self, event, names: List[str]) -> str:
        """批量搜索教师信息

        Args:
            names: 教师姓名列表
        """
        return await self._call_skill("hit.teacher_batch", {"names": names})

    # ========== 群文件扫描 ==========

    async def scan_group_files(self, event, limit: int = 20) -> str:
        """扫描群文件并入库到知识库

        Args:
            event: 事件对象（包含群组信息）
            limit: 最大处理文件数
        """
        if not self.plugin or not self.file_scanner:
            return "错误: 插件或文件扫描服务未初始化"

        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            return "错误: 该指令仅在群聊中可用"

        try:

            async def _list_cb(gid: str):
                return await self.plugin._napcat_get_group_file_list(event, gid)

            async def _download_cb(file_ref):
                if isinstance(file_ref, dict):
                    return await self.plugin._napcat_download_group_file(
                        event, file_ref
                    )
                return await self.plugin._napcat_download_group_file(
                    event,
                    {"file_id": str(file_ref), "file_name": str(file_ref)},
                )

            # 先获取文件列表，汇报数量和预估时间
            file_list = await _list_cb(group_id)
            if not file_list:
                return "群文件列表为空"

            # 过滤支持的文件类型
            from urllib.parse import urlparse

            supported_exts = {
                ".pdf",
                ".doc",
                ".docx",
                ".ppt",
                ".pptx",
                ".txt",
                ".md",
                ".xls",
                ".xlsx",
            }
            valid_files = []
            for f in file_list:
                fname = f.get("file_name", "")
                ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                if ext in supported_exts:
                    valid_files.append(f)

            count = min(len(valid_files), limit)
            # 预估时间：每个文件约10秒
            est_time = count * 10

            if count == 0:
                return "没有找到支持的文件类型（PDF/Word/PPT/TXT/MD/Excel）"

            # 汇报后开始处理
            report = (
                f"发现 {len(valid_files)} 个文件，将处理前 {count} 个\n"
                f"预估耗时约 {est_time} 秒，请稍候...\n"
            )

            result = await self.file_scanner.scan_and_ingest_group_files(
                group_id,
                download_file_func=_download_cb,
                limit=limit,
                file_list=valid_files,
            )

            if result.error_message:
                return f"扫描入库失败: {result.error_message}"

            return (
                f"{report}\n"
                f"入库成功: {result.ingested_files}\n"
                f"跳过: {result.skipped_files}\n"
                f"失败: {result.failed_files}"
            )
        except Exception as e:
            return f"扫描入库异常: {str(e)}"


def create_hit_tools(
    agent_client: AgentClient, file_scanner=None, plugin=None
) -> List[FunctionTool]:
    """创建所有 HIT Agent Tools"""
    tools = HITAgentTools(agent_client, file_scanner, plugin)

    tool_defs = [
        # 群文件扫描
        FunctionTool(
            name="scan_group_files",
            description="扫描群文件并自动入库到知识库。当用户说要扫描、入库、上传群文件时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "最大处理文件数，默认20",
                    },
                },
            },
            handler=tools.scan_group_files,
        ),
        # 课程
        FunctionTool(
            name="search_courses",
            description="搜索课程信息。根据关键词搜索课程，返回课程列表。用于回答'这门课怎么样'、'某课程的老师是谁'等问题。",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，可以是课程名称、代码或教师名",
                    },
                    "limit": {"type": "integer", "description": "返回结果数量，默认10"},
                },
                "required": ["keyword"],
            },
            handler=tools.search_courses,
        ),
        FunctionTool(
            name="get_course_detail",
            description="获取课程的详细信息，包括课程介绍、教学大纲、教师信息等。需要提供校区和课程代码。",
            parameters={
                "type": "object",
                "properties": {
                    "campus": {
                        "type": "string",
                        "description": "校区：shenzhen(哈工深)、harbin(哈工大)、weihai(哈工威)",
                    },
                    "course_code": {
                        "type": "string",
                        "description": "课程代码，如 CS3011",
                    },
                    "include_toml": {
                        "type": "boolean",
                        "description": "是否包含TOML配置，默认false",
                    },
                },
                "required": ["campus", "course_code"],
            },
            handler=tools.get_course_detail,
        ),
        FunctionTool(
            name="search_teacher",
            description="搜索教师的详细信息，包括主页、邮箱、研究方向等。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "教师姓名"}},
                "required": ["name"],
            },
            handler=tools.search_teacher,
        ),
        # 搜索
        FunctionTool(
            name="unified_search",
            description="统一搜索接口，搜索课程资料、课件、笔记、试卷等。支持多个数据源。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "sources": {
                        "type": "array",
                        "description": "数据源列表，可选：rag(知识库)、brave(网页搜索)、arxiv(学术论文)、github、cos、course、course_read、hit_teacher",
                    },
                    "top_k": {"type": "integer", "description": "返回结果数量，默认10"},
                },
                "required": ["query"],
            },
            handler=tools.unified_search,
        ),
        FunctionTool(
            name="rag_query",
            description="在RAG知识库中搜索已入库的课程资料、评价等。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询内容"},
                    "top_k": {"type": "integer", "description": "返回结果数量，默认5"},
                },
                "required": ["query"],
            },
            handler=tools.rag_query,
        ),
        # PR
        FunctionTool(
            name="lookup_pr",
            description="查询GitHub PR的状态，检查课程资料PR是否已合并。",
            parameters={
                "type": "object",
                "properties": {
                    "org": {"type": "string", "description": "GitHub组织名"},
                    "repo": {"type": "string", "description": "仓库名"},
                    "number": {"type": "integer", "description": "PR编号"},
                },
                "required": ["org", "repo", "number"],
            },
            handler=tools.lookup_pr,
        ),
        # 文件
        FunctionTool(
            name="list_cos_files",
            description="列出COS存储中的文件。",
            parameters={
                "type": "object",
                "properties": {
                    "prefix": {"type": "string", "description": "文件路径前缀过滤"}
                },
            },
            handler=tools.list_cos_files,
        ),
        FunctionTool(
            name="crawl_page",
            description="抓取单个网页的内容，用于获取网页上的课程资料或信息。",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "网页URL"}},
                "required": ["url"],
            },
            handler=tools.crawl_page,
        ),
        # MCP
        FunctionTool(
            name="list_mcp_servers",
            description="列出可用的MCP服务器及其工具。",
            parameters={"type": "object", "properties": {}},
            handler=tools.list_mcp_servers,
        ),
        FunctionTool(
            name="list_mcp_tools",
            description="列出MCP服务器的可用工具。",
            parameters={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "服务器名称"}
                },
                "required": ["server"],
            },
            handler=tools.list_mcp_tools,
        ),
        FunctionTool(
            name="call_mcp_tool",
            description="调用MCP服务器的工具。",
            parameters={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "服务器名称"},
                    "tool": {"type": "string", "description": "工具名称"},
                    "arguments": {"type": "object", "description": "工具参数"},
                },
                "required": ["server", "tool"],
            },
            handler=tools.call_mcp_tool,
        ),
        # COS存储
        FunctionTool(
            name="cos_save_file",
            description="保存文件到COS存储。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "文件路径key"},
                    "content_base64": {
                        "type": "string",
                        "description": "文件内容的base64编码",
                    },
                    "content_type": {"type": "string", "description": "文件MIME类型"},
                },
                "required": ["key", "content_base64"],
            },
            handler=tools.cos_save_file,
        ),
        FunctionTool(
            name="cos_delete_file",
            description="删除COS存储中的文件。",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string", "description": "文件路径key"}},
                "required": ["key"],
            },
            handler=tools.cos_delete_file,
        ),
        FunctionTool(
            name="cos_get_presigned_url",
            description="获取COS文件的预签名下载URL。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "文件路径key"},
                    "expires_minutes": {
                        "type": "integer",
                        "description": "URL有效期（分钟），默认60",
                    },
                },
                "required": ["key"],
            },
            handler=tools.cos_get_presigned_url,
        ),
        FunctionTool(
            name="cos_get_quota",
            description="获取COS存储配额信息。",
            parameters={"type": "object", "properties": {}},
            handler=tools.cos_get_quota,
        ),
        # 爬虫
        FunctionTool(
            name="crawl_site",
            description="抓取整个网站的内容，从起始URL开始递归爬取。",
            parameters={
                "type": "object",
                "properties": {
                    "start_url": {"type": "string", "description": "起始URL"},
                    "max_pages": {
                        "type": "integer",
                        "description": "最大页面数，默认10",
                    },
                },
                "required": ["start_url"],
            },
            handler=tools.crawl_site,
        ),
        FunctionTool(
            name="crawl_status",
            description="获取爬虫任务状态。",
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "任务ID（为空则返回所有活跃任务）",
                    }
                },
            },
            handler=tools.crawl_status,
        ),
        # RAG
        FunctionTool(
            name="rag_ingest",
            description="RAG数据直接入库到向量数据库。",
            parameters={
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "description": "数据源类型（manual/github/url）",
                    },
                    "source_name": {"type": "string", "description": "数据源名称"},
                    "content": {"type": "string", "description": "内容"},
                    "target_repo": {"type": "string", "description": "目标仓库"},
                    "auto_ingest_rag": {
                        "type": "boolean",
                        "description": "是否自动入RAG，默认true",
                    },
                },
                "required": ["source_type", "source_name", "content"],
            },
            handler=tools.rag_ingest,
        ),
        FunctionTool(
            name="rag_sync_to_repo",
            description="同步RAG数据到GitHub仓库。",
            parameters={
                "type": "object",
                "properties": {
                    "target_repo": {"type": "string", "description": "目标仓库"}
                },
            },
            handler=tools.rag_sync_to_repo,
        ),
        FunctionTool(
            name="rag_intake_manual_folder",
            description="从本地文件夹手动导入RAG数据。",
            parameters={
                "type": "object",
                "properties": {
                    "folder_path": {"type": "string", "description": "文件夹路径"},
                    "target_repo": {"type": "string", "description": "目标仓库"},
                },
                "required": ["folder_path"],
            },
            handler=tools.rag_intake_manual_folder,
        ),
        # 批量教师搜索
        FunctionTool(
            name="teacher_batch_search",
            description="批量搜索教师信息。",
            parameters={
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "description": "教师姓名列表",
                        "items": {"type": "string"},
                    },
                },
                "required": ["names"],
            },
            handler=tools.teacher_batch_search,
        ),
        # GitHub
        FunctionTool(
            name="github_batch_download",
            description="批量下载GitHub仓库文件。",
            parameters={
                "type": "object",
                "properties": {
                    "repos": {
                        "type": "array",
                        "description": "仓库列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "org": {"type": "string", "description": "组织名"},
                                "repo": {"type": "string", "description": "仓库名"},
                                "ref": {"type": "string", "description": "分支或tag"},
                            },
                        },
                    },
                },
                "required": ["repos"],
            },
            handler=tools.github_batch_download,
        ),
        # 文档转换
        FunctionTool(
            name="document_convert",
            description="文档格式转换（如PDF转文本）。",
            parameters={
                "type": "object",
                "properties": {
                    "content_base64": {
                        "type": "string",
                        "description": "文件内容的base64编码",
                    },
                    "filename": {"type": "string", "description": "文件名"},
                },
                "required": ["content_base64", "filename"],
            },
            handler=tools.document_convert,
        ),
        # 文件操作
        FunctionTool(
            name="upload_file",
            description="上传文件到存储（files.upload）。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "文件路径key"},
                    "content_base64": {
                        "type": "string",
                        "description": "文件内容的base64编码",
                    },
                    "content_type": {"type": "string", "description": "文件MIME类型"},
                },
                "required": ["key", "content_base64"],
            },
            handler=tools.upload_file,
        ),
        FunctionTool(
            name="upload_and_ingest",
            description="上传文件到COS并自动入库（链条式操作，一步完成）。上传后自动解析并入库到Qdrant知识库。",
            parameters={
                "type": "object",
                "properties": {
                    "file_name": {"type": "string", "description": "文件名"},
                    "content_base64": {
                        "type": "string",
                        "description": "文件内容的base64编码",
                    },
                    "content_type": {
                        "type": "string",
                        "description": "文件MIME类型，如 application/pdf、image/jpeg",
                    },
                    "uploader_name": {
                        "type": "string",
                        "description": "上传者名称（可选）",
                    },
                },
                "required": ["file_name", "content_base64"],
            },
            handler=tools.upload_and_ingest,
        ),
        FunctionTool(
            name="download_file",
            description="下载存储中的文件。",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "文件路径key"},
                    "format": {"type": "string", "description": "返回格式（text/url）"},
                },
                "required": ["key"],
            },
            handler=tools.download_file,
        ),
    ]

    return tool_defs


class HITAgentSession:
    """HIT Agent 会话管理 - 支持多轮对话"""

    def __init__(self, event: AstrMessageEvent, tools: List[FunctionTool]):
        self.event = event
        self.tools = tools
        self.context_data: Dict[str, Any] = {}
        self.messages: List[Dict[str, str]] = []  # 对话历史

    def add_user_message(self, content: str):
        """添加用户消息"""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str):
        """添加助手消息"""
        self.messages.append({"role": "assistant", "content": content})

    def get_messages(self) -> List[Dict[str, str]]:
        """获取对话历史"""
        return self.messages

    def clear_messages(self):
        """清除对话历史"""
        self.messages.clear()

    def set_context(self, key: str, value: Any):
        """存储会话上下文"""
        self.context_data[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """获取会话上下文"""
        return self.context_data.get(key, default)

    def clear_context(self):
        """清除会话上下文"""
        self.context_data.clear()
        self.messages.clear()
