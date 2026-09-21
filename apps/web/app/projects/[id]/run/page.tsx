import Link from "next/link";
import { redirect } from "next/navigation";
import { RunStreamPanel } from "@/components/run-stream-panel";
import { WorkbenchNav } from "@/components/workbench-nav";
import {
  answerProjectQuestion,
  cancelProjectRun,
  getProject,
  getProjectRun,
  listLlmCalls,
  listProjectRuns,
  resumeProjectRun,
  retryProjectRun,
  startProjectRun,
} from "@/lib/api";
import { riskLabel, statusLabel } from "@/lib/labels";
import type { AnalysisPackage, LlmCall, QualityScore, Question } from "@/types/project";

async function startRunAction(projectId: string) {
  "use server";

  await startProjectRun(projectId);
  redirect(`/projects/${projectId}/run`);
}

async function resumeRunAction(projectId: string, runId: string) {
  "use server";

  await resumeProjectRun(projectId, runId);
  redirect(`/projects/${projectId}/run`);
}

async function cancelRunAction(projectId: string, runId: string) {
  "use server";

  await cancelProjectRun(projectId, runId);
  redirect(`/projects/${projectId}/run`);
}

async function retryRunAction(projectId: string, runId: string) {
  "use server";

  await retryProjectRun(projectId, runId);
  redirect(`/projects/${projectId}/run`);
}

async function answerQuestionAction(projectId: string, questionId: string, formData: FormData) {
  "use server";

  await answerProjectQuestion(projectId, questionId, {
    answer_text: String(formData.get("answer_text") ?? "").trim(),
  });
  redirect(`/projects/${projectId}/run`);
}

function latestScore(packageData: AnalysisPackage | null): QualityScore | null {
  if (!packageData || packageData.quality_scores.length === 0) {
    return null;
  }
  return packageData.quality_scores[packageData.quality_scores.length - 1];
}

function totalTokensText(call: LlmCall): string {
  const totalTokens = call.usage_json.total_tokens;
  if (typeof totalTokens === "number" || typeof totalTokens === "string") {
    return String(totalTokens);
  }
  return "-";
}

function llmCallStatusClass(status: string): string {
  if (status === "succeeded") {
    return "badge badge-ok";
  }
  if (status === "failed" || status === "validation_failed") {
    return "badge badge-danger";
  }
  return "badge badge-warning";
}

function runStatusClass(status: string): string {
  if (status === "completed" || status === "ready_for_review") {
    return "badge badge-ok";
  }
  if (status === "failed" || status === "cancelled") {
    return "badge badge-danger";
  }
  return "badge badge-warning";
}

function LlmCallRow({ call }: { call: LlmCall }) {
  return (
    <div className="row">
      <div className="toolbar">
        <div>
          <p className="meta">{statusLabel(call.task_type)}</p>
          <h2>
            {call.provider} / {call.model}
          </h2>
          <p className="muted">
            耗时 {call.latency_ms === null ? "-" : `${call.latency_ms} ms`} · 总 token {totalTokensText(call)}
          </p>
        </div>
        <span className={llmCallStatusClass(call.status)}>{statusLabel(call.status)}</span>
      </div>
      {call.error_message ? <p className="error-text">{call.error_message}</p> : null}
    </div>
  );
}

function QuestionCard({ projectId, question }: { projectId: string; question: Question }) {
  const answerAction = answerQuestionAction.bind(null, projectId, question.id);

  return (
    <div className="row">
      <div>
        <h2>{question.prompt}</h2>
        <p className="meta">
          {riskLabel(question.impact)}影响 · {statusLabel(question.status)}
        </p>
        <p>{question.reason}</p>
        {question.answer_text ? <p className="excerpt">{question.answer_text}</p> : null}
      </div>
      {question.status === "open" ? (
        <form action={answerAction} className="stack">
          <textarea className="textarea" name="answer_text" placeholder="输入你的回答" required />
          <button className="button" type="submit">
            保存回答
          </button>
        </form>
      ) : null}
    </div>
  );
}

