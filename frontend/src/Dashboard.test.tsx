import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Dashboard, { DashboardData } from "./Dashboard";
import type { SignalDashboard } from "./types";

const snapshot: SignalDashboard = {
  items: [{
    signal_id: "11111111-1111-4111-8111-111111111111",
    status: "UNDER_REVIEW",
    channel: "M-able",
    feature_area: "DOMESTIC_STOCK_ORDER",
    reported_symptom_type: "ORDER_SUBMISSION_FAILURE",
    reporting_unique_sessions: 3,
    raw_report_count: 5,
    review_priority: true,
    first_report_at: "2026-08-30T00:00:00Z",
    last_report_at: "2026-08-30T00:05:00Z",
    affected_features: ["DOMESTIC_STOCK_ORDER"],
    policy_version: "signal-policy.v1",
    policy_status: "ACTIVE",
    baseline_status: "INSUFFICIENT_HISTORY",
    baseline_ratio: null,
    official_incident: false,
    official_notice_url: null,
  }],
  baseline_status: "INSUFFICIENT_HISTORY",
  baseline_ratio: null,
  limit: 50,
  offset: 0,
};

describe("운영 상황판", () => {
  it("초기 기준선과 제보 세션 수를 확정 장애·피해 고객 수로 과장하지 않는다", () => {
    const html = renderToStaticMarkup(<DashboardData snapshot={snapshot} />);

    expect(html).toContain("비교 이력 축적 중");
    expect(html).toContain("초기 운영의 정상 상태");
    expect(html).toContain("제보 세션(중복 제거)");
    expect(html).toContain("공식 장애로 확인된 상태가 아닙니다");
    expect(html).not.toContain("피해 고객");
    expect(html).not.toContain("확정 장애");
  });

  it("첫 조회 중에는 로딩 상태를 표시한다", () => {
    const html = renderToStaticMarkup(<Dashboard />);
    expect(html).toContain("상황판을 불러오는 중");
  });
});
