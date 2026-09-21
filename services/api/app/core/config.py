from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """应用配置固定从仓库根目录读取，避免不同启动目录导致配置漂移。"""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    app_name: str = "Loop Engineering API"
    database_url: str = "mysql+pymysql://root@localhost:3306/loop_engineering"
    workspace_root: Path = REPO_ROOT / "workspace" / "projects"
    source_max_file_bytes: int = 1_000_000
    source_excerpt_chars: int = 4_000
    web_fetch_timeout_seconds: float = 20
    git_clone_timeout_seconds: float = 120
    llm_provider: str = "openai-compatible"
    llm_api_key: str | None = None
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key_required: bool = True
    llm_timeout_seconds: float = 30

    @model_validator(mode="after")
    def normalize_paths(self) -> "Settings":
        """相对运行目录会漂移，路径配置统一按仓库根目录解析。"""

        if not self.workspace_root.is_absolute():
            self.workspace_root = (REPO_ROOT / self.workspace_root).resolve()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
