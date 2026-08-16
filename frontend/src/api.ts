import { analyzeLocally, maskSensitiveText } from "./mocks/analyzeReport";
import type {
  AgentCase,
  AgentSession,
  AgentVerificationInput,
  AgentVerificationResult,
  AnalysisResponse,
  ConsultationData,
  DashboardSnapshot,
  SavedCard,
  TechnicalData,
} from "./types";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "");
export const DEMO_REFERENCE_NUMBER = "KBSOS-7H4Q-9M2P";
const SESSION_TOKEN_KEY = "mts-sos-session-token";
let memorySessionToken = "";
const mockAttachments = new Map<string, string>();

let mockDashboard: DashboardSnapshot = {
  updated_at: new Date().toISOString(),
  baseline_ratio: 2.4,
  volume: [
    { time: "08:00", count: 5 },
    { time: "08:30", count: 9 },
    { time: "09:00", count: 17 },
    { time: "09:30", count: 31 },
    { time: "10:00", count: 24 },
    { time: "10:30", count: 13 },
  ],
  signals: [
    {
      id: "SIG-MA-001",
      title: "주문 제출 후 무한 로딩",
      status: "REVIEW_REQUIRED",
      report_count: 32,
      raw_report_count: 38,
      change: "+167%",
      first_seen: "2026-08-15T00:02:00.000Z",
      last_seen: "2026-08-15T01:18:00.000Z",
      channel: "M-able",
      feature_area: "국내주식 주문",
      symptom: "매도 주문 버튼을 누른 뒤 로딩 화면이 끝나지 않고 주문번호를 확인하지 못함",
      representative_report: "매도 확인 후 다음 화면으로 넘어가지 않고 계속 로딩됩니다.",
      action: "재주문 전 주문 체결·미체결 내역을 먼저 확인하도록 안내",
      official_notice_url: null,
    },
    {
      id: "SIG-MA-002",
      title: "주문 결과 화면 미표시",
      status: "SIGNAL_DETECTED",
      report_count: 18,
      raw_report_count: 22,
      change: "+80%",
      first_seen: "2026-08-15T00:17:00.000Z",
      last_seen: "2026-08-15T01:11:00.000Z",
      channel: "M-able",
      feature_area: "국내주식 주문",
      symptom: "주문 제출 뒤 완료 또는 실패 결과가 표시되지 않아 접수 여부를 알 수 없음",
      representative_report: "주문을 넣었는데 완료인지 실패인지 결과가 표시되지 않아요.",
      action: "상담 시 주문 시각과 종목을 확인하고 접수 내역 조회를 우선 진행",
      official_notice_url: null,
    },
    {
      id: "SIG-MA-003",
      title: "체결 내역 반영 지연",
      status: "RESOLVED",
      report_count: 7,
      raw_report_count: 9,
      change: "-42%",
      first_seen: "2026-08-14T23:41:00.000Z",
      last_seen: "2026-08-15T00:36:00.000Z",
      channel: "M-able",
      feature_area: "체결내역 조회",
      symptom: "주문은 체결됐으나 체결 내역과 잔고 화면 반영이 늦게 나타남",
      representative_report: "체결 알림은 왔는데 잔고와 체결내역이 그대로예요.",
      action: "추가 유입 여부를 모니터링하고 동일 증상 재발 시 신호를 다시 열기",
      official_notice_url: null,
    },
  ],
  policy: {
    title: "KB증권 주문장애 발생 시 처리특례",
    version: "민원사무편람",
    checked_at: "2026-08-14",
    source_url: "https://www.kbsec.com/go.able?linkcd=s060318010004&utm_source=chatgpt.com",
  },
};

const demoAnalysis = analyzeLocally(
  "9시 3분쯤 KB 앱에서 삼전 스무 주를 7만 원에 팔려고 했는데 주문 버튼을 누른 뒤 계속 로딩됐고 주문번호는 확인하지 못했어요.",
);

function makeMockCase(
  reference_number: string,
  expires_at: string,
  technical: TechnicalData,
  consultation: ConsultationData,
  attachment_url: string | null = null,
): AgentCase {
  return {
    reference_number,
    expires_at,
    technical,
    consultation,
    related_signal: mockDashboard.signals[0] ?? null,
    similarity: mockDashboard.signals.length ? 0.94 : null,
    attachment_url,
  };
}

const mockCards = new Map<string, AgentCase>([
  [
    DEMO_REFERENCE_NUMBER,
    makeMockCase(
      DEMO_REFERENCE_NUMBER,
      new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
      demoAnalysis.technical,
      demoAnalysis.consultation,
    ),
  ],
]);

function randomGroup(length: number): string {
  const alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
  return Array.from({ length }, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join("");
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

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      const messages = body.detail.flatMap((item) =>
        item && typeof item === "object" && "msg" in item ? [String(item.msg)] : [],
      );
      if (messages.length) return messages.join(", ");
    }
    if (body.detail && typeof body.detail === "object") return "요청 값이 올바르지 않습니다.";
    return "요청을 처리하지 못했습니다.";
  } catch {
    return "요청을 처리하지 못했습니다.";
  }
}

