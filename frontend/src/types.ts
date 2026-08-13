export type FieldStatus =
  | "CONFIRMED_FROM_TEXT"
  | "NEEDS_CONFIRMATION"
  | "UNKNOWN"
  | "OUT_OF_SCOPE";

export interface TechnicalData {
  occurred_at: string | null;
  channel: "M-able" | "UNKNOWN";
  feature_area: "DOMESTIC_STOCK_ORDER" | "UNKNOWN";
  issue_type:
    | "ORDER_SUBMISSION_FAILURE"
    | "ORDER_RESULT_UNCONFIRMED"
    | "ORDER_OTHER"
    | "UNKNOWN";
  symptom: string;
  submission_status: "SUBMITTED" | "NOT_SUBMITTED" | "UNKNOWN";
  error_code: string | null;
  field_statuses: Record<string, FieldStatus>;
  evidence: Record<string, string>;
}

export interface ConsultationData {
  action: "SELL" | "BUY" | "UNKNOWN";
  symbol_name: string | null;
  symbol_code: string | null;
  quantity: number | null;
  order_type: "LIMIT" | "MARKET" | "UNKNOWN";
  price: number | null;
  attempted_at: string | null;
  field_statuses: Record<string, FieldStatus>;
  evidence: Record<string, string>;
}

export interface AnalysisResponse {
  masked_text: string;
  masked_items: string[];
  technical: TechnicalData;
  consultation: ConsultationData;
}

export interface SavedCard {
  reference_number: string;
  expires_at: string;
}

export type SignalStatus =
  | "CANDIDATE"
  | "SIGNAL_DETECTED"
  | "REVIEW_REQUIRED"
  | "OFFICIAL_NOTICE_LINKED"
  | "RESOLVED";

export interface SignalItem {
  id: string;
  title: string;
  status: SignalStatus;
  report_count: number;
  raw_report_count: number;
  change: string;
  first_seen: string;
  last_seen: string;
  channel: string;
  feature_area: string;
  symptom: string;
  representative_report: string;
  action: string;
  official_notice_url: string | null;
}

export interface DashboardSnapshot {
  updated_at: string;
  baseline_ratio: number;
  volume: Array<{ time: string; count: number }>;
  signals: SignalItem[];
  policy: {
    title: string;
    version: string;
    checked_at: string;
    source_url: string | null;
  };
}

export interface AgentCase {
  reference_number: string;
  expires_at: string;
  technical: TechnicalData;
  consultation: ConsultationData;
  related_signal: SignalItem | null;
  similarity: number | null;
}

export interface AgentVerificationInput {
  agent_id: string;
  symbol_name: string | null;
  quantity: number | null;
  price: number | null;
  order_type: ConsultationData["order_type"];
  submission_status: TechnicalData["submission_status"];
}

export interface VerificationIssue {
  field: string;
  level: "IMPORTANT" | "NEEDS_CONFIRMATION";
  label: string;
  customer_value: string;
  agent_value: string;
}

export interface AgentVerificationResult {
  saved_at: string;
  issues: VerificationIssue[];
}
