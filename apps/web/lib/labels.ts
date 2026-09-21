export function statusLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    draft: "草稿",
    ready: "就绪",
    queued: "排队中",
    running: "运行中",
    resuming: "恢复中",
    paused_for_input: "等待输入",
    ready_for_review: "待评审",
    ready_for_review_with_risks: "带风险待评审",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    succeeded: "已成功",
    blocked: "存在阻塞",
    approved: "已通过",
    rejected: "已驳回",
    needs_more: "要求补充",
    answered: "已回答",
    open: "待回答",
    collected: "已采集",
    partial: "部分完成",
    pending: "待处理",
    background: "后台任务",
    sync: "同步运行",
    start: "启动",
    retry: "重试",
    analyzer: "智能分析",
    verifier: "智能复核",
    report: "报告生成",
    skipped: "已跳过",
    budget_exceeded: "预算已用尽",
    validation_failed: "校验失败",
  };
  return labels[value ?? ""] ?? value ?? "未知";
}

export function riskLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    low: "低",
    medium: "中",
    high: "高",
    critical: "严重",
    unknown: "未知",
  };
  return labels[value ?? ""] ?? value ?? "未知";
}

export function severityLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    info: "信息",
    warning: "警告",
    error: "错误",
  };
  return labels[value ?? ""] ?? value ?? "未知";
}

export function checkNameLabel(value: string): string {
  const labels: Record<string, string> = {
    structure_completeness: "结构完整度",
    evidence_coverage: "证据覆盖",
    codebase_coverage: "代码覆盖",
    risk_transparency: "风险透明度",
    question_resolution: "问题关闭率",
    quality_score: "质量评分",
  };
  return labels[value] ?? value;
}

export function sourceTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    web_url: "网页 URL",
    web_page: "网页",
    local_file: "本地文件",
    local_directory: "本地目录",
    github_repo: "公开 GitHub 仓库",
    markdown_file: "Markdown 文件",
    pdf_file: "PDF 文件",
    source_snapshot: "输入源快照",
    codebase_map: "代码库地图",
  };
  return labels[value] ?? value;
}

export function yesNo(value: boolean): string {
  return value ? "是" : "否";
}

export function listText(items: string[], empty = "无"): string {
  return items.length ? items.join("，") : empty;
}
