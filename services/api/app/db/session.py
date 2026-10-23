from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
WorkerSessionLocal = SessionLocal


def get_session() -> Generator[Session, None, None]:
    """为单个 FastAPI 请求提供并关闭一个数据库 session。

    Args: 无。
    Returns: 只产生一个 Session 的生成器。
    Raises: session 创建或数据库连接异常向上抛出。
    副作用：打开并关闭数据库连接；由 FastAPI dependency 注入调用。
    """
    with SessionLocal() as session:
        yield session


def bind_worker_session_to(session: Session) -> None:
    """让本地 worker 复用当前运行环境的数据库 bind。

    生产环境默认使用 MySQL `SessionLocal`；测试和单进程开发环境会通过 FastAPI
    dependency override 注入内存库或临时库，enqueue 时同步 worker 的 session factory，
    避免 API 和 worker 看见两套数据库。

    Args: session 为要复用其 bind 的现有 SQLAlchemy session。
    Returns: None。
    Raises: session 未绑定 engine 时可能抛出 SQLAlchemy 异常。
    副作用：修改模块级 `WorkerSessionLocal`；由 enqueue 流程调用。
    """

    global WorkerSessionLocal
    WorkerSessionLocal = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)


def get_worker_session_factory() -> sessionmaker[Session]:
    """返回当前 worker 使用的 session factory。

    Args: 无。
    Returns: SQLAlchemy sessionmaker。
    Raises: 不主动抛出异常。
    副作用：仅读取模块级绑定；由 worker 和 SSE 流调用。
    """
    return WorkerSessionLocal
