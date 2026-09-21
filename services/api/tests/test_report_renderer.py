from app.reports.renderer import join_values, profile_section_body, render_profile_architecture_items


def test_join_values_localizes_tech_stack_and_limits_long_lists():
    rendered = join_values(
        [
            "python",
            "typescript",
            "javascript",
            "go",
            "java",
            "fastapi",
            "nextjs",
            "mysql",
            "docker",
        ]
    )

    assert "Python 语言" in rendered
    assert "TypeScript 前端类型语言" in rendered
    assert "javascript" not in rendered
    assert "docker" not in rendered
    assert "+3 more" in rendered


def test_architecture_profile_summarizes_file_tree_instead_of_dumping_it():
    inventory = {
        "codebase_maps": [
            {
                "root_label": "demo",
                "tech_stack_json": ["python", "typescript"],
                "entrypoint_files_json": ["backend/main.py", "frontend/src/main.ts"],
                "dependency_files_json": ["pyproject.toml", "package.json"],
                "config_files_json": ["docker-compose.yml", "tsconfig.json"],
                "test_files_json": ["tests/test_app.py"],
                "file_tree_json": [{"path": f"src/module_{index}.py", "kind": "file"} for index in range(30)],
            }
        ]
    }
    package = {"findings": [{"title": "服务边界清晰"}]}

    lines = render_profile_architecture_items(package, inventory)
    rendered = "\n".join(lines)

    assert "Python 语言" in rendered
    assert "backend/main.py" in rendered
    assert "pyproject.toml" in rendered
    assert "src/module_29.py" not in rendered


def test_architecture_profile_sections_have_specific_analysis():
    inventory = {
        "codebase_maps": [
            {
                "root_label": "demo",
                "tech_stack_json": ["python", "typescript"],
                "entrypoint_files_json": ["backend/main.py"],
                "dependency_files_json": ["pyproject.toml"],
                "config_files_json": ["docker-compose.yml"],
                "test_files_json": ["tests/test_app.py"],
                "file_tree_json": [
                    {"path": "backend/app/main.py", "kind": "file"},
                    {"path": "backend/app/services/report.py", "kind": "file"},
                    {"path": "frontend/app/page.tsx", "kind": "file"},
                    {"path": "tests/test_app.py", "kind": "file"},
                ],
            }
        ]
    }
    package = {
        "findings": [{"title": "后端和前端边界明确"}],
        "risks": [{"title": "外部依赖变更风险"}],
        "recommendations": [{"summary": "先从入口到服务层阅读"}],
    }

    module_lines = "\n".join(profile_section_body("模块边界", package, inventory))
    flow_lines = "\n".join(profile_section_body("主流程与数据流", package, inventory))
    extension_lines = "\n".join(profile_section_body("扩展点", package, inventory))

    assert module_lines != flow_lines
    assert flow_lines != extension_lines
    assert "backend" in module_lines
    assert "边界" in module_lines
    assert "主流程" in flow_lines
    assert "入口" in flow_lines
    assert "backend/main.py" in flow_lines
    assert "扩展" in extension_lines
    assert "配置" in extension_lines
    assert "docker-compose.yml" in extension_lines
