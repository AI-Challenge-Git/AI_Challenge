import { maskSensitiveText } from "./privacy";
import type { components, operations } from "./generated/api";
import type {
  AgentCardListResponse,
  AgentCardSelector,
  AgentCase,
  AgentSession,
  AgentSignalVerificationInput,
  AgentSignalVerificationResult,
  AgentVerificationInput,
  AgentVerificationResult,
  AnalysisFailedResponse,
  AnalysisResponse,
  AnalysisResult,
  ConsultationData,
  FieldStatus,
  SavedCard,
  SignalDashboard,
  TechnicalData,
} from "./types";

type ApiAnalyzeJsonRequest = operations["analyze_api_reports_analyze_post"]["requestBody"]["content"]["application/json"];
type ApiAnalysisResponse = components["schemas"]["ReportAnalysisResponse"];
type ApiConfirmationRequest = components["schemas"]["ReportConfirmationRequest"];
type ApiConfirmedResponse = components["schemas"]["ReportConfirmedResponse"];
type ApiDiscardRequest = components["schemas"]["DiscardReportRequest"];
type ApiDeleteCardRequest = components["schemas"]["DeleteConsultationCardRequest"];
type ApiAgentLoginRequest = components["schemas"]["AgentLoginRequest"];
type ApiAgentLoginResponse = components["schemas"]["AgentLoginResponse"];
type ApiAgentLookupRequest = components["schemas"]["ConsultationCardLookupRequest"];
type ApiAgentVerificationRequest = components["schemas"]["AgentVerificationRequest"];
type ApiAgentSignalVerificationRequest = components["schemas"]["AgentSignalVerificationRequest"];

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "");
const SESSION_TOKEN_KEY = "mts-sos-session-token";
let memorySessionToken = "";

function toApiSubmissionStatus(status: TechnicalData["submission_status"]): components["schemas"]["SubmissionStatus"] {
  if (status === "SUBMITTED") return "CUSTOMER_REPORTED_SUBMITTED";
  if (status === "NOT_SUBMITTED") return "CUSTOMER_REPORTED_NOT_SUBMITTED";
  return "UNKNOWN";
}

function newSessionToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function sessionToken(): string {
  if (typeof sessionStorage === "undefined") return memorySessionToken ||= newSessionToken();
  const stored = sessionStorage.getItem(SESSION_TOKEN_KEY);
  if (stored) return stored;
  const token = newSessionToken();
  sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  return token;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code?: string,
    readonly status?: number,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let body: { code?: unknown; detail?: unknown } = {};
  try {
    body = (await response.json()) as { code?: unknown; detail?: unknown };
  } catch {
    // 상태 코드만으로 안전한 사용자 메시지를 만든다.
  }
  const code = typeof body.code === "string" ? body.code : undefined;
  if (code === "STALE_ANALYSIS") return new ApiError("더 최신 분석 결과가 있습니다. 다시 분석해 주세요.", code, response.status);
  if (code === "ANALYSIS_NOT_READY") return new ApiError("분석이 아직 완료되지 않았습니다. 다시 분석해 주세요.", code, response.status);
  if (code === "SYMBOL_MASTER_UNAVAILABLE") return new ApiError("종목 정보를 확인하는 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.", code, response.status);
  if (code === "UNSUPPORTED_SYMBOL") return new ApiError("지원하지 않는 종목코드입니다. 종목코드를 다시 확인해 주세요.", code, response.status);
  if (code === "SYMBOL_MISMATCH") return new ApiError("종목명과 종목코드가 일치하지 않습니다. 다시 확인해 주세요.", code, response.status);
  if (code === "SIGNAL_RELEVANCE_CONFLICT") return new ApiError("기존 관련성 확정 결과와 충돌합니다. 자동으로 변경하지 않고 수동 검토가 필요합니다.", code, response.status);
  if (code === "SIGNAL_RELEVANCE_UNAVAILABLE") return new ApiError("관련성 확인 결과를 현재 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.", code, response.status);
  if (response.status === 401) return new ApiError("로그인이 만료되었거나 인증 정보가 올바르지 않습니다. 다시 로그인해 주세요.", code, 401);
  if (response.status === 403) return new ApiError("상담원 권한이 필요합니다. 다시 로그인해 주세요.", code, 403);
  if (response.status === 404) return new ApiError("요청한 상담 정보를 찾을 수 없습니다.", code, 404);
  if (response.status === 409) return new ApiError("같은 요청 ID로 다른 내용을 전송할 수 없습니다. 새로 시도해 주세요.", code, 409);
  if (response.status === 422) {
    const message = code === "SCREENSHOT_REDACTION_REQUIRED"
      ? "이미지의 개인정보를 가렸는지 확인한 뒤 다시 시도해 주세요."
      : "입력값을 다시 확인해 주세요.";
    return new ApiError(message, code, 422);
  }
  if (response.status === 429) {
    const header = response.headers.get("Retry-After");
    const retryAfterSeconds = header && /^\d+$/.test(header) ? Number(header) : undefined;
    const message = retryAfterSeconds === undefined
      ? "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
      : `요청이 너무 많습니다. ${retryAfterSeconds}초 후 다시 시도해 주세요.`;
    return new ApiError(message, code, 429, retryAfterSeconds);
  }
  if (typeof body.detail === "string") return new ApiError(body.detail, code, response.status);
  return new ApiError("요청을 처리하지 못했습니다.", code, response.status);
}