async function request<T>(path: string, init: RequestInit = {}, token: string | null = sessionToken()): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json, application/problem+json",
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const detail = await parseError(response);
    throw new Error(response.status === 409 ? `최신 상태와 충돌했습니다. 다시 확인해 주세요. ${detail}` : detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function isAnalysisResponse(value: unknown): value is AnalysisResponse {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  return typeof body.analysis_id === "string"
    && typeof body.analysis_version === "number"
    && body.status === "confirmation"
    && (body.attachment === null || Boolean(
      body.attachment
      && typeof body.attachment === "object"
      && typeof (body.attachment as Record<string, unknown>).id === "string"
      && typeof (body.attachment as Record<string, unknown>).url === "string",
    ))
    && typeof body.masked_text === "string"
    && Array.isArray(body.masked_items)
    && Boolean(body.technical && typeof body.technical === "object")
    && Boolean(body.consultation && typeof body.consultation === "object");
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

export async function analyzeReport(rawText: string, clientRequestId: string = crypto.randomUUID(), screenshot?: File): Promise<AnalysisResponse> {
  const text = normalizeReportText(rawText);
  const requestBody = JSON.stringify({ text, client_request_id: clientRequestId });
  if (new TextEncoder().encode(requestBody).byteLength > 16 * 1024) throw new Error("입력 용량은 16KiB 이하여야 합니다.");
  if (screenshot) validateScreenshot(screenshot);
  if (!apiBaseUrl) {
    const result = analyzeLocally(text);
    if (!screenshot) return result;
    const id = crypto.randomUUID();
    const url = URL.createObjectURL(screenshot);
    mockAttachments.set(id, url);
    return { ...result, attachment: { id, url } };
  }

  const clientMasked = maskSensitiveText(text);
  const body = screenshot ? new FormData() : requestBody;
  if (body instanceof FormData) {
    body.set("text", clientMasked.text);
    body.set("client_request_id", clientRequestId);
    body.set("screenshot", screenshot!);
  }
  const responseBody = await request<unknown>("/api/reports/analyze", {
    method: "POST",
    body: body instanceof FormData
      ? body
      : JSON.stringify({ text: clientMasked.text, client_request_id: clientRequestId }),
  });
  if (!isAnalysisResponse(responseBody)) throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
  return responseBody;
}

export async function saveConfirmedReport(payload: {
  analysis_id: string;
  analysis_version: number;
  attachment_id: string | null;
  masked_text: string;
  technical: TechnicalData;
  consultation: ConsultationData;
}, clientRequestId: string = crypto.randomUUID()): Promise<SavedCard> {
  if (!apiBaseUrl) {
    const saved = {
      reference_number: `KBSOS-${randomGroup(4)}-${randomGroup(4)}`,
      expires_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
    };
    // ponytail: Mock는 모든 제보를 첫 신호에 합산한다. 백엔드 군집 API 연결 시 제거.
    const [first, ...rest] = mockDashboard.signals;
    const lastVolume = mockDashboard.volume.length - 1;
    mockDashboard = {
      ...mockDashboard,
      updated_at: new Date().toISOString(),
      signals: first
        ? [{
            ...first,
            report_count: first.report_count + 1,
            raw_report_count: first.raw_report_count + 1,
            last_seen: new Date().toISOString(),
          }, ...rest]
        : [],
      volume: mockDashboard.volume.map((item, index) =>
        index === lastVolume ? { ...item, count: item.count + 1 } : item,
      ),
    };
    mockCards.set(
      saved.reference_number,
      makeMockCase(
        saved.reference_number,
        saved.expires_at,
        payload.technical,
        payload.consultation,
        payload.attachment_id ? mockAttachments.get(payload.attachment_id) ?? null : null,
      ),
    );
    return saved;
  }

  const body = await request<{
    consultation_card: { reference_number: string; expires_at: string };
  }>("/api/reports", {
    method: "POST",
    body: JSON.stringify({ ...payload, client_request_id: clientRequestId }),
  });
  return {
    reference_number: body.consultation_card.reference_number,
    expires_at: body.consultation_card.expires_at,
  };
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  if (!apiBaseUrl) return structuredClone(mockDashboard);
  return request<DashboardSnapshot>("/api/signals/dashboard");
}

export async function loginAgent(employeeId: string, password: string): Promise<AgentSession> {
  if (!apiBaseUrl) return { access_token: `mock-agent-${crypto.randomUUID()}`, agent_label: employeeId };
  const body = await request<{ access_token: string; agent_label?: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ employee_id: employeeId, password }),
  }, null);
  if (!body.access_token) throw new Error("로그인 응답에 인증 토큰이 없습니다.");
  return { access_token: body.access_token, agent_label: body.agent_label ?? employeeId };
}

