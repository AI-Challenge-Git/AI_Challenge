import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsultationData, TechnicalData } from "./types";

const candidate = <T,>(value: T | null, status = "CONFIRMED_FROM_TEXT", evidence_quote: string | null = null) => ({
  value,
  status,
  evidence_quote,
});

const confirmationResponse = {
  analysis_id: "56d736c8-9db2-4a59-a2d2-24f96ef6bfdb",
  analysis_version: 1,
  status: "confirmation",
  attachment: null,
  masked_text: "9시 3분쯤 주문 버튼 이후 계속 로딩됩니다.",
  masked_items: [],
  technical: {
    issue_type: candidate("ORDER_SUBMISSION_FAILURE", "CONFIRMED_FROM_TEXT", "계속 로딩"),
    symptom: candidate("주문 버튼 이후 지속 로딩", "CONFIRMED_FROM_TEXT", "계속 로딩"),
    submission_status: candidate("UNKNOWN", "UNKNOWN"),
    error_code: candidate(null, "UNKNOWN"),
    reported_occurred_at: candidate(null, "NEEDS_CONFIRMATION", "9시 3분쯤"),
  },
  consultation: {
    action: candidate("SELL"),
    symbol_name: candidate("삼성전자"),
    symbol_code: candidate("005930"),
    quantity: candidate(20),
    order_type: candidate("LIMIT"),
    price_krw: candidate(70_000),
    attempted_at: candidate(null, "NEEDS_CONFIRMATION", "9시 3분쯤"),
  },
};

const technical: TechnicalData = {
  occurred_date: "2026-08-18",
  occurred_at: "09:03",
  channel: "UNKNOWN",
  feature_area: "UNKNOWN",
  issue_type: "ORDER_SUBMISSION_FAILURE",
  symptom: "주문 버튼 이후 지속 로딩",
  submission_status: "SUBMITTED",
  error_code: null,
  field_statuses: {
    occurred_date: "NEEDS_CONFIRMATION",
    occurred_at: "NEEDS_CONFIRMATION",
    channel: "UNKNOWN",
    feature_area: "UNKNOWN",
    issue_type: "CONFIRMED_FROM_TEXT",
    symptom: "CONFIRMED_FROM_TEXT",
    submission_status: "CONFIRMED_FROM_TEXT",
    error_code: "UNKNOWN",
  },
  evidence: { symptom: "계속 로딩" },
};

