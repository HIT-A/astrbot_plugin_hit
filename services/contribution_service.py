"""
智能贡献引导服务 - 自动检测贡献意图并引导PR流程
"""

import re
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

from astrbot.api import logger


class ContributionType(Enum):
    """贡献类型"""

    COURSE_REVIEW = "course_review"  # 课程评价
    COURSE_MATERIAL = "course_material"  # 课程资料
    COURSE_INFO = "course_info"  # 课程信息
    GENERAL_FEEDBACK = "general_feedback"  # 一般反馈
    UNKNOWN = "unknown"


class ContributionStep(Enum):
    """贡献流程步骤"""

    DETECTED = "detected"  # 检测到意图
    CONFIRMING_TYPE = "confirming_type"  # 确认贡献类型
    COLLECTING_INFO = "collecting_info"  # 收集信息
    GENERATING_PR = "generating_pr"  # 生成PR内容
    CONFIRMING_PR = "confirming_pr"  # 确认PR
    SUBMITTING = "submitting"  # 提交中
    COMPLETED = "completed"  # 完成
    CANCELLED = "cancelled"  # 取消


@dataclass
class ContributionSession:
    """贡献会话"""

    session_id: str
    user_id: str
    group_id: Optional[str]
    contribution_type: ContributionType = ContributionType.UNKNOWN
    current_step: ContributionStep = ContributionStep.DETECTED
    collected_data: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    pr_content: Optional[str] = None
    pr_title: Optional[str] = None


