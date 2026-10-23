from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """应用配置固定从仓库根目录读取，避免不同启动目录导致配置漂移。

    属性说明：`app_name` 为应用名；`database_url` 为 SQLAlchemy 连接串；`workspace_root` 为受控工作区；
    `source_*` 和 `*_timeout_seconds` 为采集限制；`llm_*` 为默认 LLM provider、认证和调用参数。
    """

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    app_name: str = "Loop Engineering API"  # FastAPI 应用名称。
    database_url: str = "mysql+pymysql://root@localhost:3306/loop_engineering"  # 数据库连接串。
    workspace_root: Path = REPO_ROOT / "workspace" / "projects"  # Git clone 和 demo 的受控根目录。
    source_max_file_bytes: int = 1_000_000  # 单个来源文件允许读取的最大字节数。
    source_excerpt_chars: int = 4_000  # 来源摘要最多保留的字符数。
    web_fetch_timeout_seconds: float = 20  # 网页请求超时时间，单位为秒。
    git_clone_timeout_seconds: float = 120  # Git clone 命令超时时间，单位为秒。
    llm_provider: str = "openai-compatible"  # 默认 LLM provider 名称。
    llm_api_key: str | None = None  # 默认 provider 的 API key；未配置时为 None。
    llm_base_url: str = ""  # 默认 provider 的兼容 API 基地址。
    llm_model: str = ""  # 默认 provider 使用的模型名。
    llm_api_key_required: bool = True  # 是否要求默认 provider 提供 API key。
    llm_timeout_seconds: float = 30  # 默认 LLM 请求超时时间，单位为秒。

    @model_validator(mode="after")
    def normalize_paths(self) -> "Settings":
        """相对运行目录会漂移，路径配置统一按仓库根目录解析。"""

        if not self.workspace_root.is_absolute():
            self.workspace_root = (REPO_ROOT / self.workspace_root).resolve()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