async function request<T>(path: string, init: RequestInit = {}, token: string | null = sessionToken()): Promise<T> {
  if (!apiBaseUrl) throw new ApiError("API 주소가 설정되지 않았습니다. VITE_API_BASE_URL을 설정해 주세요.");
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: "omit",
    headers: {
      Accept: "application/json, application/problem+json",
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

type CandidateField<T> = {
  value: T | null;
  status: FieldStatus;
  evidence_quote: string | null;
};

const fieldStatuses: FieldStatus[] = ["CONFIRMED_FROM_TEXT", "NEEDS_CONFIRMATION", "UNKNOWN", "OUT_OF_SCOPE"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object");
}

function candidate<T>(section: Record<string, unknown>, name: string): CandidateField<T> {
  const raw = section[name];
  if (!isRecord(raw) || !fieldStatuses.includes(raw.status as FieldStatus)) {
    throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
  }
  return {
    value: (raw.value ?? null) as T | null,
    status: raw.status as FieldStatus,
    evidence_quote: typeof raw.evidence_quote === "string" ? raw.evidence_quote : null,
  };
}

function splitDateTime(value: string | null): { date: string | null; time: string | null } {
  if (!value) return { date: null, time: null };
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) throw new Error("AI 분석 응답의 발생 시각 형식이 올바르지 않습니다.");
  const date = [parsed.getFullYear(), parsed.getMonth() + 1, parsed.getDate()]
    .map((part, index) => index ? String(part).padStart(2, "0") : String(part))
    .join("-");
  const time = `${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`;
  return { date, time };
}

function adaptAnalysisResult(value: unknown): AnalysisResult {
  if (!isRecord(value)
    || typeof value.analysis_id !== "string"
    || typeof value.analysis_version !== "number"
    || !["pending", "confirmation", "failed", "complete"].includes(String(value.status))) {
    throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
  }
  const base = { analysis_id: value.analysis_id, analysis_version: value.analysis_version };
  if (value.status === "pending" || value.status === "complete") return { ...base, status: value.status };
  if (value.status === "failed") {
    const error = isRecord(value.error) ? value.error : null;
    if (!error || !["TIMEOUT", "INVALID_SCHEMA", "PROVIDER_UNAVAILABLE"].includes(String(error.code))) {
      throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
    }
    return { ...base, status: "failed", error: { code: error.code as "TIMEOUT" | "INVALID_SCHEMA" | "PROVIDER_UNAVAILABLE" } };
  }
  if (!isRecord(value.technical)
    || !isRecord(value.consultation)
    || typeof value.masked_text !== "string"
    || !Array.isArray(value.masked_items)) {
    throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
  }

  const issueType = candidate<TechnicalData["issue_type"]>(value.technical, "issue_type");
  const symptom = candidate<string>(value.technical, "symptom");
  const submissionStatus = candidate<string>(value.technical, "submission_status");
  const errorCode = candidate<string>(value.technical, "error_code");
  const occurred = candidate<string>(value.technical, "reported_occurred_at");
  const occurredParts = splitDateTime(occurred.value);
  const action = candidate<ConsultationData["action"]>(value.consultation, "action");
  const symbolName = candidate<string>(value.consultation, "symbol_name");
  const symbolCode = candidate<string>(value.consultation, "symbol_code");
  const quantity = candidate<number>(value.consultation, "quantity");
  const orderType = candidate<ConsultationData["order_type"]>(value.consultation, "order_type");
  const price = candidate<number>(value.consultation, "price_krw");
  const attemptedAt = candidate<string>(value.consultation, "attempted_at");

  const technical: TechnicalData = {
    occurred_date: occurredParts.date,
    occurred_at: occurredParts.time,
    channel: "UNKNOWN",
    feature_area: "UNKNOWN",
    issue_type: issueType.value ?? "UNKNOWN",
    symptom: symptom.value ?? "",
    submission_status: submissionStatus.value === "CUSTOMER_REPORTED_SUBMITTED"
      ? "SUBMITTED"
      : submissionStatus.value === "CUSTOMER_REPORTED_NOT_SUBMITTED"
        ? "NOT_SUBMITTED"
        : "UNKNOWN",
    error_code: errorCode.value,
    field_statuses: {
      occurred_date: occurred.status,
      occurred_at: occurred.status,
      channel: "UNKNOWN",
      feature_area: "UNKNOWN",
      issue_type: issueType.status,
      symptom: symptom.status,
      submission_status: submissionStatus.status,
      error_code: errorCode.status,
    },
    evidence: {
      ...(occurred.evidence_quote ? { occurred_date: occurred.evidence_quote, occurred_at: occurred.evidence_quote } : {}),
      ...(issueType.evidence_quote ? { issue_type: issueType.evidence_quote } : {}),
      ...(symptom.evidence_quote ? { symptom: symptom.evidence_quote } : {}),
      ...(submissionStatus.evidence_quote ? { submission_status: submissionStatus.evidence_quote } : {}),
      ...(errorCode.evidence_quote ? { error_code: errorCode.evidence_quote } : {}),
    },
  };
  const consultation: ConsultationData = {
    action: action.value ?? "UNKNOWN",
    symbol_name: symbolName.value,
    symbol_code: symbolCode.value === "UNKNOWN" ? null : symbolCode.value,
    quantity: quantity.value,
    order_type: orderType.value ?? "UNKNOWN",
    price: price.value,
    attempted_at: attemptedAt.value,
    field_statuses: {
      action: action.status,
      symbol_name: symbolName.status,
      symbol_code: symbolCode.status,
      quantity: quantity.status,
      order_type: orderType.status,
      price: price.status,
      attempted_at: attemptedAt.status,
    },
    evidence: {
      ...(action.evidence_quote ? { action: action.evidence_quote } : {}),
      ...(symbolName.evidence_quote ? { symbol_name: symbolName.evidence_quote } : {}),
      ...(symbolCode.evidence_quote ? { symbol_code: symbolCode.evidence_quote } : {}),
      ...(quantity.evidence_quote ? { quantity: quantity.evidence_quote } : {}),
      ...(orderType.evidence_quote ? { order_type: orderType.evidence_quote } : {}),
      ...(price.evidence_quote ? { price: price.evidence_quote } : {}),
      ...(attemptedAt.evidence_quote ? { attempted_at: attemptedAt.evidence_quote } : {}),
    },
  };
  let attachment: AnalysisResponse["attachment"] = null;
  if (value.attachment !== null) {
    if (!isRecord(value.attachment)
      || typeof value.attachment.id !== "string"
      || typeof value.attachment.url !== "string") {
      throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
    }
    attachment = { id: value.attachment.id, url: value.attachment.url };
  }
  return {
    ...base,
    status: "confirmation",
    attachment,
    masked_text: value.masked_text,
    masked_items: value.masked_items.map(String),
    technical,
    consultation,
  };
}

export function normalizeReportText(rawText: string): string {
  const text = rawText.trim().normalize("NFC");
  const length = [...text].length;
  if (length < 20 || length > 500) throw new Error("오류 상황을 20자 이상 500자 이하로 입력해 주세요.");
  return text;
}

export function validateScreenshot(file: File): void {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("PNG, JPG, WebP 이미지만 첨부할 수 있습니다.");
  if (file.size > 5 * 1024 * 1024) throw new Error("이미지는 5MB 이하만 첨부할 수 있습니다.");
}

export function analysisFailureMessage(code: AnalysisFailedResponse["error"]["code"]): string {
  if (code === "TIMEOUT") return "분석 시간이 초과되었습니다. 다시 시도해 주세요.";
  if (code === "INVALID_SCHEMA") return "분석 결과를 처리하지 못했습니다. 다시 시도해 주세요.";
  return "현재 AI 분석 서비스를 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.";
}

export async function analyzeReport(
  rawText: string,
  clientRequestId: string = crypto.randomUUID(),
  screenshot?: File,
  screenshotRedactedConfirmed = false,
): Promise<AnalysisResult> {
  const text = normalizeReportText(rawText);
  const requestBody = JSON.stringify({ text, client_request_id: clientRequestId });
  if (new TextEncoder().encode(requestBody).byteLength > 16 * 1024) throw new Error("입력 용량은 16KiB 이하여야 합니다.");
  if (screenshot) {
    validateScreenshot(screenshot);
    if (!screenshotRedactedConfirmed) throw new Error("이미지의 민감정보를 직접 가렸는지 확인해 주세요.");
  }
  const clientMasked = maskSensitiveText(text);
  const analyzePayload: ApiAnalyzeJsonRequest = { text: clientMasked.text, client_request_id: clientRequestId };
  const body: BodyInit = screenshot
    ? new FormData()
    : JSON.stringify(analyzePayload);
  if (body instanceof FormData && screenshot) {
    body.set("text", clientMasked.text);
    body.set("client_request_id", clientRequestId);
    body.set("screenshot", screenshot);
    body.set("screenshot_redacted_confirmed", String(screenshotRedactedConfirmed));
  }
  // ponytail: 30초 동안 같은 멱등 요청을 폴링한다. 장기 작업이 필요해지면 전용 상태 조회 API로 교체.
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const result = adaptAnalysisResult(await request<ApiAnalysisResponse>("/api/reports/analyze", { method: "POST", body }));
    if (result.status !== "pending") return result;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("분석이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.");
}

function localDateTimeToIso(date: string | null, time: string | null): string | null {
  if (!date && !time) return null;
  if (!date || !time) throw new Error("발생 날짜와 시각을 모두 입력해 주세요.");
  const value = new Date(`${date}T${time}:00`);
  if (Number.isNaN(value.getTime())) throw new Error("발생 날짜와 시각 형식이 올바르지 않습니다.");
  return value.toISOString();
}

function toOffsetIso(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) throw new Error("주문 시각 형식이 올바르지 않습니다.");
  return parsed.toISOString();
}

