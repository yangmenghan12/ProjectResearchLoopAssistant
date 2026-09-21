import Link from "next/link";
import { redirect } from "next/navigation";
import { WorkbenchNav } from "@/components/workbench-nav";
import { collectSourceBundle, createSource, createSourceBundle, listSourceBundles, listSources } from "@/lib/api";
import { sourceTypeLabel, statusLabel } from "@/lib/labels";

async function createSourceAction(projectId: string, formData: FormData) {
  "use server";

  await createSource(projectId, {
    source_type: String(formData.get("source_type") ?? ""),
    uri: String(formData.get("uri") ?? "").trim(),
  });

  redirect(`/projects/${projectId}/sources`);
}

async function createSourceBundleAction(projectId: string, formData: FormData) {
  "use server";

  const rawValues = String(formData.get("bundle_items") ?? "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);

  if (rawValues.length === 0) {
    redirect(`/projects/${projectId}/sources?bundle_error=empty`);
  }
  if (rawValues.length > 20) {
    redirect(`/projects/${projectId}/sources?bundle_error=too_many`);
  }

  await createSourceBundle(projectId, {
    name: String(formData.get("bundle_name") ?? "初始项目资料包").trim() || "初始项目资料包",
    items: rawValues.map((rawValue) => ({
      input_type: String(formData.get("bundle_input_type") ?? "markdown_file"),
      raw_value: rawValue,
    })),
  });

  redirect(`/projects/${projectId}/sources`);
}

async function collectSourceBundleAction(projectId: string, bundleId: string) {
  "use server";

  await collectSourceBundle(projectId, bundleId);
  redirect(`/projects/${projectId}/sources`);
}

function bundleErrorMessage(code: string | undefined) {
  if (code === "empty") {
    return "请至少填写 1 条有效 URL 或路径。";
  }
  if (code === "too_many") {
    return "单个资料包最多支持 20 条输入，请拆成多个资料包提交。";
  }
  return "";
}

export default async function ProjectSourcesPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ bundle_error?: string }>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const bundleError = bundleErrorMessage(query.bundle_error);
  const [sources, sourceBundles] = await Promise.all([listSources(id), listSourceBundles(id)]);
  const boundCreateSource = createSourceAction.bind(null, id);
  const boundCreateSourceBundle = createSourceBundleAction.bind(null, id);

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>输入源</h1>
          <p className="muted">添加网页、本地路径或公开 GitHub 仓库，作为后续分析的输入。</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button" href={`/projects/${id}/inventory`}>
            查看采集结果
          </Link>
          <Link className="button button-secondary" href={`/projects/${id}`}>
            返回概览
          </Link>
        </div>
      </div>
      <WorkbenchNav projectId={id} active="sources" />

      <section className="inventory-grid">
        <form action={boundCreateSourceBundle} className="panel stack">
          <div>
            <h2>项目资料包</h2>
            <p className="muted">一次提交 README、文档、PDF、本地目录或公开仓库，作为本轮调研的输入集合。</p>
          </div>
          {bundleError ? <p className="error-text">{bundleError}</p> : null}

          <label className="field">
            资料包名称
            <input className="input" defaultValue="初始项目资料包" maxLength={160} name="bundle_name" required />
          </label>

          <label className="field">
            条目类型
            <select className="select" defaultValue="markdown_file" name="bundle_input_type">
              <option value="markdown_file">Markdown 文件</option>
              <option value="pdf_file">PDF 文件</option>
              <option value="local_file">本地文件</option>
              <option value="local_directory">本地目录</option>
              <option value="github_repo">公开 GitHub 仓库</option>
              <option value="web_page">网页</option>
            </select>
          </label>

          <label className="field">
            URL 或路径清单
            <textarea className="textarea" name="bundle_items" required rows={6} />
          </label>

          <button className="button" type="submit">
            创建资料包
          </button>
        </form>

        <form action={boundCreateSource} className="panel stack">
          <div>
            <h2>单个输入源</h2>
            <p className="muted">保留兼容入口，适合临时补充一个来源。</p>
          </div>

          <label className="field">
            类型
            <select className="select" defaultValue="web_url" name="source_type">
              <option value="web_url">网页 URL</option>
              <option value="local_file">本地文件</option>
              <option value="local_directory">本地目录</option>
              <option value="github_repo">公开 GitHub 仓库</option>
            </select>
          </label>

          <label className="field">
            URL 或路径
            <input className="input" name="uri" required />
          </label>

          <button className="button button-secondary" type="submit">
            添加输入源
          </button>
        </form>
      </section>

      <section className="panel stack">
        <div>
          <h2>资料包列表</h2>
          <p className="muted">资料包采集后会生成输入源快照，并进入后续调研 Loop。</p>
        </div>
        {sourceBundles.length ? (
          <div className="rows">
            {sourceBundles.map((bundle) => {
              const boundCollectBundle = collectSourceBundleAction.bind(null, id, bundle.id);
              return (
                <div className="row" key={bundle.id}>
                  <div className="toolbar">
                    <div>
                      <h3>{bundle.name}</h3>
                      <p className="meta">
                        状态：{statusLabel(bundle.status)} · 条目：{bundle.items.length} · 创建时间：
                        {new Date(bundle.created_at).toLocaleString("zh-CN")}
                      </p>
                    </div>
                    <form action={boundCollectBundle}>
                      <button className="button button-secondary" type="submit">
                        采集资料包
                      </button>
                    </form>
                  </div>
                  <ul className="compact-list">
                    {bundle.items.map((item) => (
                      <li key={item.id}>
                        <span className="badge">{sourceTypeLabel(item.input_type)}</span> {item.raw_value}
                        <span className={item.status === "failed" ? "badge badge-danger" : "badge"}>
                          {statusLabel(item.status)}
                        </span>
                        {item.error_message ? <span className="error-text"> {item.error_message}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="muted">还没有资料包。</p>
        )}
      </section>

      <div className="stack">
        {sources.map((source) => (
          <div className="panel" key={source.id}>
            <h2>{sourceTypeLabel(source.source_type)}</h2>
            <p>{source.normalized_uri}</p>
            <p className="meta">
              状态：{statusLabel(source.status)} · 创建时间：{new Date(source.created_at).toLocaleString("zh-CN")}
            </p>
          </div>
        ))}

        {sources.length === 0 ? <div className="panel muted">还没有输入源。添加一个来源后，它会出现在这里。</div> : null}
      </div>
    </div>
  );
}
