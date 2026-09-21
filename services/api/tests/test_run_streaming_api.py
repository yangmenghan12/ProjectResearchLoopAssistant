import json
from pathlib import Path

from app.engine.runner import NODE_ORDER
from tests.test_runs_api import EXPECTED_NODE_ORDER, build_client, create_collected_project


def parse_sse_frames(body: str) -> list[dict]:
    """解析测试客户端收到的 SSE 文本，便于断言事件契约。"""

    frames: list[dict] = []
    for raw_frame in body.strip().split("\n\n"):
        if not raw_frame.strip():
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        for line in raw_frame.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if event_name is None:
            continue
        frames.append({"event": event_name, "data": json.loads("\n".join(data_lines))})
    return frames


def test_stream_run_emits_langgraph_node_events_and_persists_package(tmp_path: Path):
    client = build_client()
    project = create_collected_project(client, tmp_path)

    response = client.post(f"/projects/{project['id']}/runs/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse_frames(response.text)
    assert [event["event"] for event in events] == ["run_queued", "run_started"] + [
        "node_completed"
    ] * len(EXPECTED_NODE_ORDER) + ["run_completed"]

    started = events[1]["data"]
    assert started["project_id"] == project["id"]
    assert started["status"] == "running"

    node_events = [event["data"] for event in events if event["event"] == "node_completed"]
    assert NODE_ORDER == EXPECTED_NODE_ORDER
    assert [event["node_name"] for event in node_events] == EXPECTED_NODE_ORDER
    assert [event["turn_index"] for event in node_events] == list(
        range(1, len(EXPECTED_NODE_ORDER) + 1)
    )
    assert all(event["orchestrator"] == "langgraph" for event in node_events)

    completed = events[-1]["data"]
    assert completed["status"] in {"ready_for_review", "ready_for_review_with_risks"}
    assert completed["turn_count"] == len(EXPECTED_NODE_ORDER)

    package_response = client.get(f"/projects/{project['id']}/runs/{completed['run_id']}")
    assert package_response.status_code == 200
    package = package_response.json()
    assert package["run"]["status"] == completed["status"]
    assert len(package["turns"]) == len(EXPECTED_NODE_ORDER)
    assert [turn["node_name"] for turn in package["turns"]] == EXPECTED_NODE_ORDER
    assert all(turn["metadata_json"]["orchestrator"] == "langgraph" for turn in package["turns"])
