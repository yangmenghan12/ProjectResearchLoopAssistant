import { WorkbenchNav } from "@/components/workbench-nav";
import { getProject, getProjectInventory } from "@/lib/api";

function valueText(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function CompactList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) {
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

export default async function ProjectCodebasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [project, inventory] = await Promise.all([getProject(id), getProjectInventory(id)]);

  return (
    <div className="stack">
      <div>
        <h1>工程结构分析</h1>
        <p className="muted">{project.name}</p>
      </div>
      <WorkbenchNav projectId={id} active="codebase" />

      {inventory.codebase_maps.length ? (
        inventory.codebase_maps.map((codebaseMap) => (
          <section className="panel stack" key={codebaseMap.id}>
            <div>
              <h2>{codebaseMap.root_label}</h2>
              <p className="meta">
                文件 {valueText(codebaseMap.metadata_json.total_files)} · 目录{" "}
                {valueText(codebaseMap.metadata_json.total_directories)}
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
                <CompactList items={codebaseMap.test_files_json.slice(0, 16)} empty="未识别到测试文件。" />
              </div>
              <div>
                <h3>入口线索</h3>
                <CompactList items={codebaseMap.entrypoint_files_json} empty="未识别到入口文件。" />
              </div>
              <div>
                <h3>跳过项</h3>
                <CompactList
                  items={codebaseMap.skipped_items_json
                    .slice(0, 16)
                    .map((item) => `${valueText(item.path)} · ${valueText(item.reason)}`)}
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
        ))
      ) : (
        <section className="panel stack">
          <h2>还没有工程结构地图</h2>
          <p className="muted">采集本地目录或 GitHub 仓库后，这里会展示工程结构分析结果。</p>
        </section>
      )}
    </div>
  );
}
