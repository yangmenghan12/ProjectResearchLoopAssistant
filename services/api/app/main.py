from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
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
        return {
            "status": "ok",
            "app": settings.app_name,
        }

    return app


app = create_app()
