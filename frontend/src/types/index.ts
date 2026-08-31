export interface SuggestedFile {
  path: string;
  priority: 'high' | 'medium' | 'low';
  reason: string;
}

export interface FileAvailability {
  base_folder: string;
  total_shortlisted: number;
  found_in_supplied_dir: { original: string; resolved: string }[];
  found_elsewhere: { original: string; resolved: string }[];
  not_found: string[];
  summary_text: string;
}

export interface FileSuggestions {
  suggested_files: SuggestedFile[];
  reasoning: string;
  problem_category: string;
  priority: Record<string, string>;
  input_tokens: number;
  output_tokens: number;
  file_availability: FileAvailability;
}

export interface ClassificationRow {
  Chunk: number;
  Lines: string;
  Classification: string;
  Reason: string;
}

export interface LogPayloadEntry {
  file: string;
  content: string;
  original_size: number;
}

export interface DataAnalysis {
  ml_classification_result: {
    output_flags: Record<string, boolean | number>;
    files: Record<string, unknown>;
  };
  classification_table: ClassificationRow[];
  total_yaml_bytes: number;
  yaml_file_sizes: Record<string, number>;
  log_payload: LogPayloadEntry[];
}

export interface RCAResult {
  rca_summary: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  payload_bytes?: number;
  shortlisted_yaml_bytes?: number;
  shortlisted_log_bytes?: number;
  shortlisted_total_bytes?: number;
  orchestrator_input_tokens?: number;
  orchestrator_output_tokens?: number;
  orchestrator_cost_usd?: number;
  file_selection_input_tokens?: number;
  file_selection_output_tokens?: number;
  file_selection_cost_usd?: number;
  orchestrator_cum_input_tokens?: number;
  orchestrator_cum_output_tokens?: number;
  file_selection_cum_input_tokens?: number;
  file_selection_cum_output_tokens?: number;
}

export interface CausalDagNode {
  id: string;
  title: string;
  date: string;
  short: string;
  category: 'primary' | 'symptom' | 'secondary';
  badge?: string;
}

export interface CausalDagEdge {
  from: string;
  to: string;
  kind: 'primary' | 'secondary';
}

export interface CausalDag {
  nodes: CausalDagNode[];
  edges: CausalDagEdge[];
}

export interface ToolHistoryEntry {
  agent: string;
  timestamp: string;
  success: boolean;
  summary: string;
}

export interface SessionResults {
  file_suggestions: FileSuggestions;
  ml_classification_result: Record<string, unknown>;
  data_analysis: DataAnalysis;
  rca_summary: string;
  rca_result: RCAResult;
  log_error_entries?: LogPayloadEntry[];
  agent_thoughts: string[];
  agent_iteration_count: number;
  agent_input_tokens?: number;
  agent_output_tokens?: number;
  agent_total_tokens: number;
  total_yaml_bytes?: number;
  total_log_bytes?: number;
  rca_priority_stage?: string;
  rca_costs_per_stage?: Record<string, RCAResult>;
  file_selection_cost_usd?: number;
  orchestrator_cost_usd?: number;
  total_cost_usd?: number;
}

export interface Session {
  session_id: string;
  api_session_id?: string;
  problem_statement: string;
  case_number?: string;
  status: 'rca_completed' | 'in_progress' | 'error' | string;
  created_at: string;
  updated_at: string;
  results: SessionResults;
  tool_history: ToolHistoryEntry[];
}

export interface AgentMemory {
  sessions: Session[];
  problem_statements: { session_id: string; problem_statement: string }[];
  metadata: {
    created_at: string;
    last_updated: string;
    total_sessions: number;
  };
}

export type WorkflowPhase =
  | 'idle'
  | 'extracting'
  | 'initializing'
  | 'file_selection'
  | 'yaml_processing'
  | 'log_processing'
  | 'data_aggregation'
  | 'rca_analysis'
  | 'completed'
  | 'error';

export interface WorkflowStep {
  id: string;
  label: string;
  phase: WorkflowPhase;
  status: 'pending' | 'active' | 'completed' | 'error';
}

export interface ConsoleMessage {
  id: number;
  text: string;
  type: 'heading' | 'info' | 'success' | 'error' | 'dim' | 'normal';
  timestamp?: string;
}
