from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_assistant,
    routes_demo,
    routes_evaluations,
    routes_events,
    routes_inventory,
    routes_llm,
    routes_projects,
    routes_research_profiles,
    routes_reports,
    routes_runs,
    routes_source_bundles,
    routes_sources,
    routes_tools,
)
from app.core.config import get_settings


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用，以集中注册 API 路由和跨域策略。

    Args: 无。
    Returns: FastAPI，已挂载全部业务路由的应用实例。
    Raises: 配置读取失败时可能抛出配置相关异常。
    副作用：读取环境配置并修改应用对象；由 ASGI 入口调用。
    """
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_demo.router)
    app.include_router(routes_assistant.router)
    app.include_router(routes_evaluations.router)
    app.include_router(routes_projects.router)
    app.include_router(routes_research_profiles.router)
    app.include_router(routes_source_bundles.router)
    app.include_router(routes_sources.router)
    app.include_router(routes_inventory.router)
    app.include_router(routes_events.router)
    app.include_router(routes_llm.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_reports.router)
    app.include_router(routes_tools.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """返回进程存活状态，供启动检查和外部健康探针使用。

        Args: 无。
        Returns: 包含 `status` 和应用名的字典。
        Raises: 不主动抛出业务异常。
        副作用：仅读取已加载配置；由健康检查请求调用。
        """
        return {
            "status": "ok",
            "app": settings.app_name,
        }

    return app


app = create_app()
