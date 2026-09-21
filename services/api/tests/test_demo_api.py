from tests.test_runs_api import build_client


def test_create_code_analysis_demo_project_builds_reviewable_package():
    client = build_client()

    response = client.post("/demo/projects/code-analysis")

    assert response.status_code == 201
    project = response.json()
    assert project["name"].startswith("Demo Code Analysis")
    assert project["status"] in {"ready_for_review", "ready_for_review_with_risks"}

    sources_response = client.get(f"/projects/{project['id']}/sources")
    inventory_response = client.get(f"/projects/{project['id']}/inventory")
    runs_response = client.get(f"/projects/{project['id']}/runs")
    latest_report_response = client.get(f"/projects/{project['id']}/reports/latest")

    assert sources_response.status_code == 200
    assert len(sources_response.json()) == 1
    assert sources_response.json()[0]["source_type"] == "local_directory"
    normalized_uri = sources_response.json()[0]["normalized_uri"].replace("\\", "/")
    assert "/workspace/projects/" in normalized_uri
    assert "/services/api/workspace/" not in normalized_uri

    assert inventory_response.status_code == 200
    inventory = inventory_response.json()
    assert len(inventory["snapshots"]) == 1
    assert len(inventory["codebase_maps"]) == 1
    assert inventory["codebase_maps"][0]["test_files_json"]

    assert runs_response.status_code == 200
    assert len(runs_response.json()) == 1

    assert latest_report_response.status_code == 200
    report = latest_report_response.json()
    assert report["verifier_result_json"]["checks"]
    assert "# Demo Code Analysis 项目调研报告" in report["markdown_content"]
