from tests.test_reports_api import create_project_with_run


def test_report_pdf_export_returns_pdf_bytes(tmp_path):
    client, project, _ = create_project_with_run(tmp_path)
    report = client.post(f"/projects/{project['id']}/reports").json()

    response = client.get(f"/projects/{project['id']}/reports/{report['id']}/export.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
