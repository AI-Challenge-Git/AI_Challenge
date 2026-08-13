import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { getDashboardSnapshot } from "./api";
import Dashboard from "./Dashboard";

describe("운영 상황판", () => {
  it("제보량과 신호 상세 데이터를 제공한다", async () => {
    const snapshot = await getDashboardSnapshot();
    expect(snapshot.volume.length).toBeGreaterThan(0);
    expect(snapshot.signals[0]).toMatchObject({ title: "주문 제출 후 무한 로딩" });
    expect(snapshot.policy.source_url).toMatch(/^https:\/\/www\.kbsec\.com\//);
  });

  it("초기 로딩 상태를 표시한다", () => {
    const html = renderToStaticMarkup(<Dashboard />);
    expect(html).toContain("운영 상황판");
    expect(html).toContain("상황판을 불러오는 중");
  });
});
