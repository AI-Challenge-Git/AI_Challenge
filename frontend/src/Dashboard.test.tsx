import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Dashboard from "./Dashboard";

describe("운영 상황판", () => {
  it("가짜 신호 대신 API 연동 대기 상태를 표시한다", () => {
    const html = renderToStaticMarkup(<Dashboard />);
    expect(html).toContain("운영 상황판");
    expect(html).toContain("연동 대기");
    expect(html).toContain("표시할 운영 데이터가 없습니다");
    expect(html).not.toContain("주문 제출 후 무한 로딩");
  });
});
