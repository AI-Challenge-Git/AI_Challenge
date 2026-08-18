import { describe, expect, it } from "vitest";
import { analyzeReport } from "../api";
import { analyzeLocally, maskSensitiveText } from "./analyzeReport";

describe("민감정보 마스킹", () => {
  it("전화번호·계좌번호·이메일을 원문 대신 라벨로 치환한다", () => {
    const result = maskSensitiveText(
      "연락처는 010-1234-5678, 계좌는 123-456-789012, 이메일은 test@example.com입니다.",
    );

    expect(result.text).not.toContain("010-1234-5678");
    expect(result.text).not.toContain("123-456-789012");
    expect(result.text).not.toContain("test@example.com");
    expect(result.text).toContain("[전화번호]");
    expect(result.text).toContain("[계좌번호]");
    expect(result.text).toContain("[이메일]");
    expect(result.text).not.toContain("마스킹]");
    expect(result.detected).toEqual(expect.arrayContaining(["전화번호", "계좌번호", "이메일"]));
  });

  it("흔한 구분자와 붙여 쓴 민감정보도 마스킹한다", () => {
    const result = maskSensitiveText(
      "연락처는 (010) 1234/5678, 카드는 1234567812345678, 이메일은 test @ example . com, 계좌번호 123456789012입니다.",
    );

    expect(result.text).toBe("연락처는 [전화번호], 카드는 [카드번호], 이메일은 [이메일], [계좌번호]입니다.");
  });

  it("주민번호·비밀번호·OTP가 포함된 요청을 거부한다", () => {
    expect(() => maskSensitiveText("주민번호는 900101-1234567입니다.")).toThrow("입력할 수 없습니다");
    expect(() => maskSensitiveText("주민번호는 9001011234567입니다.")).toThrow("입력할 수 없습니다");
    expect(() => maskSensitiveText("비밀번호는 secret1234입니다.")).toThrow("입력할 수 없습니다");
    expect(() => maskSensitiveText("OTP는 123456입니다.")).toThrow("입력할 수 없습니다");
  });

  it("날짜·가격·시각·주문번호를 개인정보로 오인해 마스킹하지 않는다", () => {
    const result = maskSensitiveText("2026-08-17 09:03에 주문번호 123456789012로 삼성전자 20주를 70,000원에 매도했어요.");
    expect(result.text).toContain("2026-08-17");
    expect(result.text).toContain("09:03");
    expect(result.text).toContain("123456789012");
    expect(result.text).toContain("70,000원");
  });

  it("API Mock 경로에서도 감지 항목을 유지한다", async () => {
    const result = await analyzeReport("연락처는 010-1234-5678이고 주문 화면이 계속 로딩됐어요.");
    if (result.status !== "confirmation") throw new Error("Mock 분석이 완료되지 않았습니다.");
    expect(result.masked_items).toContain("전화번호");
  });
});

describe("단일 제보 Mock 구조화", () => {
  it("기술 증상과 개별 주문정보를 분리한다", () => {
    const result = analyzeLocally(
      "9시 3분쯤 KB 앱에서 삼전 스무 주를 7만 원에 팔려고 했는데 주문 버튼을 누른 뒤 계속 로딩됐고 주문번호는 확인하지 못했어요.",
    );

    expect(result.technical).toMatchObject({
      occurred_at: "09:03",
      channel: "M-able",
      feature_area: "DOMESTIC_STOCK_ORDER",
      issue_type: "ORDER_SUBMISSION_FAILURE",
      submission_status: "UNKNOWN",
    });
    expect(result.consultation).toMatchObject({
      action: "SELL",
      symbol_name: "삼성전자",
      symbol_code: "005930",
      quantity: 20,
      price: 70000,
    });
    expect(result.consultation.order_type).toBe("UNKNOWN");
    expect(result.consultation.field_statuses.order_type).toBe("NEEDS_CONFIRMATION");
  });

  it("원문에 없는 수치와 시각을 생성하지 않는다", () => {
    const result = analyzeLocally("KB 앱에서 매도 주문을 눌렀는데 화면이 넘어가지 않고 계속 멈춰 있어요.");
    expect(result.consultation.quantity).toBeNull();
    expect(result.consultation.price).toBeNull();
    expect(result.consultation.attempted_at).toBeNull();
    expect(result.consultation.field_statuses.quantity).toBe("UNKNOWN");
  });
});
