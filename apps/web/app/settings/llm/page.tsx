import Link from "next/link";
import { redirect } from "next/navigation";
import { createLlmProfile, listLlmProfiles } from "@/lib/api";
import { statusLabel } from "@/lib/labels";
import type { LlmProfile } from "@/types/project";

const TASK_TYPES = new Set(["analyzer", "verifier", "report"]);

function formText(formData: FormData, name: string): string {
  return String(formData.get(name) ?? "").trim();
}

function requiredText(formData: FormData, name: string, label: string): string {
  const value = formText(formData, name);
  if (!value) {
    throw new Error(`${label}不能为空`);
  }
  return value;
}

function taskTypeField(formData: FormData): string {
  const value = formText(formData, "task_type");
  if (!TASK_TYPES.has(value)) {
    throw new Error("任务类型只能是智能分析、智能复核或报告生成");
  }
  return value;
}

function finiteNumberField(formData: FormData, name: string, label: string): number {
  const rawValue = formText(formData, name);
  if (!rawValue) {
    throw new Error(`${label}不能为空`);
  }

  const value = Number(rawValue);
  if (!Number.isFinite(value)) {
    throw new Error(`${label}必须是有效数字`);
  }
  return value;
}

function integerRangeField(formData: FormData, name: string, label: string, min: number, max: number): number {
  const value = finiteNumberField(formData, name, label);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${label}必须是 ${min} 到 ${max} 之间的整数`);
  }
  return value;
}

function numberRangeField(formData: FormData, name: string, label: string, min: number, max: number): number {
  const value = finiteNumberField(formData, name, label);
  if (value < min || value > max) {
    throw new Error(`${label}必须是 ${min} 到 ${max} 之间的数字`);
  }
  return value;
}

function optionalBudgetField(formData: FormData): number | null {
  const rawValue = formText(formData, "daily_budget_cents");
  if (!rawValue) {
    return null;
  }

  const value = Number(rawValue);
  if (!Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
    throw new Error("日预算必须是大于等于 0 的整数，或留空表示不限制");
  }
  return value;
}

async function createProfileAction(formData: FormData) {
  "use server";

  await createLlmProfile({
    name: requiredText(formData, "name", "档位名称"),
    task_type: taskTypeField(formData),
    provider: requiredText(formData, "provider", "服务商 Provider"),
    base_url: requiredText(formData, "base_url", "接口地址 Base URL"),
    model: requiredText(formData, "model", "模型 Model"),
    api_key_env_name: formText(formData, "api_key_env_name") || null,
    api_key_required: formData.get("api_key_required") === "on",
    max_tokens: integerRangeField(formData, "max_tokens", "最大 token", 64, 16000),
    temperature: numberRangeField(formData, "temperature", "温度", 0, 2),
    timeout_seconds: integerRangeField(formData, "timeout_seconds", "超时秒数", 1, 180),
    daily_budget_cents: optionalBudgetField(formData),
    enabled: formData.get("enabled") === "on",
  });

  redirect("/settings/llm");
}

function ProfileRow({ profile }: { profile: LlmProfile }) {
  return (
    <div className="row">
      <div className="toolbar">
        <div>
          <p className="meta">
            {statusLabel(profile.task_type)} · {profile.provider}
          </p>
          <h2>{profile.name}</h2>
          <p className="muted">
            {profile.model} · {profile.base_url}
          </p>
        </div>
        <div className="toolbar-actions">
          <span className={profile.configured ? "badge badge-ok" : "badge badge-warning"}>
            {profile.configured ? "已配置" : "未配置"}
          </span>
          <span className={profile.enabled ? "badge badge-ok" : "badge badge-warning"}>
            {profile.enabled ? "已启用" : "已停用"}
          </span>
          <span className="badge">
            {profile.daily_budget_cents === null ? "无每日预算" : `每日 ${profile.daily_budget_cents} 分`}
          </span>
        </div>
      </div>
      <p className="meta">
        API key：{profile.api_key_env_name ?? "未设置"} · 最大 token：{profile.max_tokens} · 温度：
        {profile.temperature} · 超时：{profile.timeout_seconds} 秒
      </p>
    </div>
  );
}

export default async function LlmSettingsPage() {
  const profiles = await listLlmProfiles();
  const configuredCount = profiles.filter((profile) => profile.configured).length;
  const enabledCount = profiles.filter((profile) => profile.enabled).length;
  const budgetedCount = profiles.filter((profile) => profile.daily_budget_cents !== null).length;

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>LLM 设置</h1>
          <p className="muted">配置智能分析、智能复核和报告生成使用的模型档位。</p>
        </div>
        <Link className="button button-secondary" href="/projects">
          返回项目
        </Link>
      </div>

      <section className="stats">
        <div className="stat">
          档位总数
          <span className="stat__value">{profiles.length}</span>
        </div>
        <div className="stat">
          已配置
          <span className="stat__value">{configuredCount}</span>
        </div>
        <div className="stat">
          已启用
          <span className="stat__value">{enabledCount}</span>
        </div>
        <div className="stat">
          有预算
          <span className="stat__value">{budgetedCount}</span>
        </div>
      </section>

      <section className="panel stack">
        <div>
          <h2>模型档位</h2>
          <p className="muted">查看各任务使用的模型配置状态。</p>
        </div>
        {profiles.length ? (
          <div className="rows">
            {profiles.map((profile) => (
              <ProfileRow key={profile.id} profile={profile} />
            ))}
          </div>
        ) : (
          <div className="row">
            <h2>暂无模型档位</h2>
            <p className="muted">新增一个档位后，Loop 会按任务类型选择可用模型。</p>
          </div>
        )}
      </section>

      <section className="panel stack">
        <h2>新增档位</h2>
        <form action={createProfileAction} className="stack">
          <div className="inventory-grid">
            <label className="field">
              档位名称
              <input className="input" maxLength={120} name="name" placeholder="例如 DeepSeek 分析档" required />
            </label>
            <label className="field">
              任务类型
              <select className="select" defaultValue="analyzer" name="task_type">
                <option value="analyzer">智能分析</option>
                <option value="verifier">智能复核</option>
                <option value="report">报告生成</option>
              </select>
            </label>
            <label className="field">
              服务商 Provider
              <input className="input" maxLength={80} name="provider" placeholder="例如 deepseek" required />
            </label>
            <label className="field">
              接口地址 Base URL
              <input className="input" name="base_url" placeholder="例如 https://api.deepseek.com" required />
            </label>
            <label className="field">
              模型 Model
              <input className="input" maxLength={160} name="model" placeholder="例如 deepseek-v4-flash" required />
            </label>
            <label className="field">
              API key 环境变量
              <input className="input" maxLength={120} name="api_key_env_name" placeholder="例如 LLM_API_KEY" />
            </label>
            <label className="field">
              最大 token
              <input className="input" defaultValue={1200} max={16000} min={64} name="max_tokens" required type="number" />
            </label>
            <label className="field">
              温度
              <input className="input" defaultValue={0.2} max={2} min={0} name="temperature" required step={0.1} type="number" />
            </label>
            <label className="field">
              超时秒数
              <input className="input" defaultValue={30} max={180} min={1} name="timeout_seconds" required type="number" />
            </label>
            <label className="field">
              日预算（分）
              <input className="input" min={0} name="daily_budget_cents" placeholder="不填表示不限制" type="number" />
            </label>
          </div>
          <div className="toolbar-actions">
            <label className="badge">
              <input defaultChecked name="api_key_required" type="checkbox" /> 需要 API key
            </label>
            <label className="badge">
              <input defaultChecked name="enabled" type="checkbox" /> 启用档位
            </label>
          </div>
          <div className="toolbar-actions">
            <button className="button" type="submit">
              保存档位
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
