import type { components } from "./generated/api";

export type FieldStatus = components["schemas"]["FieldStatus"];

type TechnicalField =
  | "occurred_date"
  | "occurred_at"
  | "channel"
  | "feature_area"
  | "issue_type"
  | "symptom"
  | "submission_status"
  | "error_code";

type ConsultationField =
  | "action"
  | "symbol_name"
  | "symbol_code"
  | "quantity"
  | "order_type"
  | "price"
  | "attempted_at";

export interface TechnicalData {
  occurred_date: string | null;
  occurred_at: string | null;
  channel: "M-able" | "UNKNOWN";
  feature_area: "DOMESTIC_STOCK_ORDER" | "UNKNOWN";
  issue_type: components["schemas"]["IssueType"];
  symptom: string;
  submission_status: "SUBMITTED" | "NOT_SUBMITTED" | "UNKNOWN";
  error_code: string | null;
  field_statuses: Record<TechnicalField, FieldStatus>;
  evidence: Partial<Record<TechnicalField, string>>;
}

export interface ConsultationData {
  action: components["schemas"]["OrderAction"];
  symbol_name: string | null;
  symbol_code: string | null;
  quantity: number | null;
  order_type: components["schemas"]["OrderType"];
  price: number | null;
  attempted_at: string | null;
  field_statuses: Record<ConsultationField, FieldStatus>;
  evidence: Partial<Record<ConsultationField, string>>;
}

export interface AnalysisResponse {
  analysis_id: string;
  analysis_version: number;
  status: "confirmation";
  attachment: { id: string; url: string } | null;
  masked_text: string;
  masked_items: string[];
  technical: TechnicalData;
  consultation: ConsultationData;
}

export interface AnalysisPendingResponse {
  analysis_id: string;
  analysis_version: number;
  status: "pending";
}

export interface AnalysisFailedResponse {
  analysis_id: string;
  analysis_version: number;
  status: "failed";
  error: { code: "TIMEOUT" | "INVALID_SCHEMA" | "PROVIDER_UNAVAILABLE" };
}

export interface AnalysisCompleteResponse {
  analysis_id: string;
  analysis_version: number;
  status: "complete";
}

export type AnalysisResult =
  | AnalysisResponse
  | AnalysisPendingResponse
  | AnalysisFailedResponse
  | AnalysisCompleteResponse;

export interface SavedCard {
  reference_number: string;
  expires_at: string;
}

export type AgentSession = components["schemas"]["AgentLoginResponse"];
export type AgentCardListItem = components["schemas"]["ConsultationCardListItem"];
export type AgentCardListResponse = components["schemas"]["ConsultationCardListResponse"];
export type AgentCase = components["schemas"]["ConsultationCardDetail"];
export type AgentVerificationResult = components["schemas"]["AgentVerificationResponse"];
export type AgentSignalVerificationResult = components["schemas"]["AgentSignalVerificationResponse"];
export type SignalDashboard = components["schemas"]["SignalDashboardResponse"];
export type SignalDashboardItem = components["schemas"]["SignalDashboardItem"];
export type OperatorSignalListItem = components["schemas"]["OperatorSignalListItem"];
export type OperatorSignalListResponse = components["schemas"]["OperatorSignalListResponse"];
export type OperatorSignalMutationResult = components["schemas"]["OperatorSignalMutationResponse"];
export type OperatorSignalStatus = components["schemas"]["SignalStatus"];
export type OperatorSignalClosureReason = components["schemas"]["SignalClosureReason"];

export type AgentCardSelector =
  | { reference_number: string; card_id?: never }
  | { reference_number?: never; card_id: string };

export type AgentVerificationInput = Omit<
  components["schemas"]["AgentVerificationRequest"],
  "reference_number" | "card_id" | "client_request_id"
>;

export type AgentSignalVerificationInput = Omit<
  components["schemas"]["AgentSignalVerificationRequest"],
  "reference_number" | "card_id" | "client_request_id"
>;
