from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from threading import RLock
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import GraphCheckpoint, GraphCheckpointWrite

VALUE_KEY = "__value__"
SERDE_TYPE_KEY = "__serde_type__"
SERDE_DATA_KEY = "__serde_data__"
SERDE = JsonPlusSerializer()


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """MySQL/SQLAlchemy backed LangGraph checkpointer.

    checkpoint 用于支撑恢复执行；用户可见事实仍来自 Analysis Package 相关表。
    当前实现覆盖 LangGraph 同步执行路径需要的最小接口。
    """

    def __init__(
        self,
        session: Session | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ):
        """初始化 checkpoint saver，并选择复用 Session 或按需创建 Session。

        Args: session 为单线程复用的数据库会话；session_factory 为独立 checkpoint 会话工厂，二者至少提供一个。
        Returns: None。
        Raises: session 和 session_factory 同时为空时抛出 ValueError。
        副作用：保存数据库访问依赖；不立即读写数据库。
        """
        super().__init__()
        if session is None and session_factory is None:
            raise ValueError("session or session_factory is required")
        self.session = session
        self.session_factory = session_factory
        self._session_lock = RLock()

    @classmethod
    def from_session_bind(cls, session: Session) -> "SqlAlchemyCheckpointSaver":
        """基于业务 session 的 bind 创建线程隔离的 checkpoint saver。

        LangGraph 会在后台线程中调用 checkpointer。SQLAlchemy Session 不是线程安全对象，
        因此运行图默认不能复用业务写入的 session，而是按每次 checkpoint 回调打开短 session。
        """

        return cls(
            session_factory=sessionmaker(
                # 复用业务 Session 所连接的 Engine/Connection 配置，但不复用 Session 实例。
                bind=session.get_bind(),
                # checkpoint 每次回调都使用独立事务，避免自动 flush 把中间状态带入业务事务。
                autoflush=False,
                # 由 checkpoint saver 的上下文管理器显式提交或回滚事务。
                autocommit=False,
            )
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> RunnableConfig:
        """保存一个 LangGraph checkpoint，并返回带 checkpoint_id 的配置。

        Args: config 含 thread/checkpoint 标识；checkpoint 为图状态；metadata 为节点元数据；new_versions 为版本信息。
        Returns: 可用于后续 graph 调用的 checkpoint 配置。
        Raises: 数据库写入或状态序列化失败时抛出异常。
        副作用：使用独立短 Session 写入 GraphCheckpoint；由 LangGraph 调用。
        """
        thread_id = config["configurable"]["thread_id"]
        # checkpoint_ns 用于区分同一 thread 下的不同命名空间，默认图使用空字符串。
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        # 当前快照的唯一 ID，由 LangGraph 生成并用于后续恢复。
        checkpoint_id = checkpoint["id"]
        # 父 checkpoint ID 用于串联状态版本，支持按历史版本恢复。
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")
        # 复制 checkpoint，避免修改 LangGraph 传入的原始对象。
        checkpoint_copy = dict(checkpoint)
        # channel_values 单独存入数据库字段，其他 checkpoint 元数据存入 JSON 字段。
        channel_values = dict(checkpoint_copy.pop("channel_values", {}))

        # channel_values 与其他 checkpoint 元数据分开保存，匹配数据库字段结构。
        with self._write_session() as session:
            row = self._get_checkpoint(session, thread_id, checkpoint_ns, checkpoint_id)
            if row is None:
                row = GraphCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                )
                session.add(row)
            row.parent_checkpoint_id = parent_checkpoint_id
            row.checkpoint_json = checkpoint_copy
            row.metadata_json = dict(metadata)
            row.channel_values_json = channel_values
        # 返回后续 graph 调用需要的定位信息，而不是返回数据库 ORM 行。
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """读取指定 checkpoint，未指定 ID 时返回该 thread 的最新 checkpoint。

        Args: config 至少包含 thread_id，可选 checkpoint_ns/checkpoint_id。
        Returns: CheckpointTuple；找不到时返回 None。
        Raises: 数据库读取或反序列化失败时抛出异常。
        副作用：使用独立只读 Session 访问 checkpoint 表；由 LangGraph 恢复流程调用。
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        # 有 checkpoint_id 时读取指定版本，否则读取该 thread 的最新版本。
        checkpoint_id = get_checkpoint_id(config)
        with self._read_session() as session:
            row = (
                self._get_checkpoint(session, thread_id, checkpoint_ns, checkpoint_id)
                if checkpoint_id
                else self._latest_checkpoint(session, thread_id, checkpoint_ns)
            )
            if row is None:
                return None
            # CheckpointTuple 包含状态、metadata、父版本和 pending writes。
            return self._to_tuple(session, row)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """按过滤条件迭代返回 checkpoint，支持 LangGraph 的历史检查。

        Args: config/before/filter/limit 为 LangGraph 查询条件。
        Returns: CheckpointTuple 迭代器。
        Raises: 数据库读取或反序列化失败时抛出异常。
        副作用：使用独立 Session 读取 checkpoint；由 LangGraph 调用。
        """
        statement = select(GraphCheckpoint)
        if config:
            statement = statement.where(GraphCheckpoint.thread_id == config["configurable"]["thread_id"])
            checkpoint_ns = config["configurable"].get("checkpoint_ns")
            if checkpoint_ns is not None:
                statement = statement.where(GraphCheckpoint.checkpoint_ns == checkpoint_ns)
            checkpoint_id = get_checkpoint_id(config)
            if checkpoint_id:
                statement = statement.where(GraphCheckpoint.checkpoint_id == checkpoint_id)
        if before and (before_checkpoint_id := get_checkpoint_id(before)):
            statement = statement.where(GraphCheckpoint.checkpoint_id < before_checkpoint_id)
        statement = statement.order_by(GraphCheckpoint.checkpoint_id.desc(), GraphCheckpoint.id.desc())
        if limit is not None:
            statement = statement.limit(limit)

        # 先在独立只读 Session 中组装完整结果，再在 Session 关闭前转成普通 tuple。
        with self._read_session() as session:
            tuples: list[CheckpointTuple] = []
            for row in session.scalars(statement).all():
                if filter and not all(row.metadata_json.get(key) == value for key, value in filter.items()):
                    continue
                tuples.append(self._to_tuple(session, row))

        # 生成器返回按 checkpoint_id 倒序排列的历史快照。
        yield from tuples

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """保存 checkpoint 节点产生的 pending writes，支持 interrupt/resume。

        Args: config 标识 checkpoint；writes 为 channel/value 写入序列；task_id/task_path 标识 graph 任务。
        Returns: None。
        Raises: 数据库写入或值序列化失败时抛出异常。
        副作用：写入 GraphCheckpointWrite；由 LangGraph checkpoint 流程调用。
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        # pending write 必须绑定到明确的 checkpoint 版本，才能在 interrupt 后恢复。
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._write_session() as session:
            for index, (channel, value) in enumerate(writes):
                # index 是同一 task 内 write 的稳定序号，用于幂等去重。
                existing = self._get_write(session, thread_id, checkpoint_ns, checkpoint_id, task_id, index)
                if existing is not None:
                    continue
                session.add(
                    GraphCheckpointWrite(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        checkpoint_id=checkpoint_id,
                        task_id=task_id,
                        write_index=index,
                        channel=channel,
                    value_json=pack_checkpoint_value(value),
                        task_path=task_path,
                    )
                )

    @contextmanager
    def _read_session(self) -> Iterator[Session]:
        """提供 checkpoint 读取 Session；工厂模式下每次创建短 Session。

        Args: 无。
        Returns: 上下文管理器，产出一个 Session。
        Raises: 未配置数据库访问对象时抛出 RuntimeError。
        副作用：可能创建并关闭数据库 Session；由 checkpoint 读方法调用。
        """
        if self.session_factory is None:
            if self.session is None:
                raise RuntimeError("Checkpoint session is not configured")
            with self._session_lock:
                yield self.session
            return

        with self._session_lock:
            with self.session_factory() as session:
                yield session

    @contextmanager
    def _write_session(self) -> Iterator[Session]:
        """提供 checkpoint 写入 Session，并在退出上下文时提交事务。

        Args: 无。
        Returns: 上下文管理器，产出一个 Session。
        Raises: 未配置数据库访问对象或提交失败时抛出异常。
        副作用：创建 Session、写入并 commit checkpoint 数据；由 checkpoint 写方法调用。
        """
        if self.session_factory is None:
            if self.session is None:
                raise RuntimeError("Checkpoint session is not configured")
            with self._session_lock:
                yield self.session
                self.session.flush()
            return

        with self._session_lock:
            with self.session_factory() as session:
                yield session
                session.commit()

    def _get_checkpoint(
        self,
        session: Session,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> GraphCheckpoint | None:
        return session.scalar(
            select(GraphCheckpoint).where(
                GraphCheckpoint.thread_id == thread_id,
                GraphCheckpoint.checkpoint_ns == checkpoint_ns,
                GraphCheckpoint.checkpoint_id == checkpoint_id,
            )
        )

    def _latest_checkpoint(self, session: Session, thread_id: str, checkpoint_ns: str) -> GraphCheckpoint | None:
        return session.scalar(
            select(GraphCheckpoint)
            .where(GraphCheckpoint.thread_id == thread_id, GraphCheckpoint.checkpoint_ns == checkpoint_ns)
            .order_by(GraphCheckpoint.checkpoint_id.desc(), GraphCheckpoint.id.desc())
        )

    def _get_write(
        self,
        session: Session,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        task_id: str,
        write_index: int,
    ) -> GraphCheckpointWrite | None:
        return session.scalar(
            select(GraphCheckpointWrite).where(
                GraphCheckpointWrite.thread_id == thread_id,
                GraphCheckpointWrite.checkpoint_ns == checkpoint_ns,
                GraphCheckpointWrite.checkpoint_id == checkpoint_id,
                GraphCheckpointWrite.task_id == task_id,
                GraphCheckpointWrite.write_index == write_index,
            )
        )

    def _writes_for(
        self,
        session: Session,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[GraphCheckpointWrite]:
        return list(
            session.scalars(
                select(GraphCheckpointWrite)
                .where(
                    GraphCheckpointWrite.thread_id == thread_id,
                    GraphCheckpointWrite.checkpoint_ns == checkpoint_ns,
                    GraphCheckpointWrite.checkpoint_id == checkpoint_id,
                )
                .order_by(GraphCheckpointWrite.task_id.asc(), GraphCheckpointWrite.write_index.asc())
            ).all()
        )

    def _to_tuple(self, session: Session, row: GraphCheckpoint) -> CheckpointTuple:
        checkpoint = {**row.checkpoint_json, "channel_values": row.channel_values_json}
        pending_writes = [
            (write.task_id, write.channel, unpack_checkpoint_value(write.value_json))
            for write in self._writes_for(session, row.thread_id, row.checkpoint_ns, row.checkpoint_id)
        ]
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=row.metadata_json,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=pending_writes,
        )


def pack_value(value: Any) -> dict[str, Any]:
    """把任意 JSON 值包成 dict，匹配当前模型字段类型。"""

    return {VALUE_KEY: value}


def unpack_value(value: dict[str, Any]) -> Any:
    """从 checkpoint value 包装字典中取出 JSON 值。

    Args: value 为由 `pack_value` 生成的字典。
    Returns: 包装的值；缺少键时为 None。
    Raises: 无显式异常。
    副作用：无；由 checkpoint 反序列化流程调用。
    """
    return value.get(VALUE_KEY)


def pack_checkpoint_value(value: Any) -> dict[str, Any]:
    """把 pending write 包成 JSON dict，必要时使用 LangGraph serializer。

    interrupt 会写入 `Interrupt` 对象；这类值不是 JSON 原生类型，但 resume 又需要
    原样还原，所以不能简单转成字符串。
    """

    try:
        json.dumps(value)
    except TypeError:
        serde_type, data = SERDE.dumps_typed(value)
        return {
            SERDE_TYPE_KEY: serde_type,
            SERDE_DATA_KEY: base64.b64encode(data).decode("ascii"),
        }
    return {VALUE_KEY: value}


def unpack_checkpoint_value(value: dict[str, Any]) -> Any:
    """还原 checkpoint write 中的 JSON 值或 LangGraph typed 序列化对象。

    Args: value 为由 `pack_checkpoint_value` 生成的字典。
    Returns: 原始 Python 值。
    Raises: typed 数据损坏或 base64/serializer 解码失败时抛出异常。
    副作用：无；由 pending write 恢复流程调用。
    """
    if SERDE_TYPE_KEY in value:
        return SERDE.loads_typed(
            (
                value[SERDE_TYPE_KEY],
                base64.b64decode(value[SERDE_DATA_KEY].encode("ascii")),
            )
        )
    return value.get(VALUE_KEY)
