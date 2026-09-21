import { redirect } from "next/navigation";
import { WorkbenchNav } from "@/components/workbench-nav";
import { answerProjectQuestion, getProject, getProjectRun, listProjectRuns } from "@/lib/api";
import { listText, riskLabel, statusLabel } from "@/lib/labels";
import type { Question } from "@/types/project";

async function answerQuestionAction(projectId: string, questionId: string, formData: FormData) {
  "use server";

  await answerProjectQuestion(projectId, questionId, {
    answer_text: String(formData.get("answer_text") ?? "").trim(),
  });
  redirect(`/projects/${projectId}/risks`);
}

function QuestionRow({ projectId, question }: { projectId: string; question: Question }) {
  const answerAction = answerQuestionAction.bind(null, projectId, question.id);

  return (
    <div className="row">
      <div>
        <p className="meta">
          {riskLabel(question.impact)}影响 · {statusLabel(question.status)}
        </p>
        <h2>{question.prompt}</h2>
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

export default async function ProjectRisksPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, runs] = await Promise.all([getProject(id), listProjectRuns(id)]);
  const latestRun = runs[0] ?? null;
  const packageData = latestRun ? await getProjectRun(id, latestRun.id) : null;

  return (
    <div className="stack">
      <div>
        <h1>风险与问题</h1>
        <p className="muted">{project.name}</p>
      </div>
      <WorkbenchNav projectId={id} active="risks" />

      {!packageData ? (
        <section className="panel stack">
          <h2>还没有风险登记</h2>
          <p className="muted">运行 Loop 后，这里会展示风险、缓解建议和需要人工回答的问题。</p>
        </section>
      ) : (
        <>
          <section className="panel stack">
            <h2>风险登记</h2>
            {packageData.risks.length ? (
              <div className="rows">
                {packageData.risks.map((risk) => (
                  <div className="row" key={risk.id}>
                    <p className="meta">{riskLabel(risk.severity)}</p>
                    <h2>{risk.title}</h2>
                    <p>{risk.summary}</p>
                    <p className="muted">{risk.mitigation}</p>
                    <p className="meta">证据引用：{listText(risk.evidence_refs_json)}</p>
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
                  <QuestionRow key={question.id} projectId={id} question={question} />
                ))}
              </div>
            ) : (
              <p className="muted">没有待回答问题。</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
