"""
HITSZ 课程助手 - AstrBot 插件

功能：
- 课程查询（/搜, /查）
- RAG 问答（/问）
- PR 提交（/pr）

依赖：
- agent-backend (提供 skills API)
"""

import os
from typing import Optional
import httpx

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("astrbot_plugin_hitsz", "HIT-A", "HITSZ 课程助手", "1.0.0")
class HITSZPlugin(Star):
    """HITSZ 课程助手插件"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.agent_backend_url = os.getenv(
            "HITSZ_AGENT_BACKEND_URL", "http://localhost:8080"
        )
        self.api_key = os.getenv("HITSZ_AGENT_BACKEND_API_KEY", "")

    def _headers(self) -> dict:
        """获取请求头"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _call_skill(self, skill_name: str, input_data: dict) -> dict:
        """调用 agent-backend 的 skill"""
        url = f"{self.agent_backend_url}/v1/skills/{skill_name}:invoke"
        payload = {"input": input_data}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            return resp.json()

    # ==================== 课程查询指令 ====================

    @filter.command("搜")
    async def search_course(self, event: AstrMessageEvent, keyword: str):
        """搜索课程 - /搜 <关键词>"""
        if not keyword:
            yield event.plain_result("❌ 请输入搜索关键词\n用法: /搜 自动控制")
            return

        try:
            result = await self._call_skill(
                "courses.search", {"keyword": keyword, "limit": 10}
            )

            if not result.get("ok"):
                error = result.get("error", {})
                yield event.plain_result(
                    f"❌ 搜索失败: {error.get('message', '未知错误')}"
                )
                return

            output = result.get("output", {})
            courses = output.get("results", [])

            if not courses:
                yield event.plain_result(f"🔍 未找到包含「{keyword}」的课程")
                return

            # 构建结果消息
            msg = f"🔍 找到 {len(courses)} 个相关课程:\n\n"
            for i, course in enumerate(courses[:5], 1):
                code = course.get("code", "未知")
                name = course.get("name", "未知")
                msg += f"{i}. {code} - {name}\n"

            if len(courses) > 5:
                msg += f"\n... 还有 {len(courses) - 5} 个结果"

            msg += "\n\n💡 使用 /查 <课程代码> 查看详情"
            yield event.plain_result(msg)

        except Exception as e:
            logger.error(f"搜索课程失败: {e}")
            yield event.plain_result(f"❌ 搜索失败: {str(e)}")

    @filter.command("查")
    async def query_course(self, event: AstrMessageEvent, course_code: str):
        """查询课程详情 - /查 <课程代码>"""
        if not course_code:
            yield event.plain_result("❌ 请输入课程代码\n用法: /查 AUTO1001")
            return

        try:
            result = await self._call_skill(
                "course.read", {"course_code": course_code.upper()}
            )

            if not result.get("ok"):
                error = result.get("error", {})
                yield event.plain_result(
                    f"❌ 查询失败: {error.get('message', '未找到该课程')}"
                )
                return

            output = result.get("output", {})
            data = output.get("data", {})

            # 构建课程详情
            msg = f"📚 {course_code.upper()}\n"
            msg += "=" * 30 + "\n\n"

            if "result" in data:
                result_data = data["result"]
                readme_md = result_data.get("readme_md", "")
                # 截取前 500 字符
                if len(readme_md) > 500:
                    readme_md = readme_md[:500] + "...\n\n[内容过长，已截断]"
                msg += readme_md

            yield event.plain_result(msg)

        except Exception as e:
            logger.error(f"查询课程失败: {e}")
            yield event.plain_result(f"❌ 查询失败: {str(e)}")

    # ==================== RAG 问答指令 ====================

    @filter.command("问")
    async def ask_question(self, event: AstrMessageEvent, question: str):
        """RAG 问答 - /问 <问题>"""
        if not question:
            yield event.plain_result("❌ 请输入问题\n用法: /问 图书馆几点开门")
            return

        try:
            # 先调用 search 获取相关文档
            yield event.plain_result("🤔 正在搜索相关知识...")

            search_result = await self._call_skill(
                "search",
                {"query": question, "sources": ["rag"], "top_k": 5, "summarize": True},
            )

            if not search_result.get("ok"):
                error = search_result.get("error", {})
                yield event.plain_result(
                    f"❌ 搜索失败: {error.get('message', '未知错误')}"
                )
                return

            output = search_result.get("output", {})
            summary = output.get("summary", "")

            if summary and summary != "总结功能待实现":
                msg = f"💡 答案:\n\n{summary}\n\n"
                key_points = output.get("key_points", [])
                if key_points:
                    msg += "📌 要点:\n"
                    for point in key_points:
                        msg += f"• {point}\n"
                yield event.plain_result(msg)
            else:
                # 返回原始搜索结果
                results = output.get("results", [])
                if results:
                    msg = "📚 找到以下相关信息:\n\n"
                    for i, r in enumerate(results[:3], 1):
                        title = r.get("title", "未知")
                        content = r.get("content", "")[:200]
                        msg += f"{i}. {title}\n{content}...\n\n"
                    yield event.plain_result(msg)
                else:
                    yield event.plain_result("😅 抱歉，没有找到相关知识")

        except Exception as e:
            logger.error(f"RAG 问答失败: {e}")
            yield event.plain_result(f"❌ 问答失败: {str(e)}")

    # ==================== PR 提交指令组 ====================

    @filter.command_group("pr")
    def pr_group(self):
        """PR 提交相关指令"""
        pass

    @pr_group.command("help")
    async def pr_help(self, event: AstrMessageEvent):
        """PR 帮助"""
        msg = """📋 PR 提交指南

开始提交:
  /pr start <课程代码>
  例: /pr start AUTO1001

查看当前内容:
  /pr show

添加内容:
  /pr add <章节标题>
  然后发送要添加的内容

修改内容:
  /pr modify
  然后按提示操作

取消提交:
  /pr cancel
"""
        yield event.plain_result(msg)

    @pr_group.command("start")
    async def pr_start(self, event: AstrMessageEvent, course_code: str):
        """开始 PR 提交流程 - /pr start <课程代码>"""
        if not course_code:
            yield event.plain_result("❌ 请输入课程代码\n用法: /pr start AUTO1001")
            return

        # 保存会话状态到 KV 存储
        await self.put_kv_data(
            f"pr_session_{event.unified_msg_origin}",
            {"course_code": course_code.upper(), "step": "started"},
        )

        yield event.plain_result(
            f"✅ 已启动 PR 提交流程\n"
            f"课程: {course_code.upper()}\n\n"
            f"接下来您可以:\n"
            f"• /pr show - 查看当前课程内容\n"
            f"• /pr add <章节> - 添加新内容\n"
            f"• /pr modify - 修改现有内容\n"
            f"• /pr cancel - 取消提交"
        )

    @pr_group.command("show")
    async def pr_show(self, event: AstrMessageEvent):
        """查看当前课程内容 - /pr show"""
        session = await self.get_kv_data(f"pr_session_{event.unified_msg_origin}")
        if not session:
            yield event.plain_result(
                "❌ 没有活跃的 PR 会话，请先使用 /pr start <课程代码>"
            )
            return

        course_code = session.get("course_code")

        try:
            # 调用 course.read 获取内容
            result = await self._call_skill("course.read", {"course_code": course_code})

            if not result.get("ok"):
                yield event.plain_result(
                    f"⚠️ 课程 {course_code} 暂无内容，可以添加新内容"
                )
                return

            output = result.get("output", {})
            data = output.get("data", {})

            if "result" in data:
                toml_content = data["result"].get("readme_toml", "")
                # 分段显示，避免消息过长
                if len(toml_content) > 1000:
                    toml_content = toml_content[:1000] + "\n\n[内容过长，已截断]"

                yield event.plain_result(
                    f"📄 {course_code} 当前内容:\n\n```toml\n{toml_content}\n```"
                )
            else:
                yield event.plain_result("⚠️ 暂无内容")

        except Exception as e:
            logger.error(f"查看课程失败: {e}")
            yield event.plain_result(f"❌ 查看失败: {str(e)}")

    @pr_group.command("cancel")
    async def pr_cancel(self, event: AstrMessageEvent):
        """取消 PR 提交 - /pr cancel"""
        session_key = f"pr_session_{event.unified_msg_origin}"
        session = await self.get_kv_data(session_key)

        if not session:
            yield event.plain_result("❌ 没有活跃的 PR 会话")
            return

        await self.delete_kv_data(session_key)
        yield event.plain_result("✅ 已取消 PR 提交")

    @pr_group.command("add")
    async def pr_add(self, event: AstrMessageEvent, section: str = ""):
        """添加内容 - /pr add <章节标题>"""
        session = await self.get_kv_data(f"pr_session_{event.unified_msg_origin}")
        if not session:
            yield event.plain_result(
                "❌ 没有活跃的 PR 会话，请先使用 /pr start <课程代码>"
            )
            return

        if not section:
            yield event.plain_result("❌ 请输入章节标题\n用法: /pr add 关于考试")
            return

        # TODO: 实现多轮对话获取内容
        yield event.plain_result(
            f"📝 准备添加「{section}」章节\n请直接发送要添加的内容，或发送「取消」放弃"
        )

    @pr_group.command("submit")
    async def pr_submit(self, event: AstrMessageEvent):
        """提交 PR - /pr submit"""
        session = await self.get_kv_data(f"pr_session_{event.unified_msg_origin}")
        if not session:
            yield event.plain_result("❌ 没有活跃的 PR 会话")
            return

        course_code = session.get("course_code")
        ops = session.get("ops", [])

        if not ops:
            yield event.plain_result("⚠️ 没有要提交的修改")
            return

        try:
            # 调用 pr.submit
            yield event.plain_result("🚀 正在提交 PR...")

            result = await self._call_skill(
                "pr.submit",
                {
                    "campus": "shenzhen",  # 默认深圳校区
                    "course_code": course_code,
                    "ops": ops,
                },
            )

            if not result.get("ok"):
                error = result.get("error", {})
                yield event.plain_result(
                    f"❌ 提交失败: {error.get('message', '未知错误')}"
                )
                return

            output = result.get("output", {})
            pr_url = output.get("pr_url", "")

            if pr_url:
                yield event.plain_result(f"✅ PR 提交成功！\n\n🔗 {pr_url}")
            else:
                yield event.plain_result("✅ PR 提交成功！")

            # 清理会话
            await self.delete_kv_data(f"pr_session_{event.unified_msg_origin}")

        except Exception as e:
            logger.error(f"提交 PR 失败: {e}")
            yield event.plain_result(f"❌ 提交失败: {str(e)}")

    # ==================== 其他指令 ====================

    @filter.command("帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        """显示帮助信息"""
        msg = """🎓 HITSZ 课程助手

📚 课程查询:
  /搜 <关键词> - 搜索课程
  /查 <课程代码> - 查看课程详情

💬 RAG 问答:
  /问 <问题> - 基于知识库问答

📝 PR 提交:
  /pr start <课程代码> - 开始提交
  /pr show - 查看当前内容
  /pr add <章节> - 添加内容
  /pr submit - 提交 PR
  /pr cancel - 取消提交
  /pr help - 详细帮助

💡 提示:
  所有指令都可以通过 /帮助 查看说明
"""
        yield event.plain_result(msg)

    async def terminate(self):
        """插件卸载时清理资源"""
        logger.info("HITSZ 课程助手插件已卸载")
