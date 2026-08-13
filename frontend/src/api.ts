import { analyzeLocally, maskSensitiveText } from "./mocks/analyzeReport";
import type {
  AgentCase,
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
      first_seen: "09:02",
      last_seen: "10:18",
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
      first_seen: "09:17",
      last_seen: "10:11",
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
      first_seen: "08:41",
      last_seen: "09:36",
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
    source_url: "https://www.kbsec.com/go.able?linkcd=s061000010002",
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
): AgentCase {
  return {
    reference_number,
    expires_at,
    technical,
    consultation,
    related_signal: mockDashboard.signals[0] ?? null,
    similarity: mockDashboard.signals.length ? 0.94 : null,
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

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string; title?: string };
    return body.detail ?? body.title ?? "요청을 처리하지 못했습니다.";
  } catch {
    return "요청을 처리하지 못했습니다.";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    credentials: "include",
    headers: { Accept: "application/json, application/problem+json", "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(await parseError(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function isAnalysisResponse(value: unknown): value is AnalysisResponse {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  return typeof body.masked_text === "string"
    && Array.isArray(body.masked_items)
    && Boolean(body.technical && typeof body.technical === "object")
    && Boolean(body.consultation && typeof body.consultation === "object");
}

export async function analyzeReport(rawText: string): Promise<AnalysisResponse> {
  if (!apiBaseUrl) return analyzeLocally(rawText);

  const clientMasked = maskSensitiveText(rawText);
  const body = await request<unknown>("/api/reports/analyze", {
    method: "POST",
    body: JSON.stringify({ text: clientMasked.text }),
  });
  if (!isAnalysisResponse(body)) throw new Error("AI 분석 응답 형식이 올바르지 않습니다.");
  return body;
}

export async function saveConfirmedReport(payload: {
  masked_text: string;
  technical: TechnicalData;
  consultation: ConsultationData;
}): Promise<SavedCard> {
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
            last_seen: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }),
          }, ...rest]
        : [],
      volume: mockDashboard.volume.map((item, index) =>
        index === lastVolume ? { ...item, count: item.count + 1 } : item,
      ),
    };
    mockCards.set(
      saved.reference_number,
      makeMockCase(saved.reference_number, saved.expires_at, payload.technical, payload.consultation),
    );
    return saved;
  }

  const sessionId = sessionStorage.getItem("mts-sos-session") ?? crypto.randomUUID();
  sessionStorage.setItem("mts-sos-session", sessionId);
  const body = await request<{
    consultation_card: { reference_number: string; expires_at: string };
  }>("/api/reports", {
    method: "POST",
    body: JSON.stringify({ ...payload, session_id: sessionId }),
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

export async function getConsultationCard(reference: string): Promise<AgentCase> {
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
  });
}

export async function deleteConsultationCard(reference: string): Promise<void> {
  const normalized = reference.trim().toUpperCase();
  if (!apiBaseUrl) {
    if (!mockCards.delete(normalized)) throw new Error("삭제할 상담 준비카드를 찾지 못했습니다.");
    return;
  }
  await request<void>("/api/consultation-cards", {
    method: "DELETE",
    body: JSON.stringify({ reference_number: normalized }),
  });
}

export async function saveAgentVerification(
  reference: string,
  payload: AgentVerificationInput,
): Promise<AgentVerificationResult> {
  const normalized = reference.trim().toUpperCase();
  if (!apiBaseUrl) {
    const card = await getConsultationCard(normalized);
    const issues: AgentVerificationResult["issues"] = [];
    if (card.consultation.symbol_name !== null && payload.symbol_name !== card.consultation.symbol_name) {
      issues.push({
        field: "symbol_name",
        level: "IMPORTANT",
        label: "종목 불일치",
        customer_value: card.consultation.symbol_name,
        agent_value: payload.symbol_name ?? "모름",
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
    if (payload.submission_status === "UNKNOWN") {
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
        symbol_name: payload.symbol_name,
        quantity: payload.quantity,
        price: payload.price,
        order_type: payload.order_type,
      },
    });
    return { saved_at: new Date().toISOString(), issues };
  }
  return request<AgentVerificationResult>(
    `/api/consultation-cards/${encodeURIComponent(normalized)}/verifications`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}
