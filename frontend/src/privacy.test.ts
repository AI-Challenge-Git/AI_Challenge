import { describe, expect, it } from "vitest";
import { maskSensitiveText } from "./privacy";

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
    expect(() => maskSensitiveText("비밀번호는 secret1234입니다.")).toThrow("입력할 수 없습니다");
    expect(() => maskSensitiveText("OTP는 123456입니다.")).toThrow("입력할 수 없습니다");
  });

  it("날짜·가격·시각·주문번호를 개인정보로 오인하지 않는다", () => {
    const result = maskSensitiveText("2026-08-17 09:03에 주문번호 123456789012로 삼성전자 20주를 70,000원에 매도했어요.");
    expect(result.text).toContain("2026-08-17");
    expect(result.text).toContain("09:03");
    expect(result.text).toContain("123456789012");
    expect(result.text).toContain("70,000원");
  });
});