export async function saveConfirmedReport(payload: {
  analysis_id: string;
  analysis_version: number;
  attachment_id: string | null;
  masked_text: string;
  technical: TechnicalData;
  consultation: ConsultationData;
}, clientRequestId: string = crypto.randomUUID()): Promise<SavedCard> {
  const { technical, consultation, ...report } = payload;
  if (consultation.quantity !== null
    && (!Number.isInteger(consultation.quantity) || consultation.quantity <= 0)) {
    throw new Error("수량은 0보다 큰 정수로 입력해 주세요.");
  }
  if (consultation.order_type === "LIMIT"
    && (!Number.isInteger(consultation.price) || (consultation.price ?? 0) <= 0)) {
    throw new Error("지정가는 0보다 큰 정수 가격을 입력해 주세요.");
  }
  if (consultation.order_type === "MARKET" && consultation.price !== null) {
    throw new Error("시장가는 가격을 입력하지 않아야 합니다.");
  }
  const confirmation = {
    ...report,
    technical: {
      issue_type: technical.issue_type,
      symptom: technical.symptom.trim() || null,
      submission_status: toApiSubmissionStatus(technical.submission_status),
      error_code: technical.error_code,
      reported_occurred_at: localDateTimeToIso(technical.occurred_date, technical.occurred_at),
    },
    consultation: {
      action: consultation.action,
      symbol_name: consultation.symbol_name,
      symbol_code: consultation.symbol_code && consultation.symbol_code !== "UNKNOWN"
        ? normalizeSymbolCode(consultation.symbol_code) || null
        : null,
      quantity: consultation.quantity,
      order_type: consultation.order_type,
      price_krw: consultation.order_type === "MARKET" ? null : consultation.price,
      attempted_at: toOffsetIso(consultation.attempted_at),
    },
    client_request_id: clientRequestId,
  } satisfies ApiConfirmationRequest;

  const body = await request<ApiConfirmedResponse>("/api/reports", {
    method: "POST",
    body: JSON.stringify(confirmation),
  });
  const saved = {
    reference_number: body.consultation_card.reference_number,
    expires_at: body.consultation_card.expires_at,
  };
  return saved;
}

