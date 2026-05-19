"""
配置管理模块
"""

import os
from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class PluginConfig:
    """插件配置"""

    # AI配置（MiniMax ChatCompletion v2）
    glm_api_key: str
    glm_base_url: str = "https://api.minimaxi.com"
    glm_intent_model: str = "MiniMax-M2.7"
    glm_complex_model: str = "MiniMax-M2.7"

    # Agent Backend配置
    agent_backend_url: str = "http://139.199.173.108:8080"
    agent_backend_api_key: str = ""

    # 定时任务配置
    intent_check_interval: int = 180  # 3分钟
    daily_summary_time: str = "22:30"
    file_scan_cron: str = "0 2 * * *"  # 每天凌晨2点
    file_scan_interval_hours: int = 6  # 定时扫描间隔（小时），0表示禁用

    # 额度限制（滚动窗口）
    glm_quota_limit: int = 1500
    glm_quota_window_hours: int = 5
    # 兼容旧字段：等价于 glm_quota_limit
    glm_daily_quota: int = 1500

    # 文件处理
    max_file_size_mb: int = 50
    supported_extensions: list = None

    # 上下文管理
    context_window_minutes: int = 3
    max_context_length: int = 4000

    # 报错诊断日志
    error_log_enabled: bool = True

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

        def _env_bool(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        glm_api_key = os.getenv("HITSZ_AI_API_KEY", "") or os.getenv(
            "HITSZ_GLM_API_KEY", ""
        )
        glm_intent_model = os.getenv("HITSZ_AI_INTENT_MODEL", "") or os.getenv(
            "HITSZ_GLM_INTENT_MODEL", "MiniMax-M2.7"
        )
        glm_complex_model = os.getenv("HITSZ_AI_COMPLEX_MODEL", "") or os.getenv(
            "HITSZ_GLM_COMPLEX_MODEL", "MiniMax-M2.7"
        )

        quota_limit = int(os.getenv("HITSZ_AI_QUOTA_LIMIT", "1500"))

        return PluginConfig(
            glm_api_key=glm_api_key,
            glm_base_url=os.getenv("HITSZ_AI_BASE_URL", "")
            or os.getenv("HITSZ_GLM_BASE_URL", "https://api.minimaxi.com"),
            glm_intent_model=glm_intent_model,
            glm_complex_model=glm_complex_model,
            agent_backend_url=os.getenv(
                "HITSZ_AGENT_BACKEND_URL", "http://139.199.173.108:8080"
            ),
            agent_backend_api_key=os.getenv("HITSZ_AGENT_BACKEND_API_KEY", ""),
            intent_check_interval=int(os.getenv("HITSZ_INTENT_CHECK_INTERVAL", "180")),
            daily_summary_time=os.getenv("HITSZ_DAILY_SUMMARY_TIME", "22:30"),
            glm_quota_limit=quota_limit,
            glm_quota_window_hours=int(os.getenv("HITSZ_AI_QUOTA_WINDOW_HOURS", "5")),
            glm_daily_quota=quota_limit,
            max_file_size_mb=int(os.getenv("HITSZ_MAX_FILE_SIZE_MB", "50")),
            context_window_minutes=int(os.getenv("HITSZ_CONTEXT_WINDOW_MINUTES", "3")),
            error_log_enabled=_env_bool("HITSZ_ERROR_LOG_ENABLED", True),
            file_scan_interval_hours=int(
                os.getenv("HITSZ_FILE_SCAN_INTERVAL_HOURS", "6")
            ),
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
