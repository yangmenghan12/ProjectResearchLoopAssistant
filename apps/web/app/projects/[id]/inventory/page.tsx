import Link from "next/link";
import { redirect } from "next/navigation";
import { WorkbenchNav } from "@/components/workbench-nav";
import { collectProjectSources, getProject, getProjectInventory } from "@/lib/api";
import { sourceTypeLabel, statusLabel } from "@/lib/labels";
import type { CodebaseMap, SourceSnapshot } from "@/types/project";

async function collectAction(projectId: string) {
  "use server";

  await collectProjectSources(projectId);
  redirect(`/projects/${projectId}/inventory`);
}

function asText(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function CompactList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="muted">{empty}</p>;
  }

  return (
    <ul className="compact-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function SnapshotRow({ snapshot }: { snapshot: SourceSnapshot }) {
  return (
    <div className="row">
      <div>
        <h2>{snapshot.title ?? sourceTypeLabel(snapshot.source_type)}</h2>
        <p className="meta">
          {sourceTypeLabel(snapshot.source_type)} · {statusLabel(snapshot.status)} ·{" "}
          {new Date(snapshot.created_at).toLocaleString("zh-CN")}
        </p>
      </div>
      {snapshot.content_excerpt ? <p className="excerpt">{snapshot.content_excerpt}</p> : null}
      {snapshot.error_message ? <p className="error-text">{snapshot.error_message}</p> : null}
      {snapshot.skipped_items_json.length > 0 ? (
        <p className="meta">跳过项：{snapshot.skipped_items_json.map((item) => asText(item.path)).join("、")}</p>
      ) : null}
    </div>
  );
}

function CodebaseSection({ codebaseMap }: { codebaseMap: CodebaseMap }) {
  return (
    <section className="panel stack">
      <div>
        <h2>{codebaseMap.root_label}</h2>
        <p className="meta">
          文件：{asText(codebaseMap.metadata_json.total_files)} · 目录：
          {asText(codebaseMap.metadata_json.total_directories)}
        </p>
      </div>

      <div className="inventory-grid">
        <div>
          <h3>技术栈</h3>
          <CompactList items={codebaseMap.tech_stack_json} empty="未识别到技术栈线索。" />
        </div>
        <div>
          <h3>依赖文件</h3>
          <CompactList items={codebaseMap.dependency_files_json} empty="未识别到依赖文件。" />
        </div>
        <div>
          <h3>配置文件</h3>
          <CompactList items={codebaseMap.config_files_json} empty="未识别到配置文件。" />
        </div>
        <div>
          <h3>测试文件</h3>
          <CompactList items={codebaseMap.test_files_json.slice(0, 12)} empty="未识别到测试文件。" />
        </div>
        <div>
          <h3>入口线索</h3>
          <CompactList items={codebaseMap.entrypoint_files_json} empty="未识别到入口文件。" />
        </div>
        <div>
          <h3>跳过项</h3>
          <CompactList
            items={codebaseMap.skipped_items_json.slice(0, 12).map((item) => `${asText(item.path)} · ${asText(item.reason)}`)}
            empty="没有跳过项。"
          />
        </div>
      </div>

      {codebaseMap.readme_excerpt ? (
        <div>
          <h3>README 摘要</h3>
          <p className="excerpt">{codebaseMap.readme_excerpt}</p>
        </div>
      ) : null}
    </section>
  );
}

export default async function ProjectInventoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, inventory] = await Promise.all([getProject(id), getProjectInventory(id)]);
  const boundCollectAction = collectAction.bind(null, id);

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h1>采集结果</h1>
          <p className="muted">{project.name}</p>
        </div>
        <div className="toolbar-actions">
          <Link className="button button-secondary" href={`/projects/${id}/sources`}>
            输入源
          </Link>
          <Link className="button button-secondary" href={`/projects/${id}`}>
            概览
          </Link>
        </div>
      </div>
      <WorkbenchNav projectId={id} active="inventory" />

      <section className="panel stack">
        <div className="toolbar">
          <div>
            <h2>采集状态</h2>
            <p className="muted">
              已采集 {inventory.snapshots.length} 个输入源，生成 {inventory.codebase_maps.length} 个代码库地图。
            </p>
          </div>
          <form action={boundCollectAction}>
            <button className="button" type="submit">
              采集输入源
            </button>
          </form>
        </div>
      </section>

      <section className="panel stack">
        <h2>输入源快照</h2>
        {inventory.snapshots.length > 0 ? (
          <div className="rows">
            {inventory.snapshots.map((snapshot) => (
              <SnapshotRow key={snapshot.id} snapshot={snapshot} />
            ))}
          </div>
        ) : (
          <p className="muted">还没有采集快照。</p>
        )}
      </section>

      {inventory.codebase_maps.map((codebaseMap) => (
        <CodebaseSection key={codebaseMap.id} codebaseMap={codebaseMap} />
      ))}
    </div>
  );
}