class ContributionService:
    """智能贡献引导服务"""

    # 贡献意图关键词
    CONTRIBUTION_KEYWORDS = {
        ContributionType.COURSE_REVIEW: [
            "评价",
            "评测",
            "体验",
            "感受",
            "上过",
            "选过",
            "给分",
            "作业多",
            "考试难",
            "推荐",
            "避雷",
            "水课",
            "硬核",
        ],
        ContributionType.COURSE_MATERIAL: [
            "资料",
            "课件",
            "笔记",
            "试卷",
            "真题",
            "答案",
            "教材",
            "参考书",
            "ppt",
            "pdf",
            "文档",
        ],
        ContributionType.COURSE_INFO: [
            "课程信息",
            "学分",
            "学时",
            "先修",
            "后续",
            "培养方案",
            "教学大纲",
        ],
    }

    # PR模板
    PR_TEMPLATES = {
        ContributionType.COURSE_REVIEW: """## 课程评价

**课程代码**: {course_code}
**课程名称**: {course_name}
**授课教师**: {teacher}
**学期**: {semester}

### 评价内容

{content}

### 评分

- 课程内容: {content_score}/5
- 教学水平: {teaching_score}/5
- 作业负担: {workload_score}/5
- 考试难度: {exam_score}/5
- 总体推荐: {overall_score}/5

---
贡献者: {contributor}
""",
        ContributionType.COURSE_MATERIAL: """## 课程资料贡献

**课程代码**: {course_code}
**课程名称**: {course_name}
**资料类型**: {material_type}
**文件列表**:
{file_list}

### 说明

{description}

---
贡献者: {contributor}
""",
    }

    def __init__(
        self,
        default_target_org: str = "HITSZ-OpenAuto",
        session_timeout_minutes: int = 30,
    ):
        self.default_target_org = default_target_org
        self.session_timeout = session_timeout_minutes * 60  # 转换为秒
        self._sessions: Dict[str, ContributionSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动服务"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("✅ 贡献引导服务已启动")

    async def stop(self):
        """停止服务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 贡献引导服务已停止")

    async def _cleanup_loop(self):
        """清理过期会话的循环"""
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟清理一次
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 清理会话失败: {e}")

    async def _cleanup_expired_sessions(self):
        """清理过期会话"""
        now = datetime.now()
        expired_sessions = []

        async with self._lock:
            for session_id, session in self._sessions.items():
                elapsed = (now - session.last_updated).total_seconds()
                if elapsed > self.session_timeout:
                    expired_sessions.append(session_id)

            for session_id in expired_sessions:
                del self._sessions[session_id]

        if expired_sessions:
            logger.info(f"🧹 清理了 {len(expired_sessions)} 个过期贡献会话")

    def detect_contribution_intent(
        self, content: str
    ) -> tuple[bool, ContributionType, float]:
        """
        检测消息中的贡献意图

        Args:
            content: 消息内容

        Returns:
            (是否有贡献意图, 贡献类型, 置信度)
        """
        content_lower = content.lower()
        scores = {}

        for contrib_type, keywords in self.CONTRIBUTION_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in content_lower:
                    score += 1
            scores[contrib_type] = score

        if not scores or max(scores.values()) == 0:
            return False, ContributionType.UNKNOWN, 0.0

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]
        confidence = min(best_score / 3, 1.0)  # 最多3个关键词匹配达到1.0置信度

        # 额外的意图确认模式
        contribution_patterns = [
            r"我[有来].*?(?:资料|笔记|试卷|评价)",
            r"(?:分享|贡献|上传).*?(?:课程|资料)",
            r"这[门个].*?(?:课|老师).*?(?:怎么样|如何)",
        ]

        for pattern in contribution_patterns:
            if re.search(pattern, content):
                confidence = min(confidence + 0.3, 1.0)
                break

        return confidence >= 0.5, best_type, confidence

    async def create_session(
        self,
        user_id: str,
        group_id: Optional[str],
        contribution_type: ContributionType,
    ) -> ContributionSession:
        """
        创建贡献会话

        Args:
            user_id: 用户ID
            group_id: 群ID（可选）
            contribution_type: 贡献类型

        Returns:
            ContributionSession
        """
        session_id = f"{user_id}_{int(datetime.now().timestamp())}"

        session = ContributionSession(
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            contribution_type=contribution_type,
            current_step=ContributionStep.CONFIRMING_TYPE,
        )

        async with self._lock:
            self._sessions[session_id] = session

        logger.info(f"✅ 创建贡献会话: {session_id}, 类型: {contribution_type.value}")
        return session

    async def get_session(self, session_id: str) -> Optional[ContributionSession]:
        """获取会话"""
        async with self._lock:
            return self._sessions.get(session_id)

    async def update_session(
        self, session_id: str, data: Dict[str, Any]
    ) -> Optional[ContributionSession]:
        """
        更新会话数据

        Args:
            session_id: 会话ID
            data: 要更新的数据

        Returns:
            更新后的会话或None
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            session.collected_data.update(data)
            session.last_updated = datetime.now()
            return session

    async def advance_step(
        self, session_id: str, new_step: ContributionStep
    ) -> Optional[ContributionSession]:
        """
        推进会话到下一步

        Args:
            session_id: 会话ID
            new_step: 新步骤

        Returns:
            更新后的会话或None
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            session.current_step = new_step
            session.last_updated = datetime.now()
            logger.info(f"➡️ 会话 {session_id} 进入步骤: {new_step.value}")
            return session

    def generate_guidance_message(self, session: ContributionSession) -> str:
        """
        生成引导消息

        Args:
            session: 贡献会话

        Returns:
            引导消息文本
        """
        step = session.current_step
        contrib_type = session.contribution_type

        if step == ContributionStep.CONFIRMING_TYPE:
            type_names = {
                ContributionType.COURSE_REVIEW: "课程评价",
                ContributionType.COURSE_MATERIAL: "课程资料",
                ContributionType.COURSE_INFO: "课程信息",
            }
            type_name = type_names.get(contrib_type, "内容")

            return f"""💡 我检测到您想分享{type_name}！

这太棒了！您的分享将帮助其他同学更好地了解课程。

请确认您想贡献的类型:
1. 课程评价 - 分享您的上课体验和评价
2. 课程资料 - 分享课件、笔记、试卷等资料
3. 其他内容

请回复数字 1/2/3 或直接告诉我您想分享什么~"""

        elif step == ContributionStep.COLLECTING_INFO:
            if contrib_type == ContributionType.COURSE_REVIEW:
                return """📝 请提供以下信息:

1. **课程代码** (如 CS101)
2. **课程名称**
3. **授课教师**
4. **学期** (如 2024春)
5. **您的评价** (课程内容、作业量、考试难度等)

您可以一次性发送所有信息，也可以分条发送~"""

            elif contrib_type == ContributionType.COURSE_MATERIAL:
                return """📚 请提供以下信息:

1. **课程代码** (如 CS101)
2. **课程名称**
3. **资料类型** (课件/笔记/试卷/答案等)
4. **文件说明**
5. **上传文件** 或直接发送文件

请发送相关信息~"""

        elif step == ContributionStep.CONFIRMING_PR:
            if session.pr_title and session.pr_content:
                return f"""📋 已为您生成PR内容，请确认:

**标题**: {session.pr_title}

**内容预览**:
```
{session.pr_content[:500]}...
```

是否确认提交？回复:
- "确认" 或 "是" - 提交PR
- "修改" - 重新编辑
- "取消" - 放弃本次贡献"""

        return "请继续提供信息~"

    def generate_pr_content(self, session: ContributionSession) -> bool:
        """
        生成PR内容

        Args:
            session: 贡献会话

        Returns:
            是否成功生成
        """
        data = session.collected_data
        contrib_type = session.contribution_type

        if contrib_type == ContributionType.COURSE_REVIEW:
            template = self.PR_TEMPLATES[ContributionType.COURSE_REVIEW]
            session.pr_title = f"Add review for {data.get('course_code', 'Unknown')}"
            session.pr_content = template.format(
                course_code=data.get("course_code", ""),
                course_name=data.get("course_name", ""),
                teacher=data.get("teacher", ""),
                semester=data.get("semester", ""),
                content=data.get("content", ""),
                content_score=data.get("content_score", 4),
                teaching_score=data.get("teaching_score", 4),
                workload_score=data.get("workload_score", 3),
                exam_score=data.get("exam_score", 3),
                overall_score=data.get("overall_score", 4),
                contributor=data.get("contributor", "Anonymous"),
            )
            return True

        elif contrib_type == ContributionType.COURSE_MATERIAL:
            template = self.PR_TEMPLATES[ContributionType.COURSE_MATERIAL]
            session.pr_title = f"Add materials for {data.get('course_code', 'Unknown')}"
            files = data.get("files", [])
            file_list = "\n".join([f"- {f}" for f in files]) if files else "- (待上传)"

            session.pr_content = template.format(
                course_code=data.get("course_code", ""),
                course_name=data.get("course_name", ""),
                material_type=data.get("material_type", ""),
                file_list=file_list,
                description=data.get("description", ""),
                contributor=data.get("contributor", "Anonymous"),
            )
            return True

        return False

    async def cancel_session(self, session_id: str) -> bool:
        """
        取消会话

        Args:
            session_id: 会话ID

        Returns:
            是否成功取消
        """
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"❌ 会话已取消: {session_id}")
                return True
            return False

    def get_active_sessions(
        self, user_id: Optional[str] = None
    ) -> List[ContributionSession]:
        """
        获取活跃会话

        Args:
            user_id: 用户ID（可选，用于过滤）

        Returns:
            会话列表
        """
        sessions = list(self._sessions.values())
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        return sessions

    def get_status(self) -> Dict[str, Any]:
        """获取服务状态"""
        return {
            "active_sessions": len(self._sessions),
            "default_target_org": self.default_target_org,
            "session_timeout_minutes": self.session_timeout / 60,
        }
