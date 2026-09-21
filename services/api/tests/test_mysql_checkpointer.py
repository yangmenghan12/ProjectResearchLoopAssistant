from collections.abc import Iterator

from langgraph.checkpoint.base import CheckpointTuple
from sqlalchemy.orm import Session

from app.engine.checkpointer import SqlAlchemyCheckpointSaver
from tests.test_runs_api import build_client


def open_test_session() -> Iterator[Session]:
    client = build_client()
    override_session = next(iter(client.app.dependency_overrides.values()))
    yield from override_session()


def test_sqlalchemy_checkpointer_put_get_and_list():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        saver = SqlAlchemyCheckpointSaver(session)
        config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "checkpoint-1",
            "ts": "2026-07-01T00:00:00+00:00",
            "channel_values": {"project_id": "project-1"},
            "channel_versions": {"project_id": "1"},
            "versions_seen": {},
            "pending_sends": [],
        }

        returned_config = saver.put(config, checkpoint, {"source": "unit"}, {"project_id": "1"})
        loaded = saver.get_tuple(returned_config)
        listed = list(saver.list({"configurable": {"thread_id": "thread-1"}}))

        assert returned_config["configurable"]["checkpoint_id"] == "checkpoint-1"
        assert isinstance(loaded, CheckpointTuple)
        assert loaded.checkpoint["channel_values"]["project_id"] == "project-1"
        assert loaded.metadata["source"] == "unit"
        assert listed[0].checkpoint["id"] == "checkpoint-1"
    finally:
        session_iter.close()


def test_sqlalchemy_checkpointer_put_writes_round_trip():
    session_iter = open_test_session()
    session = next(session_iter)
    try:
        saver = SqlAlchemyCheckpointSaver(session)
        config = {"configurable": {"thread_id": "thread-2", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "checkpoint-2",
            "ts": "2026-07-01T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        returned_config = saver.put(config, checkpoint, {}, {})
        saver.put_writes(returned_config, [("__interrupt__", {"reason": "need input"})], "task-1")
        loaded = saver.get_tuple(returned_config)

        assert loaded.pending_writes == [("task-1", "__interrupt__", {"reason": "need input"})]
    finally:
        session_iter.close()