const consultation: ConsultationData = {
  action: "SELL",
  symbol_name: "삼성전자",
  symbol_code: "005930",
  quantity: 20,
  order_type: "LIMIT",
  price: 70_000,
  attempted_at: "2026-08-18T00:03:00Z",
  field_statuses: {
    action: "CONFIRMED_FROM_TEXT",
    symbol_name: "CONFIRMED_FROM_TEXT",
    symbol_code: "CONFIRMED_FROM_TEXT",
    quantity: "CONFIRMED_FROM_TEXT",
    order_type: "CONFIRMED_FROM_TEXT",
    price: "CONFIRMED_FROM_TEXT",
    attempted_at: "CONFIRMED_FROM_TEXT",
  },
  evidence: { price: "7만 원" },
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("백엔드 분석 DTO 연동", () => {
  it("pending이면 같은 요청으로 재시도하고 Candidate를 화면 모델로 변환한다", async () => {
    vi.useFakeTimers();
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        analysis_id: confirmationResponse.analysis_id,
        analysis_version: 1,
        status: "pending",
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify(confirmationResponse)));
    vi.stubGlobal("fetch", fetchMock);
    const { analyzeReport } = await import("./api");

    const pending = analyzeReport("9시 3분쯤 주문 버튼 이후 계속 로딩되고 결과를 확인하지 못했습니다.", "d2095cc3-8ab4-48db-adcb-c41275182497");
    await vi.advanceTimersByTimeAsync(1000);
    const result = await pending;

    expect(result.status).toBe("confirmation");
    if (result.status !== "confirmation") return;
    expect(result.technical).toMatchObject({ occurred_date: null, occurred_at: null, issue_type: "ORDER_SUBMISSION_FAILURE" });
    expect(result.consultation).toMatchObject({ price: 70_000, attempted_at: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1]?.body).toBe(fetchMock.mock.calls[1][1]?.body);
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>).Authorization)
      .toBe((fetchMock.mock.calls[1][1]?.headers as Record<string, string>).Authorization);
  });

  it("failed와 complete 응답도 안전하게 구분한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        analysis_id: confirmationResponse.analysis_id,
        analysis_version: 1,
        status: "failed",
        error: { code: "TIMEOUT" },
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        analysis_id: confirmationResponse.analysis_id,
        analysis_version: 1,
        status: "complete",
      })));
    vi.stubGlobal("fetch", fetchMock);
    const { analyzeReport } = await import("./api");

    await expect(analyzeReport("주문 버튼을 누른 뒤 계속 로딩되어 결과를 확인하지 못했습니다.", crypto.randomUUID()))
      .resolves.toMatchObject({ status: "failed", error: { code: "TIMEOUT" } });
    await expect(analyzeReport("주문 버튼을 누른 뒤 계속 로딩되어 결과를 확인하지 못했습니다.", crypto.randomUUID()))
      .resolves.toMatchObject({ status: "complete" });
  });

  it("확인 요청은 화면 전용 필드를 제거하고 백엔드 DTO로 변환한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const expiresAt = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      consultation_card: { reference_number: "KBSOS-TEST", expires_at: expiresAt },
    })));
    vi.stubGlobal("fetch", fetchMock);
    const { getConsultationCard, loginAgent, saveConfirmedReport } = await import("./api");

    const saved = await saveConfirmedReport({
      analysis_id: confirmationResponse.analysis_id,
      analysis_version: 1,
      attachment_id: null,
      masked_text: confirmationResponse.masked_text,
      technical,
      consultation: { ...consultation, action: "BUY" },
    }, "50cfd27c-aef1-44fd-9e8c-de24ade721c3");

    const sent = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(sent.technical).toEqual({
      issue_type: "ORDER_SUBMISSION_FAILURE",
      symptom: "주문 버튼 이후 지속 로딩",
      submission_status: "CUSTOMER_REPORTED_SUBMITTED",
      error_code: null,
      reported_occurred_at: expect.stringMatching(/(?:Z|[+-]\d{2}:\d{2})$/),
    });
    expect(sent.consultation).toEqual({
      action: "BUY",
      symbol_name: "삼성전자",
      symbol_code: "005930",
      quantity: 20,
      order_type: "LIMIT",
      price_krw: 70_000,
      attempted_at: "2026-08-18T00:03:00.000Z",
    });
    expect(JSON.stringify(sent)).not.toMatch(/field_statuses|evidence|occurred_date|channel|feature_area|"price":/);

    const agent = await loginAgent("CS1024", "demo");
    await expect(getConsultationCard(saved.reference_number, agent.access_token))
      .resolves.toMatchObject({ reference_number: "KBSOS-TEST", technical, consultation: { ...consultation, action: "BUY" } });
  });

  it("이미지는 multipart로 같은 멱등 요청에 포함한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(confirmationResponse)));
    vi.stubGlobal("fetch", fetchMock);
    const { analyzeReport } = await import("./api");
    const screenshot = new File(["image"], "error.png", { type: "image/png" });
    const requestId = crypto.randomUUID();
    const reportText = "9시 3분쯤 주문 버튼 이후 계속 로딩되고 결과를 확인하지 못했습니다.";

    await analyzeReport(reportText, requestId, screenshot);

    const body = fetchMock.mock.calls[0][1]?.body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("client_request_id")).toBe(requestId);
    expect((body as FormData).get("screenshot")).toBe(screenshot);
    expect((body as FormData).get("text")).toBe(reportText);
  });

  it("지정가 누락과 시장가 가격 입력은 네트워크 전송 전에 거부한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { saveConfirmedReport } = await import("./api");
    const payload = {
      analysis_id: confirmationResponse.analysis_id,
      analysis_version: 1,
      attachment_id: null,
      masked_text: confirmationResponse.masked_text,
      technical,
    };

    await expect(saveConfirmedReport({ ...payload, consultation: { ...consultation, price: null } }))
      .rejects.toThrow("지정가는");
    await expect(saveConfirmedReport({ ...payload, consultation: { ...consultation, order_type: "MARKET" } }))
      .rejects.toThrow("시장가는");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("최신 분석이 아니면 재분석 가능한 오류 코드를 유지한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      code: "STALE_ANALYSIS",
      detail: "최신 분석 결과를 다시 확인해 주세요.",
    }), { status: 409 })));
    const { saveConfirmedReport } = await import("./api");

    await expect(saveConfirmedReport({
      analysis_id: confirmationResponse.analysis_id,
      analysis_version: 1,
      attachment_id: null,
      masked_text: confirmationResponse.masked_text,
      technical,
      consultation,
    })).rejects.toEqual(expect.objectContaining({
      code: "STALE_ANALYSIS",
      message: "더 최신 분석 결과가 있습니다. 다시 분석해 주세요.",
    }));
  });

  it("미확정 제보 폐기는 같은 세션 토큰과 DELETE body를 사용한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const { discardAnalysis } = await import("./api");

    await discardAnalysis(confirmationResponse.analysis_id, "810b5eab-eaa0-4807-9f5f-a1157f2d9f3a");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/reports",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({
          analysis_id: confirmationResponse.analysis_id,
          client_request_id: "810b5eab-eaa0-4807-9f5f-a1157f2d9f3a",
        }),
        headers: expect.objectContaining({ Authorization: expect.stringMatching(/^Bearer [A-Za-z0-9_-]{43}$/) }),
      }),
    );
  });
});
