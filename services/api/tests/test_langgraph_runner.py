from app.engine.runner import NODE_ORDER
from tests.test_runs_api import build_client, create_collected_project, enqueue_and_process_run


def test_build_loop_graph_uses_langgraph_compiled_graph():
    from app.engine.langgraph_runner import LANGGRAPH_NODE_ORDER, build_loop_graph

    graph = build_loop_graph()

    assert LANGGRAPH_NODE_ORDER == NODE_ORDER
    assert graph.__class__.__module__.startswith("langgraph")


def test_run_turns_record_langgraph_orchestrator_metadata(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])
    turns = package["turns"]
    assert [turn["node_name"] for turn in turns] == NODE_ORDER
    assert all(turn["metadata_json"]["orchestrator"] == "langgraph" for turn in turns)


def test_loop_graph_state_is_serializable_and_records_thread_id(tmp_path):
    import json

    client = build_client()
    project = create_collected_project(client, tmp_path)

    package = enqueue_and_process_run(client, project["id"])
    run = package["run"]
    assert run["thread_id"]

    package = client.get(f"/projects/{project['id']}/runs/{run['id']}").json()
    for turn in package["turns"]:
        json.dumps(turn["metadata_json"])
        assert turn["metadata_json"]["orchestrator"] == "langgraph"
