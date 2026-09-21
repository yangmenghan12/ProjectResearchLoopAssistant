from tests.test_runs_api import build_client, create_collected_project, enqueue_and_process_run


def test_missing_goal_pauses_with_langgraph_interrupt_metadata(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path, analysis_goal=None)

    first_package = enqueue_and_process_run(client, project["id"])

    assert first_package["run"]["status"] == "paused_for_input"
    assert first_package["run"]["thread_id"]
    assert first_package["questions"][0]["status"] == "open"
    assert any(
        turn["metadata_json"].get("pause_source") == "langgraph_interrupt"
        for turn in first_package["turns"]
    )


def test_resume_uses_same_thread_after_answer(tmp_path):
    client = build_client()
    project = create_collected_project(client, tmp_path, analysis_goal=None)
    first_package = enqueue_and_process_run(client, project["id"])
    thread_id = first_package["run"]["thread_id"]
    question_id = first_package["questions"][0]["id"]

    client.post(
        f"/projects/{project['id']}/questions/{question_id}/answer",
        json={"answer_text": "Assess adoption."},
    )
    resumed = client.post(f"/projects/{project['id']}/runs/{first_package['run']['id']}/resume").json()

    assert resumed["run"]["thread_id"] == thread_id
    assert resumed["run"]["resume_count"] == 1
    assert resumed["run"]["status"] in {"ready_for_review", "ready_for_review_with_risks"}
