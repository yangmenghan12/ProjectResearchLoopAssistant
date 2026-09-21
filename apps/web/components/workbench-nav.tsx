import Link from "next/link";

const NAV_ITEMS = [
  { key: "overview", label: "概览", href: "" },
  { key: "sources", label: "输入源", href: "sources" },
  { key: "inventory", label: "采集", href: "inventory" },
  { key: "run", label: "调研过程", href: "run" },
  { key: "tools", label: "工具", href: "tools" },
  { key: "findings", label: "发现", href: "findings" },
  { key: "codebase", label: "工程结构", href: "codebase" },
  { key: "risks", label: "风险", href: "risks" },
  { key: "report", label: "调研报告", href: "report" },
];

export function WorkbenchNav({ projectId, active }: { projectId: string; active: string }) {
  return (
    <nav className="workbench-nav" aria-label="项目工作台">
      {NAV_ITEMS.map((item) => {
        const href = item.href ? `/projects/${projectId}/${item.href}` : `/projects/${projectId}`;
        const className = item.key === active ? "workbench-link workbench-link-active" : "workbench-link";
        return (
          <Link className={className} href={href} key={item.key}>
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
