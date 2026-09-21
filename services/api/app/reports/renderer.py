from typing import Any

from app.verifiers.package import as_list, value_of

DEFAULT_LIST_LIMIT = 6

TECH_STACK_LABELS = {
    "python": "Python 语言",
    "typescript": "TypeScript 前端类型语言",
    "javascript": "JavaScript 脚本语言",
    "go": "Go 语言",
    "java": "Java 语言",
    "fastapi": "FastAPI 后端框架",
    "nextjs": "Next.js 前端框架",
    "mysql": "MySQL 数据库",
    "docker": "Docker 容器化",
}


PROJECT_RESEARCH_SECTION_HEADINGS = [
    "工程接手指南",
    "启动与运行线索",
    "架构地图",
    "证据索引",
    "后续阅读路径",
]

PROFILE_AWARE_SECTION_HEADINGS = [
    "调研 Profile",
    "资料包摘要",
]


def render_report_markdown(
    project: Any,
    package: dict[str, Any],
    inventory: dict[str, Any],
    verifier_result: dict[str, Any],
) -> str:
    """把 Analysis Package 渲染成稳定的中文 Markdown 报告快照。

    报告是可审计产物：它不重新推理，只忠实呈现已经落库的证据、校验结果和人工可复核的结论。
    """

    lines: list[str] = [
        f"# {text(value_of(project, 'name', '项目'))} 项目调研报告",
        "",
        "## 项目概览",
        "",
        f"- 项目 ID：`{text(value_of(project, 'id', 'unknown'))}`",
        f"- 主题：{text(value_of(project, 'topic', ''))}",
        f"- 分析目标：{text(value_of(project, 'analysis_goal', '未填写'))}",
        f"- 项目状态：`{status_label(text(value_of(project, 'status', 'unknown')))}`",
        "",
    ]

    lines.extend(render_profile_summary(package, verifier_result))
    lines.extend(render_source_bundle_summary(inventory))
    lines.extend(
        [
            "## 校验摘要",
            "",
            f"- 是否通过：`{bool_label(bool(verifier_result.get('passed', False)))}`",
            f"- 风险等级：`{risk_label(text(verifier_result.get('risk_level', 'unknown')))}`",
            f"- 摘要：{text(verifier_result.get('summary', ''))}",
            "",
        ]
    )
    lines.extend(render_executive_summary(project, package, verifier_result))
    lines.extend(render_onboarding_guide(project, package, inventory))
    lines.extend(render_profile_report_sections(package, inventory))
    lines.extend(render_runbook_clues(inventory))
    lines.extend(render_architecture_map(inventory, package))
    lines.extend(render_evidence_index(package))
    lines.extend(render_reading_path(package, inventory))
    lines.extend(render_evidence_chain(package))
    lines.extend(render_llm_assisted_verifier(verifier_result))
    lines.extend(render_checks(verifier_result))
    lines.extend(render_source_inventory(inventory))
    lines.extend(render_evidence(package))
    lines.extend(render_findings(package))
    lines.extend(render_risks(package))
    lines.extend(render_questions(package))
    lines.extend(render_recommendations(package))
    lines.extend(render_timeline(package))
    return "\n".join(lines).strip() + "\n"


def render_profile_summary(package: dict[str, Any], verifier_result: dict[str, Any]) -> list[str]:
    profile = profile_info(package, verifier_result)
    lines = ["## 调研 Profile", ""]
    if not profile:
        lines.append("- 当前报告未声明调研档位，按默认快速接手档位处理。")
        lines.append("")
        return lines

    lines.append(f"- 档位：{text(value_of(profile, 'label', '未命名'))} (`{text(value_of(profile, 'key', 'unknown'))}`)")
    description = text(value_of(profile, "description", ""))
    if description:
        lines.append(f"- 说明：{description}")
    checklist = as_list(value_of(profile, "checklist", []))
    if checklist:
        lines.append(f"- 核心检查：{join_values(checklist)}")
    required_evidence = as_list(value_of(profile, "required_evidence_types", []))
    if required_evidence:
        lines.append(f"- 需要证据类型：{join_values(required_evidence)}")
    lines.append("")
    return lines


