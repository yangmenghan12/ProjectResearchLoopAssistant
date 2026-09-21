import type { QualityScore } from "@/types/project";

type Metric = {
  label: string;
  value: number;
};

export function QualityPanel({ score }: { score: QualityScore | null }) {
  if (!score) {
    return (
      <section className="panel stack">
        <h2>分析质量</h2>
        <p className="muted">还没有质量评分。完成一次调研 Loop 后，这里会展示项目调研包的质量结果。</p>
      </section>
    );
  }

  const metrics: Metric[] = [
    { label: "结构完整度", value: score.structure_completeness },
    { label: "证据覆盖", value: score.evidence_coverage },
    { label: "代码覆盖", value: score.codebase_coverage },
    { label: "风险透明度", value: score.risk_transparency },
    { label: "问题关闭率", value: score.question_resolution },
    { label: "建议可信度", value: score.recommendation_confidence },
  ];

  return (
    <section className="panel stack">
      <div className="toolbar">
        <div>
          <h2>分析质量</h2>
          <p className="muted">每轮 Loop 都用同一套 rubric 检查是否足够完整、可追踪、可评审。</p>
        </div>
        <div className="quality-total">{score.overall_score}</div>
      </div>

      <div className="metric-grid">
        {metrics.map((metric) => (
          <div className="metric" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
          </div>
        ))}
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
  );
}
