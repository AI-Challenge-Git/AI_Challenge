import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("상담 참조번호 조회", () => {
  it("참조번호를 정규화하고 없는 번호를 거절한다", async () => {
    const { DEMO_REFERENCE_NUMBER, getConsultationCard } = await import("./api");
    expect((await getConsultationCard("  kbsos-7h4q-9m2p ")).reference_number).toBe(DEMO_REFERENCE_NUMBER);
    await expect(getConsultationCard("KBSOS-NOT-FOUND")).rejects.toThrow("찾지 못했습니다");
  });

  it("상담 재확인값의 불일치를 반환한다", async () => {
    const { DEMO_REFERENCE_NUMBER, saveAgentVerification } = await import("./api");
    const result = await saveAgentVerification(DEMO_REFERENCE_NUMBER, {
      agent_id: "CS1024",
      symbol_name: "SK하이닉스",
      quantity: 30,
      price: 71_000,
      order_type: "LIMIT",
      submission_status: "UNKNOWN",
    });
    expect(result.issues.map(({ field }) => field)).toEqual([
      "symbol_name",
      "quantity",
      "price",
      "submission_status",
    ]);
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
        credentials: "include",
        body: JSON.stringify({ reference_number: "KBSOS-TEST-TEST" }),
      }),
    );
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
      masked_text: "주문 화면이 계속 로딩됩니다.",
      technical: demo.technical,
      consultation: demo.consultation,
    });

    await deleteConsultationCard(saved.reference_number);

    await expect(getConsultationCard(saved.reference_number)).rejects.toThrow("찾지 못했습니다");
  });
});
