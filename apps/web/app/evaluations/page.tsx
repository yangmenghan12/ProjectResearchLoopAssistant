import Link from "next/link";
import { redirect } from "next/navigation";
import { getLatestEvaluation, runEvaluations } from "@/lib/api";
import type { EvaluationRun } from "@/types/project";

async function runEvaluationsAction() {
  "use server";

  await runEvaluations();
  redirect("/evaluations");
}

async function loadLatestEvaluation(): Promise<EvaluationRun | null> {
  try {
    return await getLatestEvaluation();
  } catch {
    return null;
  }
}

function passBadge(passed: boolean) {
  return (
    <span className={passed ? "badge badge-ok" : "badge badge-danger"}>
      {passed ? "通过" : "失败"}
    </span>
  );
}

export default async function EvaluationsPage() {
  const evaluation = await loadLatestEvaluation();

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>调研回归评估</h1>
          <p className="muted">固定小型样例 + 五类调研档位基准评估，验证只读扫描、项目调研包、档位验证器、报告章节和资料包摘要是否稳定。</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button button-secondary" href="/projects">
            返回项目
          </Link>
          <form action={runEvaluationsAction}>
            <button className="button" type="submit">
              运行评估
            </button>
          </form>
        </div>
      </div>

      {evaluation ? (
        <>
          <section className="stats">
            <div className="stat">
              总用例
              <span className="stat__value">{evaluation.summary_json.total}</span>
            </div>
            <div className="stat">
              通过
              <span className="stat__value">{evaluation.summary_json.passed}</span>
            </div>
            <div className="stat">
              失败
              <span className="stat__value">{evaluation.summary_json.failed}</span>
            </div>
            <div className="stat">
              状态
              <span className="stat__value">{evaluation.status}</span>
            </div>
          </section>

          <section className="panel stack">
            <div className="toolbar">
              <div>
                <h2>最新评估结果</h2>
                <p className="muted">
                  完成时间{" "}
                  {evaluation.completed_at
                    ? new Date(evaluation.completed_at).toLocaleString("zh-CN")
                    : "尚未完成"}
                </p>
              </div>
              {passBadge(evaluation.summary_json.failed === 0)}
            </div>

            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>用例</th>
                    <th>说明</th>
                    <th>类型</th>
                    <th>调研档位</th>
                    <th>期望检查</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {evaluation.case_results_json.map((result) => (
                    <tr key={result.case_id}>
                      <td>{result.case_id}</td>
                      <td>{result.description}</td>
                      <td>{result.case_type === "profile_benchmark" ? "调研档位" : "基础扫描"}</td>
                      <td>{result.research_profile ? `${result.research_profile.label} (${result.research_profile.key})` : "通用"}</td>
                      <td>{result.expected_checks.join(", ")}</td>
                      <td>{passBadge(result.passed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel stack">
            <h2>检查明细</h2>
            <div className="rows">
              {evaluation.case_results_json.map((result) => (
                <div className="row" key={result.case_id}>
                  <div className="toolbar">
                    <div>
                      <h2>{result.case_id}</h2>
                      <p className="muted">{result.description}</p>
                    </div>
                    {passBadge(result.passed)}
                  </div>
                  {result.research_profile ? (
                    <div className="stats">
                      <div className="stat">
                        调研档位
                        <span className="stat__value">{result.research_profile.label}</span>
                      </div>
                      <div className="stat">
                        质量分
                        <span className="stat__value">{result.quality_summary?.overall_score ?? "-"}</span>
                      </div>
                      <div className="stat">
                        资料包
                        <span className="stat__value">
                          {result.bundle_summary
                            ? `${result.bundle_summary.status} / ${result.bundle_summary.item_count} 条`
                            : "-"}
                        </span>
                      </div>
                    </div>
                  ) : null}
                  {result.report_sections?.length ? (
                    <p className="muted">报告章节：{result.report_sections.join("、")}</p>
                  ) : null}
                  <ul className="compact-list">
                    {result.checks.map((check) => (
                      <li key={check.name}>
                        <strong>{check.name}</strong>：{check.message} {check.passed ? "通过" : "失败"}
                      </li>
                    ))}
                  </ul>
                  {result.profile_checks?.length ? (
                    <>
                      <h3>档位检查</h3>
                      <ul className="compact-list">
                        {result.profile_checks.map((check) => (
                          <li key={check.name}>
                            <strong>{check.name}</strong>：{check.message} {check.passed ? "通过" : "失败"}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        </>
      ) : (
        <section className="panel stack">
          <h2>还没有评估记录</h2>
          <p className="muted">运行一次评估后，这里会展示 summary、case 表格和每个检查项的明细。</p>
          <form action={runEvaluationsAction}>
            <button className="button" type="submit">
              立即运行评估
            </button>
          </form>
        </section>
      )}
    </div>
  );
}
