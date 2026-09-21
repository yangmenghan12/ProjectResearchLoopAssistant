def evidence_id(seed: str) -> str:
    return f"evidence:{seed}"


def analyze_context(context: dict) -> dict:
    """基于上下文生成 Analysis Package 产物。"""

    evidence: list[dict] = []
    findings: list[dict] = []
    risks: list[dict] = []
    questions: list[dict] = []
    recommendations: list[dict] = []
    project = context["project"]
    research_profile = context.get("research_profile") or {}
    profile_checklist = research_profile.get("checklist") or []
    profile_label = research_profile.get("label") or "research profile"
    review_next_steps: list[str] = []

    for snapshot in context["snapshots"]:
        evidence.append(
            {
                "client_ref": evidence_id(f"snapshot:{snapshot['id']}"),
                "source_id": snapshot["source_id"],
                "title": f"已采集输入源：{snapshot.get('title') or snapshot['source_type']}",
                "summary": snapshot.get("content_excerpt") or f"已采集 {snapshot['source_type']} 类型输入源。",
                "evidence_type": "source_snapshot",
                "reference": snapshot.get("title") or snapshot["source_type"],
                "confidence": 85,
                "metadata": {
                    "source_type": snapshot["source_type"],
                    "skipped_items": snapshot.get("skipped_items", []),
                },
            }
        )

    for feedback in context.get("review_feedback", []):
        feedback_summary = feedback.get("answer_text") or feedback.get("prompt") or "用户要求补充分析。"
        feedback_metadata = feedback.get("metadata", {})
        evidence_ref = evidence_id(f"review-feedback:{feedback['id']}")
        evidence.append(
            {
                "client_ref": evidence_ref,
                "source_id": None,
                "title": "人工评审反馈",
                "summary": feedback_summary,
                "evidence_type": "review_feedback",
                "reference": f"report:{feedback_metadata.get('report_id', 'unknown')}",
                "confidence": 95,
                "metadata": {
                    "decision": feedback_metadata.get("decision"),
                    "report_id": feedback_metadata.get("report_id"),
                    "reviewed_run_id": feedback.get("run_id"),
                    "impact": feedback.get("impact"),
                },
            }
        )
        findings.append(
            {
                "title": "人工评审反馈已纳入本轮分析",
                "summary": f"本轮 Loop 已把评审意见作为高可信输入：{feedback_summary}",
                "category": "review_feedback",
                "impact_level": feedback.get("impact") or "medium",
                "confidence": 90,
                "evidence_refs": [evidence_ref],
                "metadata": {
                    "decision": feedback_metadata.get("decision"),
                    "report_id": feedback_metadata.get("report_id"),
                },
            }
        )
        review_next_steps.append(f"优先回应人工评审反馈：{feedback_summary}")

    for codebase_map in context["codebase_maps"]:
        stack = codebase_map.get("tech_stack", [])
        evidence_ref = evidence_id(f"codebase:{codebase_map['id']}")
        evidence.append(
            {
                "client_ref": evidence_ref,
                "source_id": codebase_map["source_id"],
                "title": f"代码库地图：{codebase_map['root_label']}",
                "summary": f"识别到技术栈：{', '.join(stack) or '未知'}。",
                "evidence_type": "codebase_map",
                "reference": codebase_map["root_label"],
                "confidence": 90,
                "metadata": {
                    "dependency_files": codebase_map.get("dependency_files", []),
                    "entrypoint_files": codebase_map.get("entrypoint_files", []),
                    "test_files": codebase_map.get("test_files", []),
                },
            }
        )
        if stack:
            findings.append(
                {
                    "title": "已识别技术栈",
                    "summary": f"扫描到的代码库看起来使用了 {', '.join(stack)}。",
                    "category": "codebase_map",
                    "impact_level": "medium",
                    "confidence": 85,
                    "evidence_refs": [evidence_ref],
                    "metadata": {"tech_stack": stack},
                }
            )
        if codebase_map.get("entrypoint_files"):
            findings.append(
                {
                    "title": "已识别入口线索",
                    "summary": f"可能的入口文件：{', '.join(codebase_map['entrypoint_files'][:5])}。",
                    "category": "architecture",
                    "impact_level": "medium",
                    "confidence": 80,
                    "evidence_refs": [evidence_ref],
                    "metadata": {"entrypoint_files": codebase_map["entrypoint_files"]},
                }
            )
        if not codebase_map.get("test_files"):
            risks.append(
                {
                    "title": "未识别到测试文件",
                    "summary": "扫描器没有找到测试文件，因此行为判断的可信度有限。",
                    "severity": "medium",
                    "mitigation": "在依赖分析结论前，补充测试或提供人工验证证据。",
                    "evidence_refs": [evidence_ref],
                    "metadata": {"root_label": codebase_map["root_label"]},
                }
            )
        skipped_sensitive = [
            item
            for item in codebase_map.get("skipped_items", [])
            if item.get("reason") == "sensitive_path"
        ]
        if skipped_sensitive:
            risks.append(
                {
                    "title": "敏感文件已按策略跳过",
                    "summary": "系统检测到敏感文件，并按照策略跳过采集。",
                    "severity": "low",
                    "mitigation": "继续避免把敏感文件写入分析包；如确有需要，只提供脱敏后的事实。",
                    "evidence_refs": [evidence_ref],
                    "metadata": {"skipped_items": skipped_sensitive},
                }
            )

    if not project.get("analysis_goal"):
        questions.append(
            {
                "prompt": "这次分析要支撑哪个具体决策？",
                "reason": "需要明确分析目标，系统才能给出更可靠的建议。",
                "impact": "high",
                "status": "open",
                "metadata": {"missing_field": "analysis_goal"},
            }
        )

    risk_count = len(risks)
    # Profile checklist 只影响后续复核路径，不伪造成已经采集到的证据。
    profile_next_steps = [
        f"对照{profile_label}清单复核：{checklist_item}"
        for checklist_item in profile_checklist
    ]
    recommendations.append(
        {
            "summary": "基于已记录的证据与风险继续评审。",
            "rationale": (
                f"本次 Loop 从采集输入中形成了 {len(evidence)} 条证据、{len(findings)} 条发现，"
                f"并记录了 {risk_count} 个风险。"
            ),
            "confidence": 75 if risk_count else 85,
            "next_steps": [
                *review_next_steps,
                *profile_next_steps,
                "对照项目目标复核发现。",
                "在最终评审前回答开放问题。",
                "补充测试或真实使用证据以提高可信度。",
            ],
            "metadata": {
                "risk_count": risk_count,
                "research_profile": research_profile.get("key"),
                "profile_checklist": profile_checklist,
            },
        }
    )

    return {
        "evidence": evidence,
        "findings": findings,
        "risks": risks,
        "questions": questions,
        "recommendations": recommendations,
    }
