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
  it("고객 세션 토큰으로 실제 Dashboard API를 조회한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const response = {
      updated_at: "2026-08-30T00:06:00Z",
      items: [],
      hourly_volume: [],
      applied_policy: null,
      baseline_status: "INSUFFICIENT_HISTORY",
      baseline_ratio: null,
      limit: 50,
      offset: 0,
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response)));
    vi.stubGlobal("fetch", fetchMock);
    const { getSignalDashboard } = await import("./api");

    await expect(getSignalDashboard()).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/signals/dashboard?limit=50&offset=0",
      expect.objectContaining({ credentials: "omit", headers: expect.objectContaining({ Authorization: expect.stringMatching(/^Bearer /) }) }),
    );
  });

  it("종목코드를 대문자 영숫자 6자리로 정규화한다", async () => {
    const { normalizeSymbolCode } = await import("./api");

    expect(normalizeSymbolCode("0011a0")).toBe("0011A0");
    expect(normalizeSymbolCode("00-11a0!")).toBe("0011A0");
  });

  it("API 주소가 없으면 로컬 가짜 분석으로 대체하지 않는다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { analyzeReport } = await import("./api");

    await expect(analyzeReport("주문 버튼을 누른 뒤 계속 로딩되어 결과를 확인하지 못했습니다."))
      .rejects.toThrow("VITE_API_BASE_URL");
    expect(fetchMock).not.toHaveBeenCalled();
  });

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
    const { saveConfirmedReport } = await import("./api");

    const saved = await saveConfirmedReport({
      analysis_id: confirmationResponse.analysis_id,
      analysis_version: 1,
      attachment_id: null,
      masked_text: confirmationResponse.masked_text,
      technical,
      consultation: { ...consultation, action: "BUY", symbol_code: "0011a0" },
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
      symbol_code: "0011A0",
      quantity: 20,
      order_type: "LIMIT",
      price_krw: 70_000,
      attempted_at: "2026-08-18T00:03:00.000Z",
    });
    expect(JSON.stringify(sent)).not.toMatch(/field_statuses|evidence|occurred_date|channel|feature_area|"price":/);

    expect(saved).toEqual({ reference_number: "KBSOS-TEST", expires_at: expiresAt });
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

    await analyzeReport(reportText, requestId, screenshot, true);

    const body = fetchMock.mock.calls[0][1]?.body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("client_request_id")).toBe(requestId);
    expect((body as FormData).get("screenshot")).toBe(screenshot);
    expect((body as FormData).get("text")).toBe(reportText);
    expect((body as FormData).get("screenshot_redacted_confirmed")).toBe("true");
  });

  it("이미지 가림 확인 전에는 분석 요청을 전송하지 않는다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { analyzeReport } = await import("./api");
    const screenshot = new File(["image"], "error.png", { type: "image/png" });

    await expect(analyzeReport(
      "9시 3분쯤 주문 버튼 이후 계속 로딩되고 결과를 확인하지 못했습니다.",
      crypto.randomUUID(),
      screenshot,
    )).rejects.toThrow("직접 가렸는지 확인");
    expect(fetchMock).not.toHaveBeenCalled();
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

  it("401·404·409·422·429를 안전한 화면 오류로 변환한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const problem = (status: number, code: string, detail = "내부 상세정보") => JSON.stringify({
      type: "about:blank", title: "Error", status, code, detail,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(problem(401, "INVALID_AGENT_CREDENTIALS"), { status: 401 }))
      .mockResolvedValueOnce(new Response(problem(404, "CARD_NOT_FOUND"), { status: 404 }))
      .mockResolvedValueOnce(new Response(problem(409, "IDEMPOTENCY_CONFLICT"), { status: 409 }))
      .mockResolvedValueOnce(new Response(problem(422, "SCREENSHOT_REDACTION_REQUIRED"), { status: 422 }))
      .mockResolvedValueOnce(new Response(problem(429, "RATE_LIMITED"), { status: 429, headers: { "Retry-After": "37" } }));
    vi.stubGlobal("fetch", fetchMock);
    const { analyzeReport, getConsultationCard, getConsultationCards, saveAgentVerification } = await import("./api");
    const cardId = "11111111-1111-4111-8111-111111111111";

    await expect(getConsultationCards("agent-token")).rejects.toMatchObject({ status: 401, message: expect.stringContaining("다시 로그인") });
    await expect(getConsultationCard({ card_id: cardId }, "agent-token")).rejects.toMatchObject({ status: 404, message: "요청한 상담 정보를 찾을 수 없습니다." });
    await expect(saveAgentVerification({ card_id: cardId }, {
      action: "SELL",
      symbol_name: null,
      symbol_code: null,
      quantity: null,
      order_type: "UNKNOWN",
      price_krw: null,
      submission_status: "UNKNOWN",
      order_history_checked: true,
    }, "agent-token", "33333333-3333-4333-8333-333333333333")).rejects.toMatchObject({ status: 409, message: expect.stringContaining("요청 ID") });
    await expect(analyzeReport(
      "주문 버튼을 누른 뒤 계속 로딩되어 결과를 확인하지 못했습니다.",
      "44444444-4444-4444-8444-444444444444",
      new File(["image"], "error.png", { type: "image/png" }),
      true,
    )).rejects.toMatchObject({ status: 422, code: "SCREENSHOT_REDACTION_REQUIRED" });
    await expect(getConsultationCards("agent-token")).rejects.toMatchObject({
      status: 429,
      retryAfterSeconds: 37,
      message: expect.stringContaining("37초"),
    });
  });

  it("AI 실패 코드를 안전한 사용자 문구로 구분한다", async () => {
    const { analysisFailureMessage } = await import("./api");

    expect(analysisFailureMessage("TIMEOUT")).toContain("시간이 초과");
    expect(analysisFailureMessage("INVALID_SCHEMA")).toContain("처리하지 못했습니다");
    expect(analysisFailureMessage("PROVIDER_UNAVAILABLE")).toContain("서비스를 이용할 수 없습니다");
    expect(analysisFailureMessage("PROVIDER_UNAVAILABLE")).not.toMatch(/결제|크레딧|OpenAI/i);
  });

  it("관련성 확정 충돌은 자동 변경하지 않고 수동 검토로 안내한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: "SIGNAL_RELEVANCE_CONFLICT",
      detail: "내부 결과",
    }), { status: 409 })));
    const { saveAgentSignalVerification } = await import("./api");

    await expect(saveAgentSignalVerification(
      { card_id: "11111111-1111-4111-8111-111111111111" },
      { signal_id: "33333333-3333-4333-8333-333333333333", decision: "NOT_RELATED" },
      "agent-token",
      "44444444-4444-4444-8444-444444444444",
    )).rejects.toMatchObject({ code: "SIGNAL_RELEVANCE_CONFLICT", message: expect.stringContaining("수동 검토") });
  });

  it("종목 Master 오류를 사용자가 이해할 수 있는 문구로 변환한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const problem = (status: number, code: string) => JSON.stringify({
      type: "about:blank", title: "Error", status, code, detail: "내부 상세정보",
    });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(problem(503, "SYMBOL_MASTER_UNAVAILABLE"), { status: 503 }))
      .mockResolvedValueOnce(new Response(problem(422, "UNSUPPORTED_SYMBOL"), { status: 422 }))
      .mockResolvedValueOnce(new Response(problem(422, "SYMBOL_MISMATCH"), { status: 422 })));
    const { saveConfirmedReport } = await import("./api");
    const payload = {
      analysis_id: confirmationResponse.analysis_id,
      analysis_version: 1,
      attachment_id: null,
      masked_text: confirmationResponse.masked_text,
      technical,
      consultation,
    };

    await expect(saveConfirmedReport(payload)).rejects.toMatchObject({ code: "SYMBOL_MASTER_UNAVAILABLE", message: expect.stringContaining("일시적으로") });
    await expect(saveConfirmedReport(payload)).rejects.toMatchObject({ code: "UNSUPPORTED_SYMBOL", message: expect.stringContaining("지원하지 않는 종목코드") });
    await expect(saveConfirmedReport(payload)).rejects.toMatchObject({ code: "SYMBOL_MISMATCH", message: expect.stringContaining("일치하지 않습니다") });
  });
});
