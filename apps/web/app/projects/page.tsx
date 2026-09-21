import Link from "next/link";
import { redirect } from "next/navigation";
import { createCodeAnalysisDemoProject, listProjects } from "@/lib/api";
import { statusLabel } from "@/lib/labels";

async function createDemoAction() {
  "use server";

  const project = await createCodeAnalysisDemoProject();
  redirect(`/projects/${project.id}`);
}

function statusCounts(projects: Awaited<ReturnType<typeof listProjects>>) {
  return projects.reduce<Record<string, number>>((counts, project) => {
    counts[project.status] = (counts[project.status] ?? 0) + 1;
    return counts;
  }, {});
}

function projectStatusClass(status: string) {
  if (status === "completed" || status === "ready_for_review") {
    return "badge badge-ok";
  }
  if (status === "failed" || status === "rejected") {
    return "badge badge-danger";
  }
  return "badge badge-warning";
}

export default async function ProjectsPage() {
  const projects = await listProjects();
  const counts = statusCounts(projects);
  const completed = counts.completed ?? 0;
  const reviewable = (counts.ready_for_review ?? 0) + (counts.ready_for_review_with_risks ?? 0);
  const running = (counts.running ?? 0) + (counts.paused_for_input ?? 0);

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>工程调研工作台</h1>
          <p className="meta">Project Research Loop Assistant</p>
          <p className="muted">从输入源、采集、调研过程、证据、风险到调研报告，跟踪每个项目调研包的闭环状态。</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button button-secondary" href="/settings/llm">
            LLM 设置
          </Link>
          <Link className="button button-secondary" href="/evaluations">
            调研回归评估
          </Link>
          <form action={createDemoAction}>
            <button className="button button-secondary" type="submit">
              创建演示项目
            </button>
          </form>
          <Link className="button" href="/projects/new">
            新建调研项目
          </Link>
        </div>
      </div>

      <section className="stats">
        <div className="stat">
          项目总数
          <span className="stat__value">{projects.length}</span>
        </div>
        <div className="stat">
          待评审
          <span className="stat__value">{reviewable}</span>
        </div>
        <div className="stat">
          已完成
          <span className="stat__value">{completed}</span>
        </div>
        <div className="stat">
          运行/暂停
          <span className="stat__value">{running}</span>
        </div>
      </section>

      <section className="panel stack">
        <div>
          <h2>项目</h2>
          <p className="muted">打开任一项目进入完整工程调研工作台。</p>
        </div>
        <div className="rows">
          {projects.map((project) => (
            <Link className="row panel-link" href={`/projects/${project.id}`} key={project.id}>
              <div className="toolbar">
                <div>
                  <p className="meta">项目主题</p>
                  <h2>{project.name}</h2>
                  <p>{project.topic}</p>
                </div>
                <span className={projectStatusClass(project.status)}>{statusLabel(project.status)}</span>
              </div>
              <p className="meta">
                最大轮数 {project.max_turns} · 创建时间 {new Date(project.created_at).toLocaleString("zh-CN")}
              </p>
            </Link>
          ))}
        </div>

        {projects.length === 0 ? (
          <div className="row">
            <h2>还没有调研项目</h2>
            <p className="muted">可以新建调研项目，或直接创建演示项目查看完整闭环。</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
