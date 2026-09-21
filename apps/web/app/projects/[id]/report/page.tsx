import Link from "next/link";
import { redirect } from "next/navigation";
import { WorkbenchNav } from "@/components/workbench-nav";
import {
  createIntelligentProjectReport,
  createProjectReport,
  getLatestProjectReport,
  getProject,
  getProjectReportDiff,
  getProjectReportMarkdown,
  reportMarkdownUrl,
  reportPdfUrl,
  reviewProjectReport,
} from "@/lib/api";
import { checkNameLabel, riskLabel, severityLabel, statusLabel } from "@/lib/labels";
import type { ReportDiff, ReportSnapshot, VerifierCheck, VerifierLlmAssisted } from "@/types/project";

async function generateReportAction(projectId: string) {
  "use server";

  await createProjectReport(projectId);
  redirect(`/projects/${projectId}/report`);
}

async function generateIntelligentReportAction(projectId: string) {
  "use server";

  await createIntelligentProjectReport(projectId);
  redirect(`/projects/${projectId}/report`);
}

async function reviewReportAction(projectId: string, reportId: string, formData: FormData) {
  "use server";

  const decision = String(formData.get("decision") ?? "");
  if (!["approved", "rejected", "needs_more"].includes(decision)) {
    throw new Error("Unsupported review decision");
  }
  const comment = String(formData.get("comment") ?? "").trim();
  await reviewProjectReport(projectId, reportId, {
    decision: decision as "approved" | "rejected" | "needs_more",
    comment: comment || null,
  });
  redirect(`/projects/${projectId}/report`);
}

async function getLatestReportOrNull(projectId: string): Promise<ReportSnapshot | null> {
  try {
    return await getLatestProjectReport(projectId);
  } catch {
    return null;
  }
}

function checkClassName(check: VerifierCheck): string {
  if (check.passed) {
    return "badge badge-ok";
  }
  if (check.severity === "warning") {
    return "badge badge-warning";
  }
  return "badge badge-danger";
}

function reportStatusClass(status: string): string {
  if (status === "approved" || status === "completed" || status === "ready_for_review") {
    return "badge badge-ok";
  }
  if (status === "rejected" || status === "failed") {
    return "badge badge-danger";
  }
  return "badge badge-warning";
}

type DiffCollectionName = "findings" | "risks" | "questions" | "recommendations";

type DiffCounts = {
  added: number;
  removed: number;
  changed: number;
};

type SearchParamValue = string | string[] | undefined;

type LlmIssueView = {
  title: string;
  description: string | null;
  meta: string | null;
};

const DIFF_COLLECTION_LABELS: Record<DiffCollectionName, string> = {
  findings: "关键发现",
  risks: "风险",
  questions: "待澄清问题",
  recommendations: "建议",
};

