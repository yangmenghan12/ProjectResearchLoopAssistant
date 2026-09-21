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

        return cls(session_factory=sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False))

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")
        checkpoint_copy = dict(checkpoint)
        channel_values = dict(checkpoint_copy.pop("channel_values", {}))

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
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        with self._read_session() as session:
            row = (
                self._get_checkpoint(session, thread_id, checkpoint_ns, checkpoint_id)
                if checkpoint_id
                else self._latest_checkpoint(session, thread_id, checkpoint_ns)
            )
            if row is None:
                return None
            return self._to_tuple(session, row)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
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

        with self._read_session() as session:
            tuples: list[CheckpointTuple] = []
            for row in session.scalars(statement).all():
                if filter and not all(row.metadata_json.get(key) == value for key, value in filter.items()):
                    continue
                tuples.append(self._to_tuple(session, row))

        yield from tuples

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._write_session() as session:
            for index, (channel, value) in enumerate(writes):
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
    if SERDE_TYPE_KEY in value:
        return SERDE.loads_typed(
            (
                value[SERDE_TYPE_KEY],
                base64.b64decode(value[SERDE_DATA_KEY].encode("ascii")),
            )
        )
    return value.get(VALUE_KEY)