export async function getConsultationCard(reference: string, agentToken?: string): Promise<AgentCase> {
  const normalized = reference.trim().toUpperCase();
  if (!apiBaseUrl) {
    const card = mockCards.get(normalized);
    if (!card) throw new Error("일치하는 참조번호를 찾지 못했습니다.");
    if (new Date(card.expires_at).getTime() <= Date.now()) throw new Error("만료된 참조번호입니다.");
    return structuredClone(card);
  }
  return request<AgentCase>("/api/consultation-cards/lookup", {
    method: "POST",
    body: JSON.stringify({ reference_number: normalized }),
  }, agentToken);
}

export async function deleteConsultationCard(reference: string, clientRequestId: string = crypto.randomUUID()): Promise<void> {
  const normalized = reference.trim().toUpperCase();
  if (!apiBaseUrl) {
    const card = mockCards.get(normalized);
    if (!card) throw new Error("삭제할 상담 준비카드를 찾지 못했습니다.");
    if (card.attachment_url?.startsWith("blob:")) URL.revokeObjectURL(card.attachment_url);
    mockCards.delete(normalized);
    return;
  }
  await request<void>("/api/consultation-cards", {
    method: "DELETE",
    body: JSON.stringify({ reference_number: normalized, client_request_id: clientRequestId }),
  });
}

export async function saveAgentVerification(
  reference: string,
  payload: AgentVerificationInput,
  agentToken?: string,
  clientRequestId: string = crypto.randomUUID(),
): Promise<AgentVerificationResult> {
  const normalized = reference.trim().toUpperCase();
  if (!apiBaseUrl) {
    const card = await getConsultationCard(normalized);
    const issues: AgentVerificationResult["issues"] = [];
    if (card.consultation.action !== "UNKNOWN" && payload.action !== card.consultation.action) {
      issues.push({
        field: "action",
        level: "IMPORTANT",
        label: "주문 구분 불일치",
        customer_value: card.consultation.action,
        agent_value: payload.action,
      });
    }
    if (card.consultation.symbol_name !== null && payload.symbol_name !== card.consultation.symbol_name) {
      issues.push({
        field: "symbol_name",
        level: "IMPORTANT",
        label: "종목 불일치",
        customer_value: card.consultation.symbol_name,
        agent_value: payload.symbol_name ?? "모름",
      });
    }
    if (card.consultation.symbol_code !== null && payload.symbol_code !== card.consultation.symbol_code) {
      issues.push({
        field: "symbol_code",
        level: "IMPORTANT",
        label: "종목코드 불일치",
        customer_value: card.consultation.symbol_code,
        agent_value: payload.symbol_code ?? "모름",
      });
    }
    if (card.consultation.quantity !== null && payload.quantity !== card.consultation.quantity) {
      issues.push({
        field: "quantity",
        level: "IMPORTANT",
        label: "수량 불일치",
        customer_value: `${card.consultation.quantity}주`,
        agent_value: payload.quantity === null ? "모름" : `${payload.quantity}주`,
      });
    }
    if (card.consultation.price !== null && payload.price !== card.consultation.price) {
      issues.push({
        field: "price",
        level: "IMPORTANT",
        label: "가격 불일치",
        customer_value: `${card.consultation.price.toLocaleString()}원`,
        agent_value: payload.price === null ? "모름" : `${payload.price.toLocaleString()}원`,
      });
    }
    if (card.consultation.order_type !== "UNKNOWN" && payload.order_type !== card.consultation.order_type) {
      issues.push({
        field: "order_type",
        level: "IMPORTANT",
        label: "주문 방식 불일치",
        customer_value: card.consultation.order_type,
        agent_value: payload.order_type,
      });
    }
    if (card.technical.submission_status !== "UNKNOWN" && payload.submission_status !== card.technical.submission_status) {
      issues.push({
        field: "submission_status",
        level: "IMPORTANT",
        label: "주문 제출 여부 불일치",
        customer_value: card.technical.submission_status,
        agent_value: payload.submission_status,
      });
    } else if (payload.submission_status === "UNKNOWN") {
      issues.push({
        field: "submission_status",
        level: "NEEDS_CONFIRMATION",
        label: "주문 제출 여부 미확인",
        customer_value: "확인 불가",
        agent_value: "확인 불가",
      });
    }
    mockCards.set(normalized, {
      ...card,
      technical: { ...card.technical, submission_status: payload.submission_status },
      consultation: {
        ...card.consultation,
        action: payload.action,
        symbol_name: payload.symbol_name,
        symbol_code: payload.symbol_code,
        quantity: payload.quantity,
        price: payload.price,
        order_type: payload.order_type,
      },
    });
    return { saved_at: new Date().toISOString(), issues };
  }
  return request<AgentVerificationResult>(
    "/api/consultation-cards/verifications",
    {
      method: "POST",
      body: JSON.stringify({ reference_number: normalized, ...payload, client_request_id: clientRequestId }),
    },
    agentToken,
  );
}
