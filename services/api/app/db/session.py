from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
WorkerSessionLocal = SessionLocal


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def bind_worker_session_to(session: Session) -> None:
    """让本地 worker 复用当前运行环境的数据库 bind。

    生产环境默认使用 MySQL `SessionLocal`；测试和单进程开发环境会通过 FastAPI
    dependency override 注入内存库或临时库，enqueue 时同步 worker 的 session factory，
    避免 API 和 worker 看见两套数据库。
    """

    global WorkerSessionLocal
    WorkerSessionLocal = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)


def get_worker_session_factory() -> sessionmaker[Session]:
    return WorkerSessionLocal