export default async function ProjectRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, runs] = await Promise.all([getProject(id), listProjectRuns(id)]);
  const latestRun = runs[0] ?? null;
  const packageData = latestRun ? await getProjectRun(id, latestRun.id) : null;
  const llmCalls = latestRun ? await listLlmCalls({ runId: latestRun.id }) : [];
  const score = latestScore(packageData);
  const startAction = startRunAction.bind(null, id);
  const resumeAction = latestRun ? resumeRunAction.bind(null, id, latestRun.id) : null;
  const cancelAction = latestRun ? cancelRunAction.bind(null, id, latestRun.id) : null;
  const retryAction = latestRun ? retryRunAction.bind(null, id, latestRun.id) : null;
  const canCancel = latestRun ? ["queued", "running", "resuming"].includes(latestRun.status) : false;
  const canRetry = latestRun ? ["failed", "cancelled"].includes(latestRun.status) : false;
  const canStart = !latestRun || !["queued", "running", "resuming"].includes(latestRun.status);

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>调研过程</h1>
          <p className="muted">{project.name}</p>
          <p className="meta">{latestRun ? `最近运行：${statusLabel(latestRun.status)}` : "还没有运行记录"}</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button button-secondary" href={`/projects/${id}/inventory`}>
            采集结果
          </Link>
          <Link className="button button-secondary" href={`/projects/${id}/report`}>
            调研报告
          </Link>
          <Link className="button button-secondary" href={`/projects/${id}`}>
            概览
          </Link>
        </div>
      </div>
      <WorkbenchNav projectId={id} active="run" />
      <RunStreamPanel projectId={id} runId={latestRun?.id ?? null} />

      <section className="panel stack">
        <div className="toolbar">
          <div>
            <h2>调研状态</h2>
            <p className="muted">
              {latestRun ? `${statusLabel(latestRun.status)} · ${latestRun.stop_reason ?? "等待下一步"}` : "还没有运行记录。"}
            </p>
          </div>
          {latestRun ? <span className={runStatusClass(latestRun.status)}>{statusLabel(latestRun.status)}</span> : null}
          <div className="toolbar-actions">
            <form action={startAction}>
              <button className="button" type="submit" disabled={!canStart}>
                {latestRun ? "启动新调研 Loop" : "启动调研 Loop"}
              </button>
            </form>
            {latestRun?.status === "paused_for_input" && resumeAction ? (
              <form action={resumeAction}>
                <button className="button button-secondary" type="submit">
                  继续运行
                </button>
              </form>
            ) : null}
            {canCancel && cancelAction ? (
              <form action={cancelAction}>
                <button className="button button-secondary" type="submit">
                  取消
                </button>
              </form>
            ) : null}
            {canRetry && retryAction ? (
              <form action={retryAction}>
                <button className="button button-secondary" type="submit">
                  重试
                </button>
              </form>
            ) : null}
          </div>
        </div>
        {latestRun ? (
          <div className="stats">
            <div className="stat">
              执行模式
              <span className="stat__value">{statusLabel(latestRun.execution_mode)}</span>
            </div>
            <div className="stat">
              恢复次数
              <span className="stat__value">{latestRun.resume_count}</span>
            </div>
            <div className="stat">
              当前轮次
              <span className="stat__value">
                {latestRun.current_turn}/{latestRun.max_turns}
              </span>
            </div>
          </div>
        ) : null}
        {latestRun ? (
          <div className="inventory-grid">
            <div>
              <h3>Graph 线程</h3>
              <p className="meta">{latestRun.thread_id ?? "尚未分配"}</p>
            </div>
            <div>
              <h3>最近 checkpoint</h3>
              <p className="meta">{latestRun.last_checkpoint_id ?? "尚未写入"}</p>
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel stack">
        <h2>智能调用</h2>
        {llmCalls.length ? (
          <div className="rows">
            {llmCalls.map((call) => (
              <LlmCallRow call={call} key={call.id} />
            ))}
          </div>
        ) : (
          <p className="muted">
            {latestRun ? "本次调研没有智能调用记录，可能未配置模型，或使用了确定性分析兜底。" : "还没有调研记录，启动调研 Loop 后这里会显示智能调用。"}
          </p>
        )}
      </section>

      {score ? (
        <section className="panel stack">
          <h2>调研质量</h2>
          <div className="stats">
            <div className="stat">
              总分
              <span className="stat__value">{score.overall_score}</span>
            </div>
            <div className="stat">
              证据覆盖
              <span className="stat__value">{score.evidence_coverage}</span>
            </div>
            <div className="stat">
              代码覆盖
              <span className="stat__value">{score.codebase_coverage}</span>
            </div>
          </div>
          <div className="inventory-grid">
            <div>
              <h3>扣分原因</h3>
              <ul className="compact-list">
                {(score.reasons_json.length ? score.reasons_json : ["暂无明显扣分原因。"]).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3>下一步建议</h3>
              <ul className="compact-list">
                {(score.next_actions_json.length ? score.next_actions_json : ["可以进入用户评审。"]).map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      ) : null}

      {packageData ? (
        <>
          <section className="panel stack">
            <h2>调研过程时间线</h2>
            <div className="rows">
              {packageData.turns.map((turn) => (
                <div className="row" key={turn.id}>
                  <p className="meta">
                    #{turn.turn_index} · {turn.node_name} · {statusLabel(turn.status)}
                  </p>
                  <h2>{turn.action}</h2>
                  <p>{turn.observation}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="panel stack">
            <h2>发现</h2>
            {packageData.findings.length ? (
              <div className="rows">
                {packageData.findings.map((finding) => (
                  <div className="row" key={finding.id}>
                    <p className="meta">
                      {finding.category} · {riskLabel(finding.impact_level)}影响 · 可信度 {finding.confidence}
                    </p>
                    <h2>{finding.title}</h2>
                    <p>{finding.summary}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">还没有发现。</p>
            )}
          </section>

          <section className="panel stack">
            <h2>风险</h2>
            {packageData.risks.length ? (
              <div className="rows">
                {packageData.risks.map((risk) => (
                  <div className="row" key={risk.id}>
                    <p className="meta">{riskLabel(risk.severity)}</p>
                    <h2>{risk.title}</h2>
                    <p>{risk.summary}</p>
                    <p className="muted">{risk.mitigation}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">没有记录风险。</p>
            )}
          </section>

          <section className="panel stack">
            <h2>问题</h2>
            {packageData.questions.length ? (
              <div className="rows">
                {packageData.questions.map((question) => (
                  <QuestionCard key={question.id} projectId={id} question={question} />
                ))}
              </div>
            ) : (
              <p className="muted">没有待回答问题。</p>
            )}
          </section>

          <section className="panel stack">
            <h2>建议</h2>
            {packageData.recommendations.map((recommendation) => (
              <div className="row" key={recommendation.id}>
                <p className="meta">可信度 {recommendation.confidence}</p>
                <h2>{recommendation.summary}</h2>
                <p>{recommendation.rationale}</p>
                <ul className="compact-list">
                  {recommendation.next_steps_json.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        </>
      ) : null}
    </div>
  );
}