const DIFF_COLLECTIONS: DiffCollectionName[] = ["findings", "risks", "questions", "recommendations"];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function signedNumber(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

function asDisplayText(value: unknown): string | null {
  if (typeof value === "string") {
    const text = value.trim();
    return text || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  return null;
}

function firstSearchParam(value: SearchParamValue): string {
  const first = Array.isArray(value) ? value[0] : value;
  return typeof first === "string" ? first.trim() : "";
}

function diffCounts(diff: Record<string, unknown>, collectionName: DiffCollectionName): DiffCounts {
  const collection = asRecord(diff[collectionName]);
  return {
    added: asArray(collection?.added).length,
    removed: asArray(collection?.removed).length,
    changed: asArray(collection?.changed).length,
  };
}

function diffHasChanges(counts: DiffCounts): boolean {
  return counts.added > 0 || counts.removed > 0 || counts.changed > 0;
}

function normalizeLlmIssue(issue: unknown, index: number): LlmIssueView {
  const directText = asDisplayText(issue);
  if (directText) {
    return { title: directText, description: null, meta: null };
  }

  const record = asRecord(issue);
  if (!record) {
    return { title: `意见 ${index + 1}`, description: null, meta: null };
  }

  const title =
    asDisplayText(record.title) ?? asDisplayText(record.message) ?? asDisplayText(record.summary) ?? `意见 ${index + 1}`;
  const summary = asDisplayText(record.summary);
  const message = asDisplayText(record.message);
  const description = summary && summary !== title && summary !== message ? summary : null;
  const status = asDisplayText(record.status);
  const severity = asDisplayText(record.severity);
  const metaParts = [status ? statusLabel(status) : null, severity ? severityLabel(severity) : null].filter(Boolean);

  return {
    title,
    description,
    meta: metaParts.length ? metaParts.join(" · ") : null,
  };
}

function normalizeLlmIssues(value: unknown): LlmIssueView[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((issue, index) => normalizeLlmIssue(issue, index));
}

function llmAssistedSummary(assisted: VerifierLlmAssisted): string | null {
  return asDisplayText(assisted.summary);
}

function llmAssistedStatus(assisted: VerifierLlmAssisted): string | null {
  return asDisplayText(assisted.status);
}

function llmIssueKey(issue: LlmIssueView, index: number): string {
  return `${issue.title}-${index}`;
}

function normalizedDiff(reportDiff: ReportDiff): Record<string, unknown> {
  return asRecord(reportDiff.diff) ?? {};
}

function qualityText(value: number | null): string {
  if (value === null) {
    return "缺失";
  }
  return String(value);
}

async function getReportDiffOrMessage(
  projectId: string,
  reportId: string,
  baseReportId: string,
): Promise<{ reportDiff: ReportDiff | null; error: string | null }> {
  try {
    return { reportDiff: await getProjectReportDiff(projectId, reportId, baseReportId), error: null };
  } catch {
    return {
      reportDiff: null,
      error: "未能读取对比报告，请确认报告 ID 属于当前项目。",
    };
  }
}

function CheckRows({ checks }: { checks: VerifierCheck[] }) {
  return (
    <div className="rows">
      {checks.map((check) => (
        <div className="row" key={check.name}>
          <div className="toolbar">
            <div>
              <h2>{checkNameLabel(check.name)}</h2>
              <p className="meta">{severityLabel(check.severity)}</p>
            </div>
            <span className={checkClassName(check)}>{check.passed ? "通过" : "需关注"}</span>
          </div>
          <ul className="compact-list">
            {check.messages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function LlmAssistedSection({ assisted }: { assisted: VerifierLlmAssisted }) {
  const issues = normalizeLlmIssues(assisted.issues);
  const status = llmAssistedStatus(assisted);
  const summary = llmAssistedSummary(assisted);

  return (
    <div className="row">
      <div>
        <h2>智能复核意见</h2>
        <p className="muted">
          状态：{status ? statusLabel(status) : "未标注"}
          {summary ? ` · ${summary}` : ""}
        </p>
      </div>
      {issues.length ? (
        <ul className="compact-list">
          {issues.map((issue, index) => (
            <li key={llmIssueKey(issue, index)}>
              <strong>{issue.title}</strong>
              {issue.meta ? <span className="meta"> · {issue.meta}</span> : null}
              {issue.description ? <p className="muted">{issue.description}</p> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">暂无额外问题。</p>
      )}
    </div>
  );
}

function ReportDiffSummary({ reportDiff }: { reportDiff: ReportDiff }) {
  const diff = normalizedDiff(reportDiff);
  const qualityBefore = asNumber(diff.quality_before);
  const qualityAfter = asNumber(diff.quality_after);
  const qualityDelta = asNumber(diff.quality_delta);

  return (
    <div className="rows">
      <div className="row">
        <div className="toolbar">
          <div>
            <h2>质量变化</h2>
            <p className="meta">
              基准报告 {reportDiff.base_report_id} · 当前报告 {reportDiff.target_report_id}
            </p>
          </div>
          <span className={qualityDelta !== null && qualityDelta < 0 ? "badge badge-warning" : "badge badge-ok"}>
            {qualityDelta !== null ? signedNumber(qualityDelta) : "无变化数据"}
          </span>
        </div>
        <p className="muted">
          质量评分：{qualityText(qualityBefore)} → {qualityText(qualityAfter)}
        </p>
      </div>
      {DIFF_COLLECTIONS.map((collectionName) => {
        const counts = diffCounts(diff, collectionName);
        return (
          <div className="row" key={collectionName}>
            <div className="toolbar">
              <div>
                <h2>{DIFF_COLLECTION_LABELS[collectionName]}</h2>
                <p className="meta">
                  新增 {counts.added} · 移除 {counts.removed} · 变更 {counts.changed}
                </p>
              </div>
              <span className={diffHasChanges(counts) ? "badge badge-warning" : "badge badge-ok"}>
                {diffHasChanges(counts) ? "有变化" : "无变化"}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

type ProjectReportPageProps = {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ base_report_id?: SearchParamValue }>;
};

export default async function ProjectReportPage({ params, searchParams }: ProjectReportPageProps) {
  const { id } = await params;
  const resolvedSearchParams = searchParams ? await searchParams : {};
  const baseReportId = firstSearchParam(resolvedSearchParams.base_report_id);
  const [project, report] = await Promise.all([getProject(id), getLatestReportOrNull(id)]);
  const [markdown, diffState]: [string, { reportDiff: ReportDiff | null; error: string | null }] = report
    ? await Promise.all([
        getProjectReportMarkdown(id, report.id).then((result) => result.markdown),
        baseReportId
          ? getReportDiffOrMessage(id, report.id, baseReportId)
          : Promise.resolve({ reportDiff: null, error: null }),
      ])
    : ["", { reportDiff: null, error: null }];
  const generateAction = generateReportAction.bind(null, id);
  const generateIntelligentAction = generateIntelligentReportAction.bind(null, id);
  const reviewAction = report ? reviewReportAction.bind(null, id, report.id) : null;
  const verifier = report?.verifier_result_json ?? null;

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>调研报告</h1>
          <p className="muted">{project.name}</p>
          <p className="meta">{report ? `最新报告：${statusLabel(report.status)}` : "还没有报告快照"}</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button button-secondary" href={`/projects/${id}/run`}>
            调研过程
          </Link>
          <Link className="button button-secondary" href={`/projects/${id}`}>
            概览
          </Link>
        </div>
      </div>
      <WorkbenchNav projectId={id} active="report" />

      <section className="panel stack">
        <div className="toolbar">
          <div>
            <h2>调研报告状态</h2>
            <p className="muted">
              {report ? `${statusLabel(report.status)} · ${report.verifier_result_json.summary}` : "还没有报告快照。"}
            </p>
          </div>
          {report ? <span className={reportStatusClass(report.status)}>{statusLabel(report.status)}</span> : null}
          <div className="toolbar-actions">
            <form action={generateAction}>
              <button className="button" type="submit">
                {report ? "重新生成调研报告" : "生成调研报告"}
              </button>
            </form>
            <form action={generateIntelligentAction}>
              <button className="button button-secondary" type="submit">
                生成智能调研报告
              </button>
            </form>
          </div>
        </div>

        {report ? (
          <div className="stats">
            <div className="stat">
              校验
              <span className="stat__value">{verifier?.passed ? "通过" : "有风险"}</span>
            </div>
            <div className="stat">
              风险
              <span className="stat__value">{riskLabel(verifier?.risk_level)}</span>
            </div>
            <div className="stat">
              调研报告
              <span className="stat__value">{statusLabel(report.status)}</span>
            </div>
          </div>
        ) : null}
      </section>

      {report && verifier ? (
        <>
          <section className="panel stack">
            <div>
              <h2>校验项</h2>
              <p className="muted">{verifier.summary}</p>
            </div>
            {verifier.blocking_reasons.length ? (
              <div>
                <h2>阻塞原因</h2>
                <ul className="compact-list">
                  {verifier.blocking_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {verifier.warning_reasons.length ? (
              <div>
                <h2>风险提示</h2>
                <ul className="compact-list">
                  {verifier.warning_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {verifier.llm_assisted ? <LlmAssistedSection assisted={verifier.llm_assisted} /> : null}
            <CheckRows checks={verifier.checks} />
          </section>

          <section className="panel stack">
            <div>
              <h2>人工评审</h2>
              <p className="muted">
                当前决策：{statusLabel(report.review_decision ?? "未决策")}
                {report.review_comment ? ` · ${report.review_comment}` : ""}
              </p>
            </div>
            {reviewAction ? (
              <form action={reviewAction} className="stack">
                <textarea className="textarea" name="comment" placeholder="评审备注；驳回或要求补充时必填" />
                <div className="toolbar-actions">
                  <button className="button" name="decision" type="submit" value="approved">
                    通过
                  </button>
                  <button className="button button-secondary" name="decision" type="submit" value="needs_more">
                    要求补充
                  </button>
                  <button className="button button-danger" name="decision" type="submit" value="rejected">
                    驳回
                  </button>
                </div>
              </form>
            ) : null}
            {report.status === "needs_more" || report.status === "rejected" ? (
              <div className="toolbar-actions">
                <Link className="button button-secondary" href={`/projects/${id}/run`}>
                  查看新一轮调研 Loop
                </Link>
              </div>
            ) : null}
          </section>

          <section className="panel stack">
            <div className="toolbar">
              <div>
                <h2>Markdown 调研报告导出</h2>
                <p className="muted">{report.title}</p>
              </div>
              <div className="toolbar-actions">
                <Link className="button button-secondary" href={reportMarkdownUrl(id, report.id)} rel="noreferrer" target="_blank">
                  导出 MD
                </Link>
                <Link className="button button-secondary" href={reportPdfUrl(id, report.id)} rel="noreferrer" target="_blank">
                  导出 PDF
                </Link>
              </div>
            </div>
            <pre className="excerpt markdown-preview">{markdown}</pre>
          </section>

          <section className="panel stack">
            <div>
              <h2>调研包版本对比</h2>
              <p className="muted">输入同项目内的基准报告 ID，对比当前调研包的质量与内容变化。</p>
            </div>
            <form className="toolbar" method="get">
              <input
                aria-label="基准报告 ID"
                className="input"
                defaultValue={baseReportId}
                name="base_report_id"
                placeholder="输入要对比的报告 ID"
              />
              <div className="toolbar-actions">
                <button className="button button-secondary" type="submit">
                  查询对比
                </button>
              </div>
            </form>
            {diffState.error ? <p className="error-text">{diffState.error}</p> : null}
            {diffState.reportDiff ? <ReportDiffSummary reportDiff={diffState.reportDiff} /> : null}
          </section>
        </>
      ) : null}
    </div>
  );
}