def render_source_bundle_summary(inventory: dict[str, Any]) -> list[str]:
    lines = ["## 资料包摘要", ""]
    bundles = as_list(inventory.get("source_bundles"))
    if not bundles:
        lines.append("- 暂未登记资料包，报告基于已采集输入源生成。")
        lines.append("")
        return lines

    for bundle in bundles:
        lines.append(
            f"- {text(value_of(bundle, 'name', '未命名资料包'))} "
            f"(`{status_label(text(value_of(bundle, 'status', 'unknown')))}`)"
        )
        lines.append(
            f"  - 条目：`{text(value_of(bundle, 'item_count', 0))}`；"
            f"已采集：`{text(value_of(bundle, 'collected_count', 0))}`；"
            f"失败：`{text(value_of(bundle, 'failed_count', 0))}`；"
            f"待处理：`{text(value_of(bundle, 'pending_count', 0))}`"
        )
    lines.append("")
    return lines


def render_executive_summary(project: Any, package: dict[str, Any], verifier_result: dict[str, Any]) -> list[str]:
    findings_count = len(as_list(package.get("findings")))
    risks_count = len(as_list(package.get("risks")))
    passed = bool(verifier_result.get("passed", False))
    risk_level = text(verifier_result.get("risk_level", "unknown"))
    summary = text(verifier_result.get("summary", ""))
    conclusion = f"{bool_label(passed)}，风险等级：{risk_label(risk_level)}"
    if summary:
        conclusion = f"{conclusion}；{summary}"

    return [
        "## 执行摘要",
        "",
        f"- 项目：{text(value_of(project, 'name', '项目'))}",
        f"- 发现数量：`{findings_count}`",
        f"- 风险数量：`{risks_count}`",
        f"- 校验结论：{conclusion}",
        "",
    ]