export async function discardAnalysis(analysisId: string, clientRequestId: string = crypto.randomUUID()): Promise<void> {
  const discard: ApiDiscardRequest = { analysis_id: analysisId, client_request_id: clientRequestId };
  await request<void>("/api/reports", {
    method: "DELETE",
    body: JSON.stringify(discard),
  });
}

export async function getSignalDashboard(limit = 50, offset = 0): Promise<SignalDashboard> {
  const dashboard = await request<SignalDashboard>(`/api/signals/dashboard?limit=${limit}&offset=${offset}`);
  if (dashboard.baseline_status === "AVAILABLE" && dashboard.baseline_ratio == null) {
    throw new ApiError("운영 상황판 기준선 응답 형식이 올바르지 않습니다.", "INVALID_DASHBOARD_CONTRACT");
  }
  return dashboard;
}

export async function loginAgent(employeeId: string, password: string): Promise<AgentSession> {
  const login: ApiAgentLoginRequest = { employee_id: employeeId, password };
  return request<ApiAgentLoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(login),
  }, null);
}

export function normalizeSymbolCode(value: string): string {
  return value.toUpperCase().replace(/[^0-9A-Z]/g, "").slice(0, 6);
}

function normalizedSelector(selector: AgentCardSelector | string): AgentCardSelector {
  if (typeof selector === "string") return { reference_number: selector.trim().toUpperCase() };
  if (selector.reference_number !== undefined) {
    return { reference_number: selector.reference_number.trim().toUpperCase() };
  }
  return { card_id: selector.card_id.trim() };
}

