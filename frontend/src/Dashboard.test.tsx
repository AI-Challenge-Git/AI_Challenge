import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import Dashboard, { DashboardData, dashboardRetryDelay, operatorVisibilityLabel } from "./Dashboard";
import type { OperatorSignalListItem, SignalDashboard } from "./types";

const snapshot: SignalDashboard = {
  updated_at: "2026-08-30T00:06:00Z",
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
  hourly_volume: [{
    bucket_start: "2026-08-30T00:00:00Z",
    raw_report_count: 5,
    reporting_unique_sessions: 3,
  }],
  applied_policy: null,
  baseline_status: "INSUFFICIENT_HISTORY",
  baseline_ratio: null,
  limit: 50,
  offset: 0,
};

const operatorSignal: OperatorSignalListItem = {
  signal_id: "11111111-1111-4111-8111-111111111111",
  status: "SIGNAL_DETECTED",
  closure_reason: null,
  channel: "M-able",
  feature_area: "DOMESTIC_STOCK_ORDER",
  reported_symptom_type: "ORDER_SUBMISSION_FAILURE",
  representative_symptom_text: null,
  reporting_unique_sessions: 3,
  raw_report_count: 5,
  review_priority: false,
  first_report_at: "2026-08-30T00:00:00Z",
  last_report_at: "2026-08-30T00:05:00Z",
  window_expires_at: "2026-08-30T00:15:00Z",
  public_visible: true,
  policy_version: "signal-policy.v1",
  policy_status: "EXPERIMENTAL",
  official_notice_url: null,
  closed_at: null,
};

describe("운영 상황판", () => {
  it("초기 기준선과 제보 세션 수를 확정 장애·피해 고객 수로 과장하지 않는다", () => {
    const html = renderToStaticMarkup(<DashboardData snapshot={snapshot} />);

    expect(html).toContain("비교 이력 축적 중");
    expect(html).toContain("초기 운영의 정상 상태");
    expect(html).toContain("제보 세션(중복 제거)");
    expect(html).toContain("시간대별 제보량");
    expect(html).toContain("활성 정책 없음");
    expect(html).toContain("공식 장애로 확인된 상태가 아닙니다");
    expect(html).not.toContain("피해 고객");
    expect(html).not.toContain("확정 장애");
  });

  it("429 응답은 Retry-After 동안 폴링을 미룬다", () => {
    expect(dashboardRetryDelay(new ApiError("요청이 너무 많습니다.", "RATE_LIMITED", 429, 37))).toBe(37_000);
  });

  it("기준선 상태에 따라 직전 시간대 또는 증가 배율을 표시한다", () => {
    const zeroBaseline = renderToStaticMarkup(<DashboardData snapshot={{ ...snapshot, baseline_status: "ZERO_BASELINE" }} />);
    const available = renderToStaticMarkup(<DashboardData snapshot={{ ...snapshot, baseline_status: "AVAILABLE", baseline_ratio: 2 }} />);

    expect(zeroBaseline).toContain("직전 시간대 제보 없음");
    expect(available).toContain("2배");
    expect(available).toContain("직전 시간대 대비 증가 배율");
  });

  it("활성 실험 정책의 군집 방식을 승인 완료로 과장하지 않는다", () => {
    const html = renderToStaticMarkup(<DashboardData snapshot={{
      ...snapshot,
      applied_policy: {
        policy_version: "signal-policy.v2",
        status: "EXPERIMENTAL",
        window_seconds: 600,
        min_unique_sessions: 3,
        review_priority_threshold: 5,
        similarity_threshold: 0.8,
        linkage_method: "AVERAGE",
        representative_method: "MEDOID",
        structured_rules_version: "rules.v1",
        taxonomy_version: "taxonomy.v1",
        baseline_policy_version: null,
      },
    }} />);

    expect(html).toContain("실험 정책");
    expect(html).toContain("AVERAGE · MEDOID");
    expect(html).not.toContain("승인 완료");
  });

  it("첫 조회 중에는 로딩 상태를 표시한다", () => {
    const html = renderToStaticMarkup(<Dashboard />);
    expect(html).toContain("상황판을 불러오는 중");
  });

  it("운영자 신호의 공개 남은 시간과 숨김 상태를 종료와 구분한다", () => {
    expect(operatorVisibilityLabel(operatorSignal, Date.parse("2026-08-30T00:05:00Z"))).toBe("10분 후 공개 만료");
    expect(operatorVisibilityLabel({ ...operatorSignal, public_visible: false })).toBe("공개 상황판 숨김 · 종료 아님");
    expect(operatorVisibilityLabel({ ...operatorSignal, status: "UNDER_REVIEW" })).toBe("검토 중 · 공개 유지");
  });
});