def render_profile_report_sections(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    profile = profile_info(package, {})
    sections = [text(section) for section in as_list(value_of(profile, "report_sections", [])) if text(section)]
    if not sections:
        return []

    lines: list[str] = []
    for heading in sections:
        lines.extend([f"## {heading}", ""])
        lines.extend(profile_section_body(heading, package, inventory))
        lines.append("")
    return lines


def profile_section_body(heading: str, package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    if heading == "课程章节建议":
        return render_course_chapter_suggestions(package, inventory)
    if heading == "关键代码讲解顺序":
        return render_code_walkthrough_order(inventory)
    if heading == "练习任务":
        return render_practice_tasks(package, inventory)
    if heading in {"新人接手路径", "30 分钟阅读路线"}:
        return render_profile_reading_items(inventory)
    if heading == "模块边界":
        return render_module_boundary_items(package, inventory)
    if heading == "主流程与数据流":
        return render_main_flow_items(package, inventory)
    if heading == "扩展点":
        return render_extension_point_items(package, inventory)
    if heading in {"二开可行性", "改造风险", "测试与配置复杂度"}:
        return render_profile_extension_items(package, inventory)
    if heading in {"交付完整性", "可启动性", "验收问题清单"}:
        return render_profile_delivery_items(package, inventory)
    return ["- 当前调研包暂未生成该档位的专属结构化内容。"]


def render_course_chapter_suggestions(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    tech_stack = collect_codebase_values(inventory, "tech_stack_json")
    return [
        f"- 章节 1：项目目标与技术栈，重点覆盖 {join_values(unique_values(tech_stack))}。",
        "- 章节 2：入口、配置和运行线索，先建立可启动的整体认知。",
        "- 章节 3：关键发现、风险和开放问题，训练证据化分析能力。",
    ]


def render_code_walkthrough_order(inventory: dict[str, Any]) -> list[str]:
    ordered_files = []
    ordered_files.extend(collect_codebase_values(inventory, "entrypoint_files_json"))
    ordered_files.extend(collect_codebase_values(inventory, "config_files_json"))
    ordered_files.extend(collect_codebase_values(inventory, "test_files_json"))
    ordered_files.extend(collect_codebase_values(inventory, "dependency_files_json"))
    ordered_files = unique_values(ordered_files)
    if not ordered_files:
        return ["- 暂未识别关键代码文件，请先补充代码目录或 README 输入源。"]
    return [f"- 第 {index} 步：`{text(path)}`" for index, path in enumerate(ordered_files[:8], start=1)]


def render_practice_tasks(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    tasks = [
        "复现启动路径：根据报告中的入口、配置和依赖线索完成一次本地启动检查。",
        "证据追踪：任选一个发现，回到证据索引确认其引用是否充分。",
    ]
    test_files = collect_codebase_values(inventory, "test_files_json")
    if test_files:
        tasks.append(f"测试验证：阅读或运行 {join_values(unique_values(test_files)[:3])}。")
    if as_list(package.get("risks")):
        tasks.append("风险演练：选择一条风险，补充一个可执行的缓解或验证动作。")
    return [f"- {task}" for task in tasks]


def render_profile_reading_items(inventory: dict[str, Any]) -> list[str]:
    items = []
    items.extend(collect_codebase_values(inventory, "entrypoint_files_json"))
    items.extend(collect_codebase_values(inventory, "config_files_json"))
    items.extend(collect_codebase_values(inventory, "test_files_json"))
    if not items:
        return ["- 暂未识别明确阅读路径。"]
    return [f"- `{item}`" for item in unique_values(items)[:8]]


def render_profile_architecture_items(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    lines = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        root_label = text(value_of(codebase_map, "root_label", "codebase"))
        entrypoints = value_of(codebase_map, "entrypoint_files_json", [])
        dependencies = value_of(codebase_map, "dependency_files_json", [])
        configs = value_of(codebase_map, "config_files_json", [])
        tests = value_of(codebase_map, "test_files_json", [])
        file_tree = as_list(value_of(codebase_map, "file_tree_json", []))
        file_count = sum(1 for item in file_tree if value_of(item, "kind") == "file")
        directory_count = sum(1 for item in file_tree if value_of(item, "kind") == "directory")

        lines.append(f"- `{root_label}`")
        lines.append(f"  - 技术栈和特性：{join_values(value_of(codebase_map, 'tech_stack_json', []))}")
        lines.append(f"  - 模块入口：{join_values(entrypoints)}")
        lines.append(f"  - 依赖与外部边界：{join_values(dependencies)}")
        lines.append(f"  - 配置边界：{join_values(configs)}")
        lines.append(f"  - 测试验证线索：{join_values(tests)}")
        lines.append(f"  - 目录规模：约 `{file_count}` 个文件、`{directory_count}` 个目录；完整文件清单保留在代码库地图页，不在报告正文展开。")
    for finding in as_list(package.get("findings"))[:3]:
        lines.append(f"- 结构发现：{text(value_of(finding, 'title', '未命名发现'))}")
    return lines or ["- 暂未形成结构化架构线索。"]


def render_module_boundary_items(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        root_label = text(value_of(codebase_map, "root_label", "codebase"))
        modules = top_level_modules(value_of(codebase_map, "file_tree_json", []))
        lines.append(f"- `{root_label}` 的模块边界")
        if modules:
            for module_name, count in modules[:8]:
                lines.append(f"  - `{module_name}`：约 `{count}` 个文件/目录线索")
        else:
            lines.append("  - 暂未识别出稳定的顶层模块，请结合代码库地图页继续确认。")
        lines.append(f"  - 入口边界：{join_values(value_of(codebase_map, 'entrypoint_files_json', []))}")
        lines.append(f"  - 配置边界：{join_values(value_of(codebase_map, 'config_files_json', []))}")
    for finding in as_list(package.get("findings"))[:3]:
        lines.append(f"- 架构发现：{text(value_of(finding, 'title', '未命名发现'))}")
    return lines or ["- 暂未形成模块边界线索。"]


def render_main_flow_items(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        root_label = text(value_of(codebase_map, "root_label", "codebase"))
        entrypoints = value_of(codebase_map, "entrypoint_files_json", [])
        configs = value_of(codebase_map, "config_files_json", [])
        dependencies = value_of(codebase_map, "dependency_files_json", [])
        tests = value_of(codebase_map, "test_files_json", [])
        lines.append(f"- `{root_label}` 主流程推断")
        lines.append(f"  - 入口：{join_values(entrypoints)}")
        lines.append(f"  - 初始化与配置：{join_values(configs)}")
        lines.append(f"  - 依赖装配：{join_values(dependencies)}")
        lines.append(f"  - 验证出口：{join_values(tests)}")
        lines.append("  - 主流程：外部请求/使用者输入 -> 入口模块 -> 配置和依赖装配 -> 业务模块处理 -> 测试或运行结果验证。")
    return lines or ["- 暂未形成主流程线索。"]


def render_extension_point_items(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        root_label = text(value_of(codebase_map, "root_label", "codebase"))
        lines.append(f"- `{root_label}` 可扩展点")
        lines.append(f"  - 配置扩展：{join_values(value_of(codebase_map, 'config_files_json', []))}")
        lines.append(f"  - 依赖扩展：{join_values(value_of(codebase_map, 'dependency_files_json', []))}")
        lines.append(f"  - 测试保护：{join_values(value_of(codebase_map, 'test_files_json', []))}")
    for recommendation in as_list(package.get("recommendations"))[:3]:
        lines.append(f"- 后续建议：{text(value_of(recommendation, 'summary', '未命名建议'))}")
    for risk in as_list(package.get("risks"))[:3]:
        lines.append(f"- 扩展风险：{text(value_of(risk, 'title', '未命名风险'))}")
    return lines or ["- 暂未形成扩展点线索。"]


def top_level_modules(file_tree: Any) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in as_list(file_tree):
        path = text(value_of(item, "path", item))
        if not path:
            continue
        module_name = path.split("/", maxsplit=1)[0]
        if not module_name:
            continue
        counts[module_name] = counts.get(module_name, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def render_profile_extension_items(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    lines = [
        f"- 测试线索：{join_values(collect_codebase_values(inventory, 'test_files_json'))}",
        f"- 配置线索：{join_values(collect_codebase_values(inventory, 'config_files_json'))}",
    ]
    for risk in as_list(package.get("risks"))[:3]:
        lines.append(f"- 改造风险：{text(value_of(risk, 'title', '未命名风险'))}")
    return lines


def render_profile_delivery_items(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    open_questions = [
        question
        for question in as_list(package.get("questions"))
        if text(value_of(question, "status", "")) == "open"
    ]
    return [
        f"- 启动文档线索：{join_values([value_of(item, 'readme_excerpt', '') for item in as_list(inventory.get('codebase_maps')) if value_of(item, 'readme_excerpt', '')])}",
        f"- 待确认问题：`{len(open_questions)}` 条。",
        f"- 输入源采集：`{len(as_list(inventory.get('snapshots')))}` 个快照。",
    ]


def render_onboarding_guide(project: Any, package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    findings_count = len(as_list(package.get("findings")))
    risks_count = len(as_list(package.get("risks")))
    questions_count = len(as_list(package.get("questions")))
    tech_stack = []
    entrypoints = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        tech_stack.extend(as_list(value_of(codebase_map, "tech_stack_json", [])))
        entrypoints.extend(as_list(value_of(codebase_map, "entrypoint_files_json", [])))

    return [
        "## 工程接手指南",
        "",
        f"- 调研目标：{text(value_of(project, 'analysis_goal', '当前项目尚未填写调研目标。'))}",
        f"- 技术栈线索：{join_values(unique_values(tech_stack))}",
        f"- 优先入口：{join_values(unique_values(entrypoints))}",
        f"- 当前发现：`{findings_count}` 条；风险：`{risks_count}` 条；开放问题：`{questions_count}` 条。",
        "- 接手建议：先阅读启动与运行线索，再沿后续阅读路径查看入口、配置、测试和关键证据。",
        "",
    ]


def render_runbook_clues(inventory: dict[str, Any]) -> list[str]:
    lines = ["## 启动与运行线索", ""]
    codebase_maps = as_list(inventory.get("codebase_maps"))
    if not codebase_maps:
        lines.append("- 当前调研包暂未形成代码库地图，无法提取启动与运行线索。")
    for codebase_map in codebase_maps:
        lines.append(f"- `{text(value_of(codebase_map, 'root_label', 'codebase'))}`")
        lines.append(f"  - 依赖文件：{join_values(value_of(codebase_map, 'dependency_files_json', []))}")
        lines.append(f"  - 配置文件：{join_values(value_of(codebase_map, 'config_files_json', []))}")
        lines.append(f"  - 入口文件：{join_values(value_of(codebase_map, 'entrypoint_files_json', []))}")
        readme_excerpt = text(value_of(codebase_map, "readme_excerpt", ""))
        if readme_excerpt:
            lines.append(f"  - README 线索：{truncate(readme_excerpt, 160)}")
    lines.append("")
    return lines


def render_architecture_map(inventory: dict[str, Any], package: dict[str, Any]) -> list[str]:
    lines = ["## 架构地图", ""]
    codebase_maps = as_list(inventory.get("codebase_maps"))
    findings = as_list(package.get("findings"))
    if not codebase_maps and not findings:
        lines.append("- 当前调研包暂未形成足够证据描述架构地图。")
    for codebase_map in codebase_maps:
        lines.append(f"- `{text(value_of(codebase_map, 'root_label', 'codebase'))}`")
        lines.append(f"  - 技术栈：{join_values(value_of(codebase_map, 'tech_stack_json', []))}")
        lines.append(f"  - 入口线索：{join_values(value_of(codebase_map, 'entrypoint_files_json', []))}")
        lines.append(f"  - 测试线索：{join_values(value_of(codebase_map, 'test_files_json', []))}")
    if findings:
        lines.append("- 关键结构发现：")
        for finding in findings[:5]:
            lines.append(f"  - {text(value_of(finding, 'title', '未命名发现'))}")
    lines.append("")
    return lines


def render_evidence_index(package: dict[str, Any]) -> list[str]:
    lines = ["## 证据索引", ""]
    evidence_items = as_list(package.get("evidence_items"))
    if not evidence_items:
        lines.append("- 当前调研包暂未形成证据索引。")
    for evidence in evidence_items:
        lines.append(
            f"- `{text(value_of(evidence, 'id', 'evidence'))}` "
            f"{text(value_of(evidence, 'title', '证据'))} "
            f"({text(value_of(evidence, 'evidence_type', 'unknown'))})"
        )
        summary = text(value_of(evidence, "summary", ""))
        if summary:
            lines.append(f"  - {summary}")
    lines.append("")
    return lines


def render_reading_path(package: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    lines = ["## 后续阅读路径", ""]
    reading_items = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        reading_items.extend(as_list(value_of(codebase_map, "entrypoint_files_json", [])))
        reading_items.extend(as_list(value_of(codebase_map, "config_files_json", [])))
        reading_items.extend(as_list(value_of(codebase_map, "test_files_json", []))[:3])
    if reading_items:
        for item in unique_values(reading_items)[:8]:
            lines.append(f"- `{text(item)}`")
    else:
        lines.append("- 当前调研包暂未识别明确阅读路径，请先补充代码目录或 README 输入源。")

    open_questions = [
        question
        for question in as_list(package.get("questions"))
        if text(value_of(question, "status", "")) == "open"
    ]
    if open_questions:
        lines.append("- 阅读前建议先确认的开放问题：")
        for question in open_questions[:3]:
            lines.append(f"  - {text(value_of(question, 'prompt', '未命名问题'))}")
    lines.append("")
    return lines


def render_evidence_chain(package: dict[str, Any]) -> list[str]:
    lines = ["## 关键证据链", ""]
    evidence_by_id = {
        text(value_of(evidence, "id")): evidence
        for evidence in as_list(package.get("evidence_items"))
        if text(value_of(evidence, "id"))
    }
    has_visible_chain = False

    for finding in as_list(package.get("findings")):
        linked_evidence = []
        for ref in as_list(value_of(finding, "evidence_refs_json", [])):
            ref_id = text(ref)
            evidence = evidence_by_id.get(ref_id)
            if evidence is not None:
                linked_evidence.append((ref_id, evidence))
        if not linked_evidence:
            continue

        has_visible_chain = True
        lines.append(f"- {text(value_of(finding, 'title', '未命名发现'))}")
        for ref_id, evidence in linked_evidence:
            title = text(value_of(evidence, "title", "证据"))
            summary = text(value_of(evidence, "summary", ""))
            lines.append(f"  - `{ref_id}` {title}：{summary or '未填写摘要'}")

    if not has_visible_chain:
        lines.append("- 暂无可展示证据链。")
    lines.append("")
    return lines


def render_llm_assisted_verifier(verifier_result: dict[str, Any]) -> list[str]:
    llm_assisted = verifier_result.get("llm_assisted")
    if not isinstance(llm_assisted, dict):
        llm_assisted = {"status": "skipped"}

    status = text(value_of(llm_assisted, "status", "skipped")) or "skipped"
    summary = text(value_of(llm_assisted, "summary", ""))
    issues = []
    for issue in as_list(value_of(llm_assisted, "issues", [])):
        message = text(value_of(issue, "message", ""))
        if not message:
            continue
        severity = text(value_of(issue, "severity", "warning")) or "warning"
        issues.append((severity, message))
    lines = [
        "## 智能复核意见",
        "",
        f"- 状态：`{status}`",
        f"- 摘要：{summary or '未提供。'}",
    ]

    if issues:
        lines.append("- 问题：")
        for severity, message in issues:
            lines.append(f"  - `{severity}` {message}")
    else:
        lines.append("- 问题：暂无。")
    lines.append("")
    return lines


def render_checks(verifier_result: dict[str, Any]) -> list[str]:
    lines = ["### 校验项", ""]
    for check in as_list(verifier_result.get("checks")):
        passed = "通过" if value_of(check, "passed") else "需关注"
        lines.append(
            f"- `{check_name_label(text(value_of(check, 'name', 'check')))}` "
            f"[{passed}/{severity_label(text(value_of(check, 'severity', 'info')))}]"
        )
        for message in as_list(value_of(check, "messages", [])):
            lines.append(f"  - {text(message)}")
    lines.append("")
    return lines


def render_source_inventory(inventory: dict[str, Any]) -> list[str]:
    lines = ["## 输入源与采集", ""]
    snapshots = as_list(inventory.get("snapshots"))
    codebase_maps = as_list(inventory.get("codebase_maps"))
    if not snapshots:
        lines.append("- 暂未采集输入源快照。")
    for snapshot in snapshots:
        lines.append(
            f"- {text(value_of(snapshot, 'title', '未命名输入源'))} "
            f"(`{text(value_of(snapshot, 'source_type', 'unknown'))}`, "
            f"`{status_label(text(value_of(snapshot, 'status', 'unknown')))}`)"
        )
        excerpt = value_of(snapshot, "content_excerpt")
        if excerpt:
            lines.append(f"  - 摘要：{text(truncate(excerpt, 180))}")
    if codebase_maps:
        lines.append("")
        lines.append("### 代码库地图")
        lines.append("")
    for codebase_map in codebase_maps:
        lines.append(f"- `{text(value_of(codebase_map, 'root_label', 'codebase'))}`")
        lines.append(f"  - 技术栈：{join_values(value_of(codebase_map, 'tech_stack_json', []))}")
        lines.append(f"  - 依赖文件：{join_values(value_of(codebase_map, 'dependency_files_json', []))}")
        lines.append(f"  - 入口线索：{join_values(value_of(codebase_map, 'entrypoint_files_json', []))}")
        lines.append(f"  - 测试文件：{join_values(value_of(codebase_map, 'test_files_json', []))}")
    lines.append("")
    return lines


def render_evidence(package: dict[str, Any]) -> list[str]:
    lines = ["## 证据", ""]
    evidence_items = as_list(package.get("evidence_items"))
    if not evidence_items:
        lines.append("- 暂无证据项。")
    for evidence in evidence_items:
        lines.append(f"- `{text(value_of(evidence, 'id', 'evidence'))}` {text(value_of(evidence, 'title', '证据'))}")
        lines.append(f"  - 类型：`{text(value_of(evidence, 'evidence_type', 'unknown'))}`")
        lines.append(f"  - 摘要：{text(value_of(evidence, 'summary', ''))}")
        reference = value_of(evidence, "reference")
        if reference:
            lines.append(f"  - 引用：{text(reference)}")
    lines.append("")
    return lines


def render_findings(package: dict[str, Any]) -> list[str]:
    lines = ["## 发现", ""]
    findings = as_list(package.get("findings"))
    if not findings:
        lines.append("- 暂无发现。")
    for finding in findings:
        lines.append(f"- {text(value_of(finding, 'title', '未命名发现'))}")
        lines.append(f"  - 类别：`{text(value_of(finding, 'category', 'unknown'))}`")
        lines.append(f"  - 影响：`{risk_label(text(value_of(finding, 'impact_level', 'unknown')))}`")
        lines.append(f"  - 摘要：{text(value_of(finding, 'summary', ''))}")
        lines.append(f"  - 证据引用：{join_values(value_of(finding, 'evidence_refs_json', []))}")
    lines.append("")
    return lines


def render_risks(package: dict[str, Any]) -> list[str]:
    lines = ["## 风险登记", ""]
    risks = as_list(package.get("risks"))
    if not risks:
        lines.append("- 暂无登记风险。")
    for risk in risks:
        lines.append(f"- {text(value_of(risk, 'title', '未命名风险'))}")
        lines.append(f"  - 严重程度：`{risk_label(text(value_of(risk, 'severity', 'unknown')))}`")
        lines.append(f"  - 摘要：{text(value_of(risk, 'summary', ''))}")
        lines.append(f"  - 缓解建议：{text(value_of(risk, 'mitigation', ''))}")
        lines.append(f"  - 证据引用：{join_values(value_of(risk, 'evidence_refs_json', []))}")
    lines.append("")
    return lines


def render_questions(package: dict[str, Any]) -> list[str]:
    lines = ["## 开放问题", ""]
    questions = as_list(package.get("questions"))
    if not questions:
        lines.append("- 暂无开放问题。")
    for question in questions:
        lines.append(f"- {text(value_of(question, 'prompt', '未命名问题'))}")
        lines.append(f"  - 状态：`{status_label(text(value_of(question, 'status', 'unknown')))}`")
        lines.append(f"  - 影响：`{risk_label(text(value_of(question, 'impact', 'unknown')))}`")
        lines.append(f"  - 背景：{text(value_of(question, 'reason', ''))}")
        answer = value_of(question, "answer_text")
        if answer:
            lines.append(f"  - 回答：{text(answer)}")
    lines.append("")
    return lines


def render_recommendations(package: dict[str, Any]) -> list[str]:
    lines = ["## 建议", ""]
    recommendations = as_list(package.get("recommendations"))
    if not recommendations:
        lines.append("- 暂无建议。")
    for recommendation in recommendations:
        lines.append(f"- {text(value_of(recommendation, 'summary', '建议'))}")
        lines.append(f"  - 依据：{text(value_of(recommendation, 'rationale', ''))}")
        lines.append(f"  - 可信度：`{text(value_of(recommendation, 'confidence', 'unknown'))}`")
        next_steps = as_list(value_of(recommendation, "next_steps_json", []))
        if next_steps:
            lines.append(f"  - 下一步：{join_values(next_steps)}")
    lines.append("")
    return lines


def render_timeline(package: dict[str, Any]) -> list[str]:
    lines = ["## 调研过程时间线", ""]
    turns = sorted(as_list(package.get("turns")), key=lambda turn: int(value_of(turn, "turn_index", 0) or 0))
    if not turns:
        lines.append("- 暂无 Loop 轮次记录。")
    for turn in turns:
        lines.append(f"- #{text(value_of(turn, 'turn_index', '?'))} `{text(value_of(turn, 'node_name', 'node'))}`")
        lines.append(f"  - 动作：{text(value_of(turn, 'action', ''))}")
        lines.append(f"  - 观察：{text(value_of(turn, 'observation', ''))}")
    lines.append("")
    return lines


def join_values(values: Any, *, limit: int = DEFAULT_LIST_LIMIT) -> str:
    items = [display_value(value) for value in as_list(values) if value not in (None, "")]
    items = [item for item in items if item]
    if not items:
        return "未检测到"
    visible = items[:limit]
    rendered = "，".join(visible)
    omitted_count = len(items) - len(visible)
    if omitted_count > 0:
        rendered = f"{rendered} (+{omitted_count} more)"
    return rendered


def display_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("path", "name", "title", "summary"):
            nested = text(value.get(key, ""))
            if nested:
                return nested
        return text(value)
    raw = text(value)
    return TECH_STACK_LABELS.get(raw.lower(), raw)


def unique_values(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in as_list(values):
        item = text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def collect_codebase_values(inventory: dict[str, Any], key: str) -> list[str]:
    values: list[str] = []
    for codebase_map in as_list(inventory.get("codebase_maps")):
        values.extend(text(value) for value in as_list(value_of(codebase_map, key, [])) if text(value))
    return values


def profile_info(package: dict[str, Any], verifier_result: dict[str, Any]) -> Any:
    profile = package.get("profile_summary")
    if profile:
        return profile
    return verifier_result.get("research_profile") if isinstance(verifier_result, dict) else {}


def truncate(value: Any, max_length: int) -> str:
    rendered = text(value)
    if len(rendered) <= max_length:
        return rendered
    return rendered[: max_length - 3] + "..."


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def bool_label(value: bool) -> str:
    return "是" if value else "否"


def risk_label(value: str) -> str:
    labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
        "critical": "严重",
        "unknown": "未知",
    }
    return labels.get(value, value)


def severity_label(value: str) -> str:
    labels = {
        "info": "信息",
        "warning": "警告",
        "error": "错误",
    }
    return labels.get(value, value)


def status_label(value: str) -> str:
    labels = {
        "draft": "草稿",
        "ready": "就绪",
        "running": "运行中",
        "paused_for_input": "等待输入",
        "pending": "待处理",
        "partial": "部分完成",
        "ready_for_review": "待评审",
        "ready_for_review_with_risks": "带风险待评审",
        "completed": "已完成",
        "failed": "失败",
        "blocked": "存在阻塞",
        "approved": "已通过",
        "rejected": "已驳回",
        "needs_more": "要求补充",
        "answered": "已回答",
        "open": "待回答",
        "collected": "已采集",
        "unknown": "未知",
    }
    return labels.get(value, value)


def check_name_label(value: str) -> str:
    labels = {
        "structure_completeness": "结构完整度",
        "evidence_coverage": "证据覆盖",
        "codebase_coverage": "代码覆盖",
        "risk_transparency": "风险透明度",
        "question_resolution": "问题关闭率",
        "quality_score": "质量评分",
    }
    return labels.get(value, value)
