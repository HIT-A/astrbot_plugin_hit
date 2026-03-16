"""
配置管理模块
"""

import os
from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class PluginConfig:
    """插件配置"""

    # Gemini配置
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash-preview-05-20"

    # Agent Backend配置
    agent_backend_url: str = "http://localhost:8080"
    agent_backend_api_key: str = ""

    # 定时任务配置
    intent_check_interval: int = 180  # 3分钟
    daily_summary_time: str = "22:30"
    file_scan_cron: str = "0 2 * * *"  # 每天凌晨2点

    # 额度限制
    gemini_daily_quota: int = 250

    # 文件处理
    max_file_size_mb: int = 50
    supported_extensions: list = None

    # 上下文管理
    context_window_minutes: int = 3
    max_context_length: int = 4000

    def __post_init__(self):
        if self.supported_extensions is None:
            self.supported_extensions = [
                "pdf",
                "doc",
                "docx",
                "ppt",
                "pptx",
                "txt",
                "md",
                "zip",
            ]


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self.config = self._load_config()
        self.group_configs: Dict[str, Dict[str, Any]] = {}

    def _load_config(self) -> PluginConfig:
        """从环境变量加载配置"""
        return PluginConfig(
            gemini_api_key=os.getenv("HITSZ_GEMINI_API_KEY", ""),
            gemini_model=os.getenv(
                "HITSZ_GEMINI_MODEL", "gemini-2.5-flash-preview-05-20"
            ),
            agent_backend_url=os.getenv(
                "HITSZ_AGENT_BACKEND_URL", "http://localhost:8080"
            ),
            agent_backend_api_key=os.getenv("HITSZ_AGENT_BACKEND_API_KEY", ""),
            intent_check_interval=int(os.getenv("HITSZ_INTENT_CHECK_INTERVAL", "180")),
            daily_summary_time=os.getenv("HITSZ_DAILY_SUMMARY_TIME", "22:30"),
            max_file_size_mb=int(os.getenv("HITSZ_MAX_FILE_SIZE_MB", "50")),
            context_window_minutes=int(os.getenv("HITSZ_CONTEXT_WINDOW_MINUTES", "3")),
        )

    def get_group_config(self, group_id: str) -> Dict[str, Any]:
        """获取群配置"""
        return self.group_configs.get(
            group_id,
            {
                "campus": "shenzhen",
                "pr_target_org": "HITSZ-OpenAuto",
                "auto_review": True,
                "admin_users": [],
            },
        )

    def set_group_config(self, group_id: str, config: Dict[str, Any]):
        """设置群配置"""
        self.group_configs[group_id] = config

    def is_admin(self, group_id: str, user_id: str) -> bool:
        """检查是否为管理员"""
        group_config = self.get_group_config(group_id)
        return user_id in group_config.get("admin_users", [])