function requireAgentToken(agentToken?: string): string {
  if (!agentToken) throw new ApiError("로그인이 필요합니다.", undefined, 401);
  return agentToken;
}

export async function getConsultationCards(
  agentToken?: string,
  limit = 50,
  offset = 0,
): Promise<AgentCardListResponse> {
  return request<AgentCardListResponse>(
    `/api/agent/consultation-cards?limit=${limit}&offset=${offset}`,
    {},
    requireAgentToken(agentToken),
  );
}

export async function getConsultationCard(
  selector: AgentCardSelector | string,
  agentToken?: string,
): Promise<AgentCase> {
  const normalized = normalizedSelector(selector);
  const lookup: ApiAgentLookupRequest = normalized;
  return request<AgentCase>("/api/consultation-cards/lookup", {
    method: "POST",
    body: JSON.stringify(lookup),
  }, requireAgentToken(agentToken));
}

export async function deleteConsultationCard(reference: string, clientRequestId: string = crypto.randomUUID()): Promise<void> {
  const normalized = reference.trim().toUpperCase();
  const deletion: ApiDeleteCardRequest = { reference_number: normalized, client_request_id: clientRequestId };
  await request<void>("/api/consultation-cards", {
    method: "DELETE",
    body: JSON.stringify(deletion),
  });
}

export async function saveAgentVerification(
  selector: AgentCardSelector | string,
  payload: AgentVerificationInput,
  agentToken?: string,
  clientRequestId: string = crypto.randomUUID(),
): Promise<AgentVerificationResult> {
  const normalized = normalizedSelector(selector);
  const verification: ApiAgentVerificationRequest = {
    ...normalized,
    ...payload,
    symbol_code: payload.symbol_code ? normalizeSymbolCode(payload.symbol_code) || null : null,
    client_request_id: clientRequestId,
  };
  return request<AgentVerificationResult>(
    "/api/consultation-cards/verifications",
    {
      method: "POST",
      body: JSON.stringify(verification),
    },
    requireAgentToken(agentToken),
  );
}

export async function saveAgentSignalVerification(
  selector: AgentCardSelector | string,
  payload: AgentSignalVerificationInput,
  agentToken?: string,
  clientRequestId: string = crypto.randomUUID(),
): Promise<AgentSignalVerificationResult> {
  const verification: ApiAgentSignalVerificationRequest = {
    ...normalizedSelector(selector),
    ...payload,
    client_request_id: clientRequestId,
  };
  return request<AgentSignalVerificationResult>(
    "/api/consultation-cards/signal-verifications",
    { method: "POST", body: JSON.stringify(verification) },
    requireAgentToken(agentToken),
  );
}
