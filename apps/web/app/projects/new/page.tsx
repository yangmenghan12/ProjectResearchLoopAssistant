import { redirect } from "next/navigation";
import { createProject } from "@/lib/api";
import { researchTemplates, templateGoal } from "@/lib/research-templates";

async function createProjectAction(formData: FormData) {
  "use server";

  const analysisGoal = String(formData.get("analysis_goal") ?? "").trim();
  const researchProfile = String(formData.get("research_profile") ?? "quick_onboarding");
  const project = await createProject({
    name: String(formData.get("name") ?? "").trim(),
    topic: String(formData.get("topic") ?? "").trim(),
    analysis_goal: templateGoal(researchProfile, analysisGoal),
    research_profile: researchProfile,
    max_turns: Number(formData.get("max_turns") ?? 6),
  });

  redirect(`/projects/${project.id}`);
}

export default function NewProjectPage() {
  return (
    <form action={createProjectAction} className="panel stack">
      <div>
        <h1>新建调研项目</h1>
        <p className="muted">选择调研场景，补充目标，Loop 会围绕证据逐步形成项目调研包。</p>
      </div>

      <label className="field">
        项目名称
        <input className="input" maxLength={160} name="name" required />
      </label>

      <label className="field">
        调研主题
        <textarea className="textarea" name="topic" required />
      </label>

      <fieldset className="field">
        <legend>调研档位</legend>
        <div className="inventory-grid">
          {researchTemplates.map((template, index) => (
            <label className="row panel-link" key={template.key}>
              <input
                defaultChecked={index === 0}
                name="research_profile"
                type="radio"
                value={template.key}
              />
              <span>
                <strong>{template.label}</strong>
                <span className="muted">{template.description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="field">
        调研目标补充
        <textarea className="textarea" name="analysis_goal" />
      </label>

      <label className="field">
        最大轮数
        <input className="input" defaultValue={6} max={20} min={1} name="max_turns" type="number" />
      </label>

      <button className="button" type="submit">
        创建调研项目
      </button>
    </form>
  );
}
