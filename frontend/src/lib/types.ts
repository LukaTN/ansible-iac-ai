export type UserRole = 'admin' | 'user';

export interface AuthUser {
  id: number;
  email: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  provider: string;
  has_password: boolean;
  can_change_password?: boolean;
  must_change_password?: boolean;
  created_at: string | null;
  last_login_at: string | null;
}

export type AuthMode = 'local' | 'hybrid' | 'oidc';

export interface AuthConfig {
  auth_mode: AuthMode;
  oidc_enabled: boolean;
  local_login_enabled: boolean;
  registration_enabled: boolean;
  app_admin_ui?: boolean;
  oidc_login_url: string | null;
}

export interface AuthState {
  authenticated: boolean;
  user: AuthUser | null;
  /** Set when signup succeeded but an admin must activate the account. */
  pending_approval?: boolean;
  message?: string;
}

export interface AuthProfile {
  user: AuthUser;
  usage: {
    token_budget_limit: number;
    token_budget_used: number;
    token_budget_remaining: number;
    unlimited: boolean;
  };
  activity: {
    thread_count: number;
    last_activity_at: string | null;
  };
}

export interface Thread {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  messages?: ChatMessage[];
}

export interface ToolTraceEntry {
  tool: string;
  [key: string]: unknown;
}

export interface AnsibleLintResult {
  status?: string;
  violations?: string[];
  backend?: string;
  message?: string;
}

export interface ValidationResult {
  is_valid?: boolean;
  passed?: number;
  passed_msgs?: string[];
  warnings?: string[];
  errors?: string[];
  ansible_lint?: AnsibleLintResult;
  module?: string;
}

export interface ModuleParam {
  name: string;
  type?: string;
}

export interface ModuleRefSource {
  module: string;
  found?: boolean;
  category?: string;
  total_params?: number;
  retrieval_rank?: number;
  retrieval_top_score?: number;
  is_playbook_module?: boolean;
  is_rag_primary?: boolean;
  description?: string;
  doc_url?: string;
  required_params?: ModuleParam[];
  optional_params?: ModuleParam[];
}

export interface ModuleRef {
  module?: string;
  found?: boolean;
  sources?: ModuleRefSource[];
}

export interface RagMeta {
  primary_module?: string;
  primary_collection?: string;
  primary_score?: number;
  chunks?: number;
  source_url?: string;
  intent?: string;
  awaiting_user?: boolean;
}

export interface ChatMessage {
  id: number | string;
  thread_id?: number;
  role: 'user' | 'assistant';
  content: string;
  playbook?: string | null;
  filename?: string | null;
  module?: string | null;
  validation?: ValidationResult | null;
  module_ref?: ModuleRef | null;
  rag_meta?: RagMeta | null;
  tool_trace?: ToolTraceEntry[] | null;
  ts: string;
}

/**
 * Reply to POST /api/chat, which is a 202: the turn has been accepted and
 * queued, not answered. There is deliberately no assistant_message here —
 * generating one takes minutes. The answer arrives via the
 * `generation_complete` socket event, or via the status poll below when
 * the socket is down.
 */
export interface ChatAcceptedResponse {
  job_id: string;
  thread: Thread;
  user_message: ChatMessage;
}

export interface ChatJobStatus {
  thread_id: number;
  running: boolean;
  cancelling: boolean;
}

export interface StatsPayload {
  total: number;
  valid: number;
  invalid: number;
  warns: number;
  modules: { module: string; count: number }[];
}

export interface RagStatus {
  available: boolean;
  chunks: number;
}

export interface DocsModuleHealth {
  slug: string;
  param_count: number;
  example_count: number;
  required_count: number;
  health_score: number;
}

export interface DocsStatus {
  kb_metadata?: {
    generated_at?: string;
    total_modules?: number;
  };
  module_health?: DocsModuleHealth[];
}

export interface RollbackVersion {
  filename: string;
  modified_at: string;
  size: number;
}

export interface ScrapeSession {
  id: number;
  triggered_at: string;
  status: string;
  summary?: {
    changed?: { slug: string; remote_hash?: string; local_hash?: string }[];
    diffs?: {
      module_slug?: string;
      slug?: string;
      diff_summary?: string;
      health_score?: number;
    }[];
  };
}

export type PanelTab = 'stats' | 'docs';
