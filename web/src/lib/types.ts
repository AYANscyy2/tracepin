// Hand-written mirror of src/tracepin/models.py + analyze.py. Kept small on purpose;
// generated types would drift silently.

export type Severity = "high" | "medium" | "low";
export type StatusCode = "UNSET" | "OK" | "ERROR";
export type ToolOutcome = "ok" | "exception" | "invalid_arguments" | "unknown_tool";

export interface CodeLocation {
  file: string;
  function: string | null;
  line: number;
  inferred_from_tool?: boolean;
}

export interface Finding {
  detector_id: string;
  severity: Severity;
  confidence: number;
  trace_id: string;
  task_id: string;
  span_ids: string[];
  title: string;
  detail: string;
  evidence: Record<string, unknown>;
  code_location: CodeLocation | null;
  scope: "trace" | "run";
  suggested_fix?: string;
}

export interface TimelineEntry {
  span_id: string;
  tool: string | null;
  outcome: ToolOutcome | null;
  duration_ms: number;
  error: string | null;
  arguments: string;
}

export interface TraceSummary {
  trace_id: string;
  task_id: string;
  task_prompt: string;
  tags: string[];
  success: boolean;
  stop_reason: string;
  iterations: number;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  final_answer: string;
  timeline: TimelineEntry[];
  finding_ids: string[];
}

export interface RunInfo {
  run_id: string;
  model: string;
  prompt_version: string;
  git_sha: string;
  task_count: number;
  pass_count: number;
  skipped_traces: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_wall_ms: number;
}

export interface Summary {
  total: number;
  by_detector: Record<string, number>;
  by_severity: Record<string, number>;
  traces_with_findings: number;
  traces_with_high: number;
  detectors_run: string[];
}

export interface Analysis {
  schema_version: number;
  run: RunInfo;
  baselines: Record<string, unknown>;
  traces: TraceSummary[];
  findings: Finding[];
  summary: Summary;
}

export interface RunIndexEntry {
  run_id: string;
  label: string;
  model: string;
  prompt_version: string;
  git_sha: string;
  task_count: number;
  pass_count: number;
  summary: Summary;
  total_input_tokens: number;
  total_wall_ms: number;
}

export interface SpanEvent {
  name: string;
  at_ms: number | null;
  attributes: Record<string, unknown>;
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  operation: string | null;
  start_ms: number; // offset from the root span start
  end_ms: number;
  status_code: StatusCode;
  status_message: string | null;
  attributes: Record<string, unknown>;
  events: SpanEvent[];
}

export interface TraceSpans {
  trace_id: string;
  task_id: string;
  spans: Span[];
}

export interface BlameInfo {
  commit: string;
  author: string;
  authored_at: string;
  subject: string;
}

export interface CodeContext {
  file: string;
  line: number;
  function: string | null;
  snippet: string;
  blame: BlameInfo | null;
  permalink: string | null;
  source_ref: string;
}

export interface LocationRank {
  file: string;
  line: number;
  function: string | null;
  findings: number;
  tasks: string[];
  detectors: Record<string, number>;
  context: CodeContext | null;
}
