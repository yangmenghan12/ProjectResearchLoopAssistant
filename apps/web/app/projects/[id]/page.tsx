import Link from "next/link";
import { QualityPanel } from "@/components/quality-panel";
import { WorkbenchNav } from "@/components/workbench-nav";
import {
  getLatestProjectReport,
  getProject,
  getProjectInventory,
  getProjectRun,
  listProjectRuns,
  listSources,
} from "@/lib/api";
import { statusLabel } from "@/lib/labels";
import { researchTemplates } from "@/lib/research-templates";
import type { AnalysisPackage, Project, ProjectInventory, ReportSnapshot } from "@/types/project";

async function getLatestReportOrNull(projectId: string): Promise<ReportSnapshot | null> {
  try {
    return await getLatestProjectReport(projectId);
  } catch {
    return null;
  }
}

function latestScore(packageData: AnalysisPackage | null) {
  if (!packageData || packageData.quality_scores.length === 0) {
    return null;
  }
  return packageData.quality_scores[packageData.quality_scores.length - 1];
}

function stepBadge(done: boolean) {
  return done ? "badge badge-ok" : "badge badge-warning";
}

function researchProfileLabel(profileKey: string) {
  return researchTemplates.find((template) => template.key === profileKey)?.label ?? profileKey;
}

function nextAction(
  project: Project,
  sourceCount: number,
  inventory: ProjectInventory,
  packageData: AnalysisPackage | null,
  report: ReportSnapshot | null,
) {
  if (sourceCount === 0) {
    return { label: "添加输入源", href: `/projects/${project.id}/sources` };
  }
  if (inventory.snapshots.length === 0) {
    return { label: "采集输入源", href: `/projects/${project.id}/inventory` };
  }
  if (!packageData) {
    return { label: "启动调研 Loop", href: `/projects/${project.id}/run` };
  }
  if (!report) {
    return { label: "生成调研报告", href: `/projects/${project.id}/report` };
  }
  if (project.status !== "completed") {
    return { label: "评审调研报告", href: `/projects/${project.id}/report` };
  }
  return { label: "查看调研报告", href: `/projects/${project.id}/report` };
}

export default async function ProjectOverviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, sources, inventory, runs, report] = await Promise.all([
    getProject(id),
    listSources(id),
    getProjectInventory(id),
    listProjectRuns(id),
    getLatestReportOrNull(id),
  ]);
  const latestRun = runs[0] ?? null;
  const packageData = latestRun ? await getProjectRun(id, latestRun.id) : null;
  const score = latestScore(packageData);
  const action = nextAction(project, sources.length, inventory, packageData, report);

  const steps = [
    { label: "输入源", done: sources.length > 0, detail: `${sources.length} 个输入源` },
    { label: "采集快照", done: inventory.snapshots.length > 0, detail: `${inventory.snapshots.length} 个快照` },
    { label: "调研过程", done: Boolean(packageData), detail: latestRun ? statusLabel(latestRun.status) : "未启动" },
    { label: "调研报告", done: Boolean(report), detail: report ? statusLabel(report.status) : "未生成" },
    { label: "人工评审", done: project.status === "completed", detail: statusLabel(project.status) },
  ];

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>{project.name}</h1>
          <p className="meta">
            项目状态：{statusLabel(project.status)} · 调研档位：{researchProfileLabel(project.research_profile)}
          </p>
          <p>{project.topic}</p>
          {project.analysis_goal ? <p className="muted">{project.analysis_goal}</p> : null}
        </div>
        <div className="toolbar-actions">
          <Link className="button" href={action.href}>
            {action.label}
          </Link>
          <Link className="button button-secondary" href="/projects">
            返回项目
          </Link>
        </div>
      </div>

      <WorkbenchNav projectId={id} active="overview" />

      <section className="panel stack">
        <div>
          <h2>调研工作流</h2>
          <p className="muted">每一步都对应可追踪状态：输入、采集、调研运行、调研报告、评审。</p>
        </div>
        <div className="metric-grid">
          {steps.map((step) => (
            <div className="metric" key={step.label}>
              <span>{step.label}</span>
              <strong>{step.done ? "已完成" : "待处理"}</strong>
              <span className={stepBadge(step.done)}>{step.detail}</span>
            </div>
          ))}
        </div>
      </section>

      <QualityPanel score={score} />

      <section className="panel stack">
        <h2>调研工作台入口</h2>
        <div className="inventory-grid">
          <Link className="row panel-link" href={`/projects/${id}/findings`}>
            <p className="meta">证据链</p>
            <h2>发现与证据</h2>
            <p className="muted">查看每条结论和对应证据。</p>
          </Link>
          <Link className="row panel-link" href={`/projects/${id}/codebase`}>
            <p className="meta">工程结构</p>
            <h2>工程结构分析</h2>
            <p className="muted">查看技术栈、入口、配置、测试和跳过项。</p>
          </Link>
          <Link className="row panel-link" href={`/projects/${id}/risks`}>
            <p className="meta">风险台账</p>
            <h2>风险与问题</h2>
            <p className="muted">查看风险、缓解建议和待回答问题。</p>
          </Link>
          <Link className="row panel-link" href={`/projects/${id}/report`}>
            <p className="meta">人工评审</p>
            <h2>调研报告评审</h2>
            <p className="muted">生成、评审并导出 Markdown 调研报告。</p>
          </Link>
        </div>
      </section>
    </div>
  );
}
