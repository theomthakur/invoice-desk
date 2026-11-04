export type Lifecycle = 'queued' | 'running' | 'completed' | 'failed' | 'interrupted';
export type Outcome = 'paid' | 'rejected' | 'needs_review' | 'duplicate' | 'superseded';
export type Mode = 'mock' | 'live';

export interface SessionInfo {
  workspace_id: string;
  persistence: 'local' | 'persistent' | 'ephemeral';
  modes_enabled: string[];
  csrf_token?: string | null;
}
export interface ConfigInfo {
  schema_version: string;
  policy_version: string;
  limits: { max_file_bytes: number; max_pdf_pages: number; max_line_items: number };
  supported_extensions: string[];
  enabled_modes: string[];
  approval_threshold: string;
  approval_currency: string;
  payment_currencies: string[];
}
export interface SampleInfo { sample_id: string; name: string; format: string }
export interface DocumentInfo { document_id: string; filename: string; format: string; size_bytes: number }
export interface Finding {
  code: string; severity: 'ERROR' | 'REVIEW' | 'WARNING' | 'INFO'; message: string;
  field?: string | null; item_ref?: string | null; observed?: string | null;
  expected?: string | null; evidence?: string | null;
}
export interface InvoiceLine {
  line_no: number; description: string; sku?: string | null; quantity: number | string;
  unit_price: string; amount: string;
}
export interface Invoice {
  invoice_number: string; revision?: string | null; vendor_name: string; invoice_date?: string | null;
  due_date?: string | null; currency: string; lines: InvoiceLine[];
  subtotal: string; tax: string; shipping: string; total: string;
}
export interface Approval {
  stage: string; action: string; rationale: string; actor: string; created_at: string;
}
export interface Receipt {
  payment_id: string; transaction_id: string; amount: string; currency: string;
  vendor: string; created_at: string; simulated: true;
}
export interface RunResult {
  run_id: string; workspace_id: string; lifecycle: Lifecycle; outcome?: Outcome | null;
  terminal_reason?: string | null; filename?: string | null; document_id?: string | null;
  payable_id?: string | null; version_id?: string | null; invoice?: Invoice | null;
  draft?: Record<string, unknown> | null; findings: Finding[]; requires_scrutiny: boolean;
  approval: Approval[]; receipt?: Receipt | null; mode?: string | null;
  provider?: string | null; model?: string | null; started_at?: string | null; finished_at?: string | null;
  content_hash?: string | null;
}
export interface ReviewOptions {
  version_id: string | null; content_hash: string | null; resolvable_codes: string[];
  blocked_codes: string[]; can_approve: boolean; can_reject: boolean;
}
export interface HandoffChoice { value: string; label: string }
export interface DepartmentAssignment {
  handoff_id: string; department: string; department_label: string; reason_code: string;
  reason: string; note: string; created_at: string; active: boolean; notification_sent: false;
}
export interface DepartmentRouting {
  assignment: DepartmentAssignment | null; can_route: boolean;
  departments: HandoffChoice[]; reasons: HandoffChoice[];
}
export interface DepartmentHandoffRequest {
  version_id: string | null; content_hash: string | null; department: string;
  reason_code: string; note: string; idempotency_token: string;
}
export interface RunEvent {
  seq: number; timestamp: string; stage: string; type: string; payload: Record<string, unknown>;
}
export interface EventsPage { run_id: string; lifecycle: Lifecycle; events: RunEvent[]; next_after_seq: number }
export interface RunAccepted { run_id: string; lifecycle: Lifecycle; already_existed: boolean }
export interface HistoryItem {
  run_id: string; filename?: string | null; lifecycle: Lifecycle; outcome?: Outcome | null;
  invoice_number?: string | null; vendor?: string | null; amount?: string | null;
  currency?: string | null; started_at: string;
}
export interface HistoryPage { items: HistoryItem[]; next_cursor?: string | null }
export interface BatchFile {
  file: string; kind: string; outcome?: Outcome | null; run_id?: string | null;
  related_file?: string | null; reason: string; amount?: string | null; currency?: string | null;
}
export interface BatchResult {
  batch_id: string; lifecycle: Lifecycle; files: BatchFile[]; counts: Record<string, number>;
  paid_by_currency: Record<string, string>;
}

export interface InvoiceLookupVersion { version_id:string; revision:string|null; status:string; identity_flag:string|null; run_id:string|null; run_outcome:Outcome|null }
export interface InvoiceLookupResult { matches: {payable_id:string; number:string; vendor:string; paid:boolean; payment_version_id:string|null; versions:InvoiceLookupVersion[]; versions_truncated:boolean}[]; truncated:boolean }
