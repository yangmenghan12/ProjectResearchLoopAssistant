import type {
  AnalysisPackage,
  AnalysisRun,
  EvaluationRun,
  LlmCall,
  LlmProfile,
  Project,
  ProjectInventory,
  Question,
  ResearchProfile,
  ReportDiff,
  ReportMarkdown,
  ReportSnapshot,
  RunEnqueue,
  RunEvent,
  Source,
  SourceBundle,
  ToolCall,
  ToolRegistration,
} from "@/types/project";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
}

export function createProject(payload: {
  name: string;
  topic: string;
  analysis_goal?: string | null;
  research_profile?: string;
  max_turns: number;
}): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${projectId}`);
}

export function listSources(projectId: string): Promise<Source[]> {
  return request<Source[]>(`/projects/${projectId}/sources`);
}

export function createSource(projectId: string, payload: { source_type: string; uri: string }): Promise<Source> {
  return request<Source>(`/projects/${projectId}/sources`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listResearchProfiles(): Promise<ResearchProfile[]> {
  return request<ResearchProfile[]>("/research-profiles");
}

export function listSourceBundles(projectId: string): Promise<SourceBundle[]> {
  return request<SourceBundle[]>(`/projects/${projectId}/source-bundles`);
}

export function createSourceBundle(
  projectId: string,
  payload: { name: string; items: Array<{ input_type: string; raw_value: string }> },
): Promise<SourceBundle> {
  return request<SourceBundle>(`/projects/${projectId}/source-bundles`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function collectSourceBundle(projectId: string, bundleId: string): Promise<SourceBundle> {
  return request<SourceBundle>(`/projects/${projectId}/source-bundles/${bundleId}/collect`, {
    method: "POST",
  });
}

export function collectProjectSources(projectId: string): Promise<ProjectInventory> {
  return request<ProjectInventory>(`/projects/${projectId}/collect`, {
    method: "POST",
  });
}

export function getProjectInventory(projectId: string): Promise<ProjectInventory> {
  return request<ProjectInventory>(`/projects/${projectId}/inventory`);
}

export function startProjectRun(projectId: string): Promise<RunEnqueue> {
  return request<RunEnqueue>(`/projects/${projectId}/runs`, {
    method: "POST",
  });
}

export function listProjectRuns(projectId: string): Promise<AnalysisRun[]> {
  return request<AnalysisRun[]>(`/projects/${projectId}/runs`);
}

export function getProjectRun(projectId: string, runId: string): Promise<AnalysisPackage> {
  return request<AnalysisPackage>(`/projects/${projectId}/runs/${runId}`);
}

export function resumeProjectRun(projectId: string, runId: string): Promise<AnalysisPackage> {
  return request<AnalysisPackage>(`/projects/${projectId}/runs/${runId}/resume`, {
    method: "POST",
  });
}

export function listRunEvents(projectId: string, runId: string): Promise<RunEvent[]> {
  return request<RunEvent[]>(`/projects/${projectId}/runs/${runId}/events`);
}

export function cancelProjectRun(projectId: string, runId: string): Promise<AnalysisRun> {
  return request<AnalysisRun>(`/projects/${projectId}/runs/${runId}/cancel`, {
    method: "POST",
  });
}

export function retryProjectRun(projectId: string, runId: string): Promise<RunEnqueue> {
  return request<RunEnqueue>(`/projects/${projectId}/runs/${runId}/retry`, {
    method: "POST",
  });
}

export function answerProjectQuestion(
  projectId: string,
  questionId: string,
  payload: { answer_text: string },
): Promise<Question> {
  return request<Question>(`/projects/${projectId}/questions/${questionId}/answer`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createProjectReport(projectId: string, payload?: { run_id?: string | null }): Promise<ReportSnapshot> {
  return request<ReportSnapshot>(`/projects/${projectId}/reports`, {
    method: "POST",
    body: payload?.run_id ? JSON.stringify(payload) : undefined,
  });
}

export function createIntelligentProjectReport(
  projectId: string,
  payload?: { run_id?: string | null },
): Promise<ReportSnapshot> {
  return request<ReportSnapshot>(`/projects/${projectId}/reports/intelligent`, {
    method: "POST",
    body: payload?.run_id ? JSON.stringify(payload) : undefined,
  });
}

export function getLatestProjectReport(projectId: string): Promise<ReportSnapshot> {
  return request<ReportSnapshot>(`/projects/${projectId}/reports/latest`);
}

export function getProjectReportMarkdown(projectId: string, reportId: string): Promise<ReportMarkdown> {
  return request<ReportMarkdown>(`/projects/${projectId}/reports/${reportId}/markdown`);
}

export function getProjectReportDiff(projectId: string, reportId: string, baseReportId: string): Promise<ReportDiff> {
  const search = new URLSearchParams({ base_report_id: baseReportId });
  return request<ReportDiff>(
    `/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(reportId)}/diff?${search.toString()}`,
  );
}

export function reportPdfUrl(projectId: string, reportId: string): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(reportId)}/export.pdf`;
}

export function reportMarkdownUrl(projectId: string, reportId: string): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(reportId)}/export.md`;
}

export function reviewProjectReport(
  projectId: string,
  reportId: string,
  payload: { decision: "approved" | "rejected" | "needs_more"; comment?: string | null },
): Promise<ReportSnapshot> {
  return request<ReportSnapshot>(`/projects/${projectId}/reports/${reportId}/review`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createCodeAnalysisDemoProject(): Promise<Project> {
  return request<Project>("/demo/projects/code-analysis", {
    method: "POST",
  });
}

export function listLlmProfiles(): Promise<LlmProfile[]> {
  return request<LlmProfile[]>("/llm/profiles");
}

export function createLlmProfile(payload: {
  name: string;
  task_type: string;
  provider: string;
  base_url: string;
  model: string;
  api_key_env_name?: string | null;
  api_key_required: boolean;
  max_tokens: number;
  temperature: number;
  timeout_seconds: number;
  daily_budget_cents?: number | null;
  enabled: boolean;
}): Promise<LlmProfile> {
  return request<LlmProfile>("/llm/profiles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listLlmCalls(params: { projectId?: string; runId?: string }): Promise<LlmCall[]> {
  const search = new URLSearchParams();
  if (params.projectId) {
    search.set("project_id", params.projectId);
  }
  if (params.runId) {
    search.set("run_id", params.runId);
  }
  const query = search.toString();
  return request<LlmCall[]>(query ? `/llm/calls?${query}` : "/llm/calls");
}

export function listTools(): Promise<ToolRegistration[]> {
  return request<ToolRegistration[]>("/tools");
}

export function listRunToolCalls(projectId: string, runId: string): Promise<ToolCall[]> {
  return request<ToolCall[]>(`/projects/${projectId}/runs/${runId}/tool-calls`);
}

export function runEvaluations(): Promise<EvaluationRun> {
  return request<EvaluationRun>("/evaluations/run", {
    method: "POST",
  });
}

export function getLatestEvaluation(): Promise<EvaluationRun> {
  return request<EvaluationRun>("/evaluations/latest");
}
