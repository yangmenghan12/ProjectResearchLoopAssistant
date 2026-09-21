import Link from "next/link";
import { WorkbenchNav } from "@/components/workbench-nav";
import { getProject, listProjectRuns, listRunToolCalls, listTools } from "@/lib/api";
import type { ToolCall, ToolRegistration } from "@/types/project";

function statusClass(status: string): string {
  if (status === "succeeded") {
    return "badge badge-ok";
  }
  if (status === "failed") {
    return "badge badge-danger";
  }
  return "badge badge-warning";
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    running: "运行中",
    succeeded: "成功",
    failed: "失败",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}

function jsonPreview(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function ToolCard({ tool }: { tool: ToolRegistration }) {
  return (
    <div className="row">
      <div className="toolbar">
        <div>
          <p className="meta">{tool.permission_level} · {tool.enabled ? "已启用" : "已禁用"}</p>
          <h2>{tool.name}</h2>
        </div>
        <span className={tool.permission_level === "L0" ? "badge badge-ok" : "badge badge-danger"}>
          {tool.permission_level === "L0" ? "只读" : "需拦截"}
        </span>
      </div>
      <p>{tool.description}</p>
      <pre className="excerpt">{jsonPreview(tool.input_schema_json)}</pre>
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCall }) {
  return (
    <div className="row">
      <div className="toolbar">
        <div>
          <p className="meta">
            {call.permission_level} · {new Date(call.created_at).toLocaleString("zh-CN")}
          </p>
          <h2>{call.tool_name}</h2>
          <p className="muted">耗时 {call.latency_ms === null ? "-" : `${call.latency_ms} ms`}</p>
        </div>
        <span className={statusClass(call.status)}>{statusText(call.status)}</span>
      </div>
      {call.error_message ? <p className="error-text">{call.error_message}</p> : null}
      <div className="inventory-grid">
        <div>
          <h3>输入摘要</h3>
          <pre className="excerpt">{jsonPreview(call.input_summary_json)}</pre>
        </div>
        <div>
          <h3>输出摘要</h3>
          <pre className="excerpt">{jsonPreview(call.output_summary_json)}</pre>
        </div>
      </div>
    </div>
  );
}

export default async function ProjectToolsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, tools, runs] = await Promise.all([getProject(id), listTools(), listProjectRuns(id)]);
  const latestRun = runs[0] ?? null;
  const toolCalls = latestRun ? await listRunToolCalls(id, latestRun.id) : [];

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>工具网关</h1>
          <p className="muted">{project.name}</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button button-secondary" href={`/projects/${id}/run`}>
            运行
          </Link>
          <Link className="button button-secondary" href={`/projects/${id}`}>
            概览
          </Link>
        </div>
      </div>

      <WorkbenchNav projectId={id} active="tools" />

      <section className="panel stack">
        <div>
          <h2>L0 只读权限边界</h2>
          <p className="muted">
            当前 Tool Gateway 只放行 L0 只读工具。未知工具、禁用工具以及未来 L1/L2 工具会被拒绝，
            每一次尝试都会写入 ToolCall 审计账本。
          </p>
        </div>
      </section>

      <section className="panel stack">
        <h2>工具注册表</h2>
        {tools.length ? (
          <div className="rows">
            {tools.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        ) : (
          <p className="muted">还没有注册任何只读工具。</p>
        )}
      </section>

      <section className="panel stack">
        <div>
          <h2>最近一次运行的工具调用账本</h2>
          <p className="muted">
            {latestRun
              ? `Run ${latestRun.id} · ${toolCalls.length} 条工具调用`
              : "还没有调研记录，启动调研 Loop 后这里会显示工具调用账本。"}
          </p>
        </div>
        {latestRun && toolCalls.length ? (
          <div className="rows">
            {toolCalls.map((call) => (
              <ToolCallRow key={call.id} call={call} />
            ))}
          </div>
        ) : latestRun ? (
          <p className="muted">最近一次运行还没有工具调用记录。</p>
        ) : null}
      </section>
    </div>
  );
}
