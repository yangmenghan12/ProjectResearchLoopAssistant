export type Project = {
  id: string;
  name: string;
  topic: string;
  analysis_goal: string | null;
  status: string;
  research_profile: string;
  max_turns: number;
  created_at: string;
  updated_at: string;
};

export type Source = {
  id: string;
  project_id: string;
  source_type: string;
  uri: string;
  normalized_uri: string;
  status: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ResearchProfile = {
  key: string;
  label: string;
  description: string;
  checklist: string[];
  quality_weights: Record<string, number>;
  verifier_checks: string[];
  report_sections: string[];
  required_evidence_types: string[];
};

export type SourceBundleItem = {
  id: string;
  bundle_id: string;
  project_id: string;
  source_id: string | null;
  input_type: string;
  raw_value: string;
  normalized_uri: string | null;
  status: string;
  error_message: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type SourceBundle = {
  id: string;
  project_id: string;
  name: string;
  status: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  items: SourceBundleItem[];
};

export type SourceSnapshot = {
  id: string;
  project_id: string;
  source_id: string;
  source_type: string;
  status: string;
  title: string | null;
  content_excerpt: string | null;
  content_path: string | null;
  metadata_json: Record<string, unknown>;
  skipped_items_json: Array<Record<string, unknown>>;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type CodebaseMap = {
  id: string;
  project_id: string;
  source_id: string;
  root_label: string;
  tech_stack_json: string[];
  dependency_files_json: string[];
  config_files_json: string[];
  test_files_json: string[];
  entrypoint_files_json: string[];
  readme_excerpt: string | null;
  file_tree_json: Array<Record<string, unknown>>;
  skipped_items_json: Array<Record<string, unknown>>;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProjectInventory = {
  project_id: string;
  snapshots: SourceSnapshot[];
  codebase_maps: CodebaseMap[];
};

export type AnalysisRun = {
  id: string;
  project_id: string;
  status: string;
  current_turn: number;
  max_turns: number;
  thread_id: string | null;
  execution_mode: string;
  last_checkpoint_id: string | null;
  resume_count: number;
  stop_reason: string | null;
  started_at: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type RunJob = {
  id: string;
  project_id: string;
  run_id: string;
  job_type: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  last_error: string | null;
  payload_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type RunEvent = {
  id: string;
  project_id: string;
  run_id: string;
  event_type: string;
  sequence: number;
  payload_json: Record<string, unknown>;
  created_at: string;
};

export type RunEnqueue = {
  run: AnalysisRun;
  job: RunJob;
};

export type LoopTurn = {
  id: string;
  run_id: string;
  project_id: string;
  turn_index: number;
  node_name: string;
  action: string;
  observation: string;
  status: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
};

export type EvidenceItem = {
  id: string;
  title: string;
  summary: string;
  evidence_type: string;
  reference: string | null;
  confidence: number;
};

export type Finding = {
  id: string;
  title: string;
  summary: string;
  category: string;
  impact_level: string;
  confidence: number;
  evidence_refs_json: string[];
};

export type Risk = {
  id: string;
  title: string;
  summary: string;
  severity: string;
  mitigation: string;
  evidence_refs_json: string[];
};

export type Question = {
  id: string;
  prompt: string;
  reason: string;
  impact: string;
  status: string;
  answer_text: string | null;
};

export type Recommendation = {
  id: string;
  summary: string;
  rationale: string;
  confidence: number;
  next_steps_json: string[];
};

export type QualityScore = {
  id: string;
  overall_score: number;
  structure_completeness: number;
  evidence_coverage: number;
  codebase_coverage: number;
  risk_transparency: number;
  question_resolution: number;
  recommendation_confidence: number;
  score_delta: number;
  reasons_json: string[];
  next_actions_json: string[];
};

export type AnalysisPackage = {
  run: AnalysisRun;
  turns: LoopTurn[];
  evidence_items: EvidenceItem[];
  findings: Finding[];
  risks: Risk[];
  questions: Question[];
  recommendations: Recommendation[];
  quality_scores: QualityScore[];
};

export type ProjectResearchPackage = AnalysisPackage;

export type VerifierCheck = {
  name: string;
  passed: boolean;
  severity: string;
  messages: string[];
};

export type VerifierLlmIssue = {
  title?: string | null;
  summary?: string | null;
  message?: string | null;
  severity?: string | null;
  status?: string | null;
} & Record<string, unknown>;

export type VerifierLlmAssisted = {
  status?: string | null;
  summary?: string | null;
  issues?: Array<VerifierLlmIssue | string>;
} & Record<string, unknown>;

export type VerifierResult = {
  passed: boolean;
  risk_level: string;
  checks: VerifierCheck[];
  summary: string;
  blocking_reasons: string[];
  warning_reasons: string[];
  llm_assisted?: VerifierLlmAssisted | null;
};

export type ReportSnapshot = {
  id: string;
  project_id: string;
  run_id: string;
  status: string;
  title: string;
  markdown_content: string;
  verifier_result_json: VerifierResult;
  review_decision: string | null;
  review_comment: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ReportMarkdown = {
  markdown: string;
};

export type ReportDiff = {
  base_report_id: string;
  target_report_id: string;
  diff: Record<string, unknown>;
};

export type LlmProfile = {
  id: string;
  name: string;
  task_type: string;
  provider: string;
  base_url: string;
  model: string;
  api_key_env_name: string | null;
  api_key_required: boolean;
  max_tokens: number;
  temperature: number;
  timeout_seconds: number;
  daily_budget_cents: number | null;
  enabled: boolean;
  configured: boolean;
  created_at: string;
  updated_at: string;
};

export type LlmCall = {
  id: string;
  project_id: string | null;
  run_id: string | null;
  report_id: string | null;
  task_type: string;
  provider: string;
  model: string;
  prompt_hash: string;
  input_refs_json: string[];
  output_ref: string | null;
  usage_json: Record<string, unknown>;
  latency_ms: number | null;
  status: string;
  error_message: string | null;
  created_at: string;
};

export type ToolRegistration = {
  id: string;
  name: string;
  description: string;
  permission_level: string;
  input_schema_json: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type ToolCall = {
  id: string;
  project_id: string | null;
  run_id: string | null;
  turn_id: string | null;
  tool_name: string;
  permission_level: string;
  status: string;
  input_summary_json: Record<string, unknown>;
  output_summary_json: Record<string, unknown>;
  error_message: string | null;
  latency_ms: number | null;
  created_at: string;
  updated_at: string;
};

export type EvaluationCheck = {
  name: string;
  passed: boolean;
  message: string;
};

export type EvaluationProfileSummary = {
  key: string;
  label: string;
};

export type EvaluationQualitySummary = {
  overall_score: number;
  metadata_profile: string;
};

export type EvaluationBundleSummary = {
  name?: string;
  status: string;
  item_count: number;
  collected_count?: number;
  failed_count?: number;
  pending_count?: number;
};

export type EvaluationCaseResult = {
  case_id: string;
  description: string;
  case_type?: "scan_regression" | "profile_benchmark" | string;
  passed: boolean;
  expected_checks: string[];
  missing_checks?: string[];
  unexpected_checks?: string[];
  checks: EvaluationCheck[];
  research_profile?: EvaluationProfileSummary;
  profile_checks?: EvaluationCheck[];
  quality_summary?: EvaluationQualitySummary;
  report_sections?: string[];
  bundle_summary?: EvaluationBundleSummary;
};

export type EvaluationRun = {
  id: string;
  status: string;
  summary_json: {
    total: number;
    passed: number;
    failed: number;
    by_type?: Record<string, { total: number; passed: number; failed: number }>;
    profiles?: Record<string, { total: number; passed: number; failed: number }>;
  };
  case_results_json: EvaluationCaseResult[];
  started_at: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};
