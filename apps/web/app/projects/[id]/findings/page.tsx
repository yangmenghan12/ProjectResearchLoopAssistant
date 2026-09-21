import { WorkbenchNav } from "@/components/workbench-nav";
import { getProject, getProjectRun, listProjectRuns } from "@/lib/api";
import { listText, riskLabel, sourceTypeLabel } from "@/lib/labels";

export default async function ProjectFindingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, runs] = await Promise.all([getProject(id), listProjectRuns(id)]);
  const latestRun = runs[0] ?? null;
  const packageData = latestRun ? await getProjectRun(id, latestRun.id) : null;

  return (
    <div className="stack">
      <div>
        <h1>发现与证据</h1>
        <p className="muted">{project.name}</p>
      </div>
      <WorkbenchNav projectId={id} active="findings" />

      {!packageData ? (
        <section className="panel stack">
          <h2>还没有分析包</h2>
          <p className="muted">先完成采集并运行 Loop，系统会在这里展示发现和证据。</p>
        </section>
      ) : (
        <>
          <section className="panel stack">
            <h2>发现</h2>
            {packageData.findings.length ? (
              <div className="rows">
                {packageData.findings.map((finding) => (
                  <div className="row" key={finding.id}>
                    <p className="meta">
                      类别 {finding.category} · {riskLabel(finding.impact_level)}影响 · 可信度 {finding.confidence}
                    </p>
                    <h2>{finding.title}</h2>
                    <p>{finding.summary}</p>
                    <p className="meta">证据引用：{listText(finding.evidence_refs_json)}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">还没有发现。</p>
            )}
          </section>

          <section className="panel stack">
            <h2>证据</h2>
            {packageData.evidence_items.length ? (
              <div className="rows">
                {packageData.evidence_items.map((evidence) => (
                  <div className="row" key={evidence.id}>
                    <p className="meta">
                      {sourceTypeLabel(evidence.evidence_type)} · 可信度 {evidence.confidence}
                    </p>
                    <h2>{evidence.title}</h2>
                    <p>{evidence.summary}</p>
                    {evidence.reference ? <p className="meta">引用：{evidence.reference}</p> : null}
                    <p className="meta">ID：{evidence.id}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">还没有证据项。</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
