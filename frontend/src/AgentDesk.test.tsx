import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("상담 참조번호 조회", () => {
  it("실제 API 주소가 있어도 데모 계정은 Mock 상담원 화면을 사용한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.resetModules();
    const { DEMO_REFERENCE_NUMBER, getConsultationCard, loginAgent } = await import("./api");

    const agent = await loginAgent("CS1024", "demo");
    expect((await getConsultationCard(DEMO_REFERENCE_NUMBER, agent.access_token)).reference_number)
      .toBe(DEMO_REFERENCE_NUMBER);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("참조번호를 정규화하고 없는 번호를 거절한다", async () => {
    const { DEMO_REFERENCE_NUMBER, getConsultationCard } = await import("./api");
    expect((await getConsultationCard("  kbsos-7h4q-9m2p ")).reference_number).toBe(DEMO_REFERENCE_NUMBER);
    await expect(getConsultationCard("KBSOS-NOT-FOUND")).rejects.toThrow("찾지 못했습니다");
  });

  it("상담 재확인값의 불일치를 반환한다", async () => {
    const { DEMO_REFERENCE_NUMBER, saveAgentVerification } = await import("./api");
    const result = await saveAgentVerification(DEMO_REFERENCE_NUMBER, {
      action: "BUY",
      symbol_name: "SK하이닉스",
      symbol_code: "000660",
      quantity: 30,
      price: 71_000,
      order_type: "LIMIT",
      submission_status: "UNKNOWN",
      order_history_checked: true,
    });
    expect(result.issues.map(({ field }) => field)).toEqual([
      "action",
      "symbol_name",
      "symbol_code",
      "quantity",
      "price",
      "submission_status",
    ]);
  });

  it("주문 방식과 제출 여부도 불일치 범위에 포함한다", async () => {
    const { DEMO_REFERENCE_NUMBER, getConsultationCard, saveAgentVerification } = await import("./api");
    const current = await getConsultationCard(DEMO_REFERENCE_NUMBER);
    await saveAgentVerification(DEMO_REFERENCE_NUMBER, {
      action: current.consultation.action,
      symbol_name: current.consultation.symbol_name,
      symbol_code: current.consultation.symbol_code,
      quantity: current.consultation.quantity,
      price: current.consultation.price,
      order_type: "LIMIT",
      submission_status: "SUBMITTED",
      order_history_checked: true,
    });
    const result = await saveAgentVerification(DEMO_REFERENCE_NUMBER, {
      action: current.consultation.action,
      symbol_name: current.consultation.symbol_name,
      symbol_code: current.consultation.symbol_code,
      quantity: current.consultation.quantity,
      price: current.consultation.price,
      order_type: "MARKET",
      submission_status: "NOT_SUBMITTED",
      order_history_checked: true,
    });
    expect(result.issues.map(({ field }) => field)).toEqual(["order_type", "submission_status"]);
  });

  it("참조번호를 URL이 아닌 POST body로 전송한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ reference_number: "KBSOS-TEST-TEST" })));
    vi.stubGlobal("fetch", fetchMock);
    vi.resetModules();
    const { getConsultationCard } = await import("./api");

    await getConsultationCard(" kbsos-test-test ");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/consultation-cards/lookup",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reference_number: "KBSOS-TEST-TEST" }),
        headers: expect.objectContaining({ Authorization: expect.stringMatching(/^Bearer [A-Za-z0-9_-]{43}$/) }),
      }),
    );
  });

  it("상담원 검증도 참조번호와 확인 증빙을 POST body로 전송한다", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ saved_at: "2026-08-15T00:00:00Z", issues: [] })));
    vi.stubGlobal("fetch", fetchMock);
    vi.resetModules();
    const { saveAgentVerification } = await import("./api");

    await saveAgentVerification("KBSOS-TEST-TEST", {
      action: "SELL",
      symbol_name: "삼성전자",
      symbol_code: "005930",
      quantity: 20,
      price: 70_000,
      order_type: "LIMIT",
      submission_status: "UNKNOWN",
      order_history_checked: true,
    }, "agent-token", "request-id");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.example.com/api/consultation-cards/verifications");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      reference_number: "KBSOS-TEST-TEST",
      client_request_id: "request-id",
      order_history_checked: true,
    });
    expect(String(init?.body)).not.toContain("agent_id");
  });

  it("고객이 삭제한 상담 준비카드는 다시 조회되지 않는다", async () => {
    const {
      DEMO_REFERENCE_NUMBER,
      deleteConsultationCard,
      getConsultationCard,
      saveConfirmedReport,
    } = await import("./api");
    const demo = await getConsultationCard(DEMO_REFERENCE_NUMBER);
    const saved = await saveConfirmedReport({
      analysis_id: crypto.randomUUID(),
      analysis_version: 1,
      attachment_id: null,
      masked_text: "주문 화면이 계속 로딩됩니다.",
      technical: demo.technical,
      consultation: demo.consultation,
    });

    await deleteConsultationCard(saved.reference_number);

    await expect(getConsultationCard(saved.reference_number)).rejects.toThrow("찾지 못했습니다");
  });

  it("첨부 이미지를 저장해 상담원 카드에서 조회한다", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock-screenshot");
    const { analyzeReport, getConsultationCard, saveConfirmedReport } = await import("./api");
    const analysis = await analyzeReport(
      "KB 앱에서 주문 버튼을 눌렀는데 화면이 멈추고 계속 로딩됩니다.",
      crypto.randomUUID(),
      new File(["image"], "error.png", { type: "image/png" }),
    );
    if (analysis.status !== "confirmation") throw new Error("Mock 분석이 완료되지 않았습니다.");
    const saved = await saveConfirmedReport({
      analysis_id: analysis.analysis_id,
      analysis_version: analysis.analysis_version,
      attachment_id: analysis.attachment?.id ?? null,
      masked_text: analysis.masked_text,
      technical: analysis.technical,
      consultation: analysis.consultation,
    });

    expect((await getConsultationCard(saved.reference_number)).attachment_url).toBe("blob:mock-screenshot");
  });
});
