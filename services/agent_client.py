"""
Agent Backend HTTP客户端 - 调用agent-backend的技能API
"""

import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx
from astrbot.api import logger


@dataclass
class AgentResponse:
    """Agent响应"""

    success: bool
    output: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    raw_response: Optional[Dict[str, Any]] = None


class AgentClient:
    """Agent Backend HTTP客户端"""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建HTTP客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        """关闭HTTP客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def invoke_skill(
        self,
        skill_name: str,
        input_data: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> AgentResponse:
        """
        调用Agent技能

        Args:
            skill_name: 技能名称（如 courses.search, files.upload 等）
            input_data: 输入数据
            timeout: 请求超时时间（可选，覆盖默认超时）

        Returns:
            AgentResponse
        """
        url = f"{self.base_url}/v1/skills/{skill_name}:invoke"
        headers = self._build_headers()

        payload = {"input": input_data}

        last_error = None
        for attempt in range(self.max_retries):
            try:
                client = await self._get_client()
                resp = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout or self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                # 解析响应
                if data.get("ok", False):
                    output = data.get("output")
                    # New async skills may return job_id without output payload.
                    if output is None and data.get("job_id"):
                        output = {
                            "job_id": data.get("job_id"),
                            "status": "queued",
                        }
                    return AgentResponse(
                        success=True,
                        output=output,
                        raw_response=data,
                    )
                else:
                    return AgentResponse(
                        success=False,
                        error=data.get("error"),
                        raw_response=data,
                    )

            except httpx.TimeoutException as e:
                last_error = f"请求超时 (尝试 {attempt + 1}/{self.max_retries})"
                logger.warning(f"⏱️ {last_error}: {skill_name}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)  # 指数退避

            except httpx.HTTPStatusError as e:
                last_error = f"HTTP错误 {e.response.status_code}: {e.response.text}"
                logger.error(f"❌ {last_error}")
                # 4xx错误不重试
                if 400 <= e.response.status_code < 500:
                    break

            except Exception as e:
                last_error = f"请求失败: {str(e)}"
                logger.error(f"❌ {last_error}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)

        return AgentResponse(
            success=False,
            error={"message": last_error or "未知错误"},
        )

    async def search_courses(
        self,
        keyword: str,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        """
        搜索课程

        Args:
            keyword: 搜索关键词
            limit: 返回结果数量限制
            filters: 过滤条件

        Returns:
            AgentResponse
        """
        input_data = {
            "keyword": keyword,
            "limit": limit,
        }
        if filters:
            input_data["filters"] = filters

        return await self.invoke_skill("courses.search", input_data)

    async def get_course_detail(
        self,
        course_code: str,
        campus: str = "shenzhen",
        include_toml: bool = False,
    ) -> AgentResponse:
        """
        获取课程详情

        Args:
            course_code: 课程代码
            campus: 校区
            include_toml: 是否返回 readme_toml

        Returns:
            AgentResponse
        """
        return await self.invoke_skill(
            "course.read",
            {
                "campus": campus,
                "course_code": course_code,
                "include_toml": include_toml,
            },
        )

    async def search_files(
        self,
        keyword: str,
        course_code: Optional[str] = None,
        file_type: Optional[str] = None,
        limit: int = 10,
    ) -> AgentResponse:
        """
        搜索文件

        Args:
            keyword: 搜索关键词
            course_code: 课程代码（可选）
            file_type: 文件类型（可选）
            limit: 返回结果数量限制

        Returns:
            AgentResponse
        """
        input_data = {
            "query": keyword,
            "sources": ["cos", "rag"],
            "top_k": limit,
            "summarize": False,
        }
        if course_code:
            input_data["course_code"] = course_code
        if file_type:
            input_data["file_type"] = file_type

        return await self.invoke_skill("search", input_data)

    async def upload_file(
        self,
        file_content: bytes,
        file_name: str,
        course_code: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        """
        上传文件

        Args:
            file_content: 文件内容（bytes）
            file_name: 文件名
            course_code: 关联课程代码（可选）
            description: 文件描述（可选）
            metadata: 元数据（可选）

        Returns:
            AgentResponse
        """
        import base64

        # 将文件内容转为base64
        file_base64 = base64.b64encode(file_content).decode("utf-8")

        input_data = {
            "file_name": file_name,
            "file_content": file_base64,
        }
        if course_code:
            input_data["course_code"] = course_code
        if description:
            input_data["description"] = description
        if metadata:
            input_data["metadata"] = metadata

        return await self.invoke_skill("files.upload", input_data, timeout=120.0)

    async def submit_review(
        self,
        course_code: str,
        content: str,
        ratings: Optional[Dict[str, int]] = None,
        semester: Optional[str] = None,
        teacher: Optional[str] = None,
        campus: str = "shenzhen",
        idempotency_key: Optional[str] = None,
    ) -> AgentResponse:
        """
        提交课程评价

        Args:
            course_code: 课程代码
            content: 评价内容
            ratings: 评分（如 {"content": 4, "teaching": 5, "overall": 4}）
            semester: 学期（如 "2024春"）
            teacher: 授课教师

        Returns:
            AgentResponse
        """
        op_content = content
        if ratings:
            op_content += (
                f"\n\n评分: 内容{ratings.get('content', '-')}/5, "
                f"教学{ratings.get('teaching', '-')}/5, "
                f"总体{ratings.get('overall', '-')}/5"
            )
        if semester:
            op_content += f"\n学期: {semester}"

        input_data = {
            "campus": campus,
            "course_code": course_code,
            "ops": [
                {
                    "op": "add_lecturer_review",
                    "lecturer_name": teacher or "匿名",
                    "content": op_content,
                }
            ],
        }
        if idempotency_key:
            input_data["idempotency_key"] = idempotency_key

        return await self.invoke_skill("pr.submit", input_data)

    async def create_pr(
        self,
        title: str,
        content: str,
        target_org: str,
        target_repo: str,
        branch: str = "main",
        files: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentResponse:
        """
        创建Pull Request

        Args:
            title: PR标题
            content: PR内容
            target_org: 目标组织
            target_repo: 目标仓库
            branch: 目标分支
            files: 要提交的文件列表（可选）

        Returns:
            AgentResponse
        """
        return AgentResponse(
            success=False,
            error={
                "message": (
                    "github.create_pr 已下线，请改用 pr.submit（课程变更）"
                    "或在调用方直接走 GitHub API。"
                )
            },
        )

    async def preview_pr(
        self,
        campus: str,
        course_code: str,
        ops: List[Dict[str, Any]],
    ) -> AgentResponse:
        """调用 pr.preview 预览课程变更。"""
        return await self.invoke_skill(
            "pr.preview",
            {
                "campus": campus,
                "course_code": course_code,
                "ops": ops,
            },
        )

    async def submit_pr(
        self,
        campus: str,
        course_code: str,
        ops: List[Dict[str, Any]],
        idempotency_key: Optional[str] = None,
        pr: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        """调用 pr.submit 提交课程改动。"""
        input_data: Dict[str, Any] = {
            "campus": campus,
            "course_code": course_code,
            "ops": ops,
        }
        if idempotency_key:
            input_data["idempotency_key"] = idempotency_key
        if pr:
            input_data["pr"] = pr
        return await self.invoke_skill("pr.submit", input_data)

    async def lookup_pr(self, org: str, repo: str, number: int) -> AgentResponse:
        """调用 pr.lookup 查询 PR 状态。"""
        return await self.invoke_skill(
            "pr.lookup",
            {
                "org": org,
                "repo": repo,
                "number": number,
            },
        )

    async def health_check(self) -> bool:
        """
        健康检查

        Returns:
            服务是否可用
        """
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/health",
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        return {
            "base_url": self.base_url,
            "api_configured": bool(self.api_key),
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "client_ready": self._client is not None and not self._client.is_closed,
        }
