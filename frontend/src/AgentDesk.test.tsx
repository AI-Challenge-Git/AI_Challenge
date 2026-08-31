import { afterEach, describe, expect, it, vi } from "vitest";

const token = "A".repeat(43);
const cardId = "11111111-1111-4111-8111-111111111111";

const loginResponse = {
  access_token: token,
  token_type: "bearer",
  expires_at: "2026-08-27T01:00:00Z",
  agent_label: "CS1024",
  role: "AGENT",
};

const cardDetail = {
  card_id: cardId,
  created_at: "2026-08-27T00:00:00Z",
  expires_at: "2026-08-27T02:00:00Z",
  technical: {
    issue_type: "ORDER_SUBMISSION_FAILURE",
    symptom: "주문 버튼 이후 지속 로딩",
    submission_status: "CUSTOMER_REPORTED_SUBMITTED",
    error_code: null,
    reported_occurred_at: "2026-08-27T00:03:00Z",
  },
  consultation: {
    action: "SELL",
    symbol_name: "삼성전자",
    symbol_code: "005930",
    quantity: 20,
    order_type: "LIMIT",
    price_krw: 70_000,
    attempted_at: "2026-08-27T00:03:00Z",
  },
  verification_status: null,
  safety_notice: "공식 채널에서 주문 상태를 확인해 주세요.",
  has_attachment: true,
  attachment_url: "https://storage.example.com/signed-image",
  related_signals: [{
    signal_id: "33333333-3333-4333-8333-333333333333",
    status: "SIGNAL_DETECTED",
    reported_symptom_type: "ORDER_SUBMISSION_FAILURE",
    reporting_unique_sessions: 3,
    last_report_at: "2026-08-30T00:04:00Z",
    official_incident: false,
    relevance_status: "NEEDS_CONFIRMATION",
    confirmation_questions: ["고객이 문제를 겪은 시각이 신호 발생 구간과 일치하나요?"],
    locked_related: null,
  }],
};

const listResponse = {
  items: [{
    card_id: cardId,
    received_at: "2026-08-27T00:00:00Z",
    issued_at: "2026-08-27T00:00:00Z",
    expires_at: "2026-08-27T02:00:00Z",
    expired: false,
    can_open: true,
    consultation_status: "OPEN",
    technical_symptom: "주문 버튼 이후 지속 로딩",
    verification_status: null,
  }],
  limit: 50,
  offset: 0,
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("상담원 API 계약", () => {
  it("API 주소가 있으면 CS1024/demo도 실제 로그인과 목록 API를 호출한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(loginResponse)))
      .mockResolvedValueOnce(new Response(JSON.stringify(listResponse)));
    vi.stubGlobal("fetch", fetchMock);
    const { getConsultationCards, loginAgent } = await import("./api");

    const agent = await loginAgent("CS1024", "demo");
    const cards = await getConsultationCards(agent.access_token);

    expect(agent.access_token).toBe(token);
    expect(cards.items[0]).toMatchObject({ card_id: cardId, can_open: true });
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example.com/api/auth/login");
    expect(fetchMock.mock.calls[1][0]).toBe("https://api.example.com/api/agent/consultation-cards?limit=50&offset=0");
  });

  it("카드 목록의 서버 만료·재확인 상태를 유지한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const serverList = {
      ...listResponse,
      items: [
        { ...listResponse.items[0], expired: true, can_open: false },
        { ...listResponse.items[0], card_id: "22222222-2222-4222-8222-222222222222", consultation_status: "VERIFIED" },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(serverList))));
    const { getConsultationCards } = await import("./api");

    await expect(getConsultationCards(token)).resolves.toEqual(serverList);
  });

  it("card_id로 상세를 조회하고 새 ConsultationCardDetail을 반환한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(cardDetail)));
    vi.stubGlobal("fetch", fetchMock);
    const { getConsultationCard } = await import("./api");

    const detail = await getConsultationCard({ card_id: cardId }, token);

    expect(detail).toMatchObject({
      card_id: cardId,
      has_attachment: true,
      attachment_url: "https://storage.example.com/signed-image",
      related_signals: [{ status: "SIGNAL_DETECTED", reporting_unique_sessions: 3, official_incident: false }],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/consultation-cards/lookup",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ card_id: cardId }),
        headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
      }),
    );
  });

  it("관련 신호 확인은 card_id·signal_id·decision과 UUID를 Bearer token으로 전송한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const response = {
      signal_id: cardDetail.related_signals[0].signal_id,
      relevance_status: "NEEDS_CONFIRMATION",
      agent_decision: "RELATED",
      verification_status: "MATCHED",
      final_related: true,
      lock_decision: "ALLOW",
      saved_at: "2026-08-27T00:10:00Z",
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response)));
    vi.stubGlobal("fetch", fetchMock);
    const { saveAgentSignalVerification } = await import("./api");
    const requestId = "44444444-4444-4444-8444-444444444444";

    await expect(saveAgentSignalVerification(
      { card_id: cardId },
      { signal_id: response.signal_id, decision: "RELATED" },
      token,
      requestId,
    )).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/consultation-cards/signal-verifications",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ card_id: cardId, signal_id: response.signal_id, decision: "RELATED", client_request_id: requestId }),
        headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
        credentials: "omit",
      }),
    );
  });

  it("verification은 price_krw와 서버 submission enum을 전송한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const response = {
      verification_id: "22222222-2222-4222-8222-222222222222",
      status: "IMPORTANT",
      fields: [{ field: "price_krw", status: "IMPORTANT", customer_value: 70_000, agent_value: 71_000 }],
      mismatch_fields: ["price_krw"],
      saved_at: "2026-08-27T00:10:00Z",
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(response)));
    vi.stubGlobal("fetch", fetchMock);
    const { saveAgentVerification } = await import("./api");

    const result = await saveAgentVerification({ card_id: cardId }, {
      action: "SELL",
      symbol_name: "삼성전자",
      symbol_code: "005930",
      quantity: 20,
      price_krw: 71_000,
      order_type: "LIMIT",
      submission_status: "CUSTOMER_REPORTED_SUBMITTED",
      order_history_checked: true,
    }, token, "33333333-3333-4333-8333-333333333333");

    const sent = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(sent).toMatchObject({
      card_id: cardId,
      price_krw: 71_000,
      submission_status: "CUSTOMER_REPORTED_SUBMITTED",
      client_request_id: "33333333-3333-4333-8333-333333333333",
    });
    expect(sent).not.toHaveProperty("price");
    expect(result).toMatchObject({ status: "IMPORTANT", mismatch_fields: ["price_krw"] });
  });

});
