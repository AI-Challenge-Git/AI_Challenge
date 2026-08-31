import { useEffect, useRef, useState } from "react";
import { ApiError, getSignalDashboard } from "./api";
import type { SignalDashboard, SignalDashboardItem } from "./types";

const STATUS_LABEL: Record<SignalDashboardItem["status"], string> = {
  SIGNAL_DETECTED: "장애 의심 신호",
  UNDER_REVIEW: "운영 검토 중",
};

const ISSUE_LABEL: Record<SignalDashboardItem["reported_symptom_type"], string> = {
  ORDER_SUBMISSION_FAILURE: "주문 제출 실패",
  LOGIN_ACCESS_FAILURE: "로그인·접속 실패",
  BALANCE_INQUIRY_ERROR: "잔고 조회 오류",
  ORDER_RESULT_UNCONFIRMED: "주문 결과 미확인",
  DEVICE_NETWORK_SUSPECTED: "기기·네트워크 의심",
  UNRELATED_OR_AMBIGUOUS: "분류 확인 필요",
  UNKNOWN: "증상 미확인",
};

const formatTime = (value: string) => new Date(value).toLocaleString("ko-KR", {
  dateStyle: "short",
  timeStyle: "short",
});

export const dashboardRetryDelay = (reason: unknown) =>
  reason instanceof ApiError && reason.status === 429
    ? Math.max(1, reason.retryAfterSeconds ?? 5) * 1000
    : 5000;

export function DashboardData({ snapshot }: { snapshot: SignalDashboard }) {
  const visibleItems = snapshot.items.filter(({ status }) => status === "SIGNAL_DETECTED" || status === "UNDER_REVIEW");
  const hourlyVolume = snapshot.hourly_volume.slice(-12);
  const maxHourlyReports = Math.max(1, ...hourlyVolume.map(({ raw_report_count }) => raw_report_count));

  return (
    <>
      <section className="dashboard-card signal-summary">
        <div><span>활성 장애 의심 신호</span><strong>{visibleItems.length}개</strong></div>
        <div><span>평소 대비 기준선</span><strong>비교 이력 축적 중</strong><small>초기 운영의 정상 상태입니다.</small></div>
        <div>
          <span>적용 정책</span>
          <strong>{snapshot.applied_policy ? `${snapshot.applied_policy.policy_version} · ${snapshot.applied_policy.status === "EXPERIMENTAL" ? "실험 정책" : snapshot.applied_policy.status === "RETIRED" ? "종료 정책" : "적용 중"}` : "활성 정책 없음"}</strong>
          {snapshot.applied_policy ? <small>{snapshot.applied_policy.linkage_method} · {snapshot.applied_policy.representative_method}</small> : null}
        </div>
      </section>

      <section className="dashboard-card dashboard-volume" aria-label="시간대별 제보량">
        <header><div><span className="section-kicker">REPORT VOLUME</span><h2>시간대별 제보량</h2></div></header>
        {hourlyVolume.length ? (
          <div className="volume-bars">
            {hourlyVolume.map((bucket) => (
              <div className="volume-bar" key={bucket.bucket_start} aria-label={`${formatTime(bucket.bucket_start)} 제보 ${bucket.raw_report_count}건, 비식별 제보 세션 ${bucket.reporting_unique_sessions}개`}>
                <strong>{bucket.raw_report_count}</strong>
                <span style={{ height: `${Math.max(4, bucket.raw_report_count / maxHourlyReports * 100)}%` }} />
                <small>{new Date(bucket.bucket_start).toLocaleTimeString("ko-KR", { hour: "2-digit" })}</small>
              </div>
            ))}
          </div>
        ) : <p className="empty-copy">아직 집계된 제보가 없습니다.</p>}
      </section>

      {visibleItems.length ? (
        <section className="dashboard-signals" aria-label="활성 장애 의심 신호">
          {visibleItems.map((signal) => (
            <article className="dashboard-card dashboard-signal" key={signal.signal_id}>
              <header>
                <div><span className="section-kicker">INCIDENT SIGNAL</span><h2>{ISSUE_LABEL[signal.reported_symptom_type]}</h2></div>
                <span className={`signal-status ${signal.status === "UNDER_REVIEW" ? "signal-urgent" : "signal-watch"}`}>{STATUS_LABEL[signal.status]}</span>
              </header>
              <dl>
                <div><dt>제보 세션(중복 제거)</dt><dd>{signal.reporting_unique_sessions}개</dd></div>
                <div><dt>전체 제보</dt><dd>{signal.raw_report_count}건</dd></div>
                <div><dt>검토 우선순위</dt><dd>{signal.review_priority ? "우선 검토" : "일반"}</dd></div>
                <div><dt>채널</dt><dd>{signal.channel}</dd></div>
                <div><dt>기능 영역</dt><dd>{signal.feature_area}</dd></div>
                <div><dt>최초 제보</dt><dd>{formatTime(signal.first_report_at)}</dd></div>
                <div><dt>최근 제보</dt><dd>{formatTime(signal.last_report_at)}</dd></div>
                <div><dt>영향 기능</dt><dd>{signal.affected_features.join(", ") || "확인 중"}</dd></div>
                <div><dt>정책</dt><dd>{signal.policy_version} · {signal.policy_status}</dd></div>
              </dl>
              <p className="signal-official-state">
                {signal.official_notice_url ? <a href={signal.official_notice_url} target="_blank" rel="noreferrer">관련 공식 공지 보기</a> : "연결된 공식 공지가 없습니다."}
                <span>공식 장애로 확인된 상태가 아닙니다.</span>
              </p>
            </article>
          ))}
        </section>
      ) : <section className="dashboard-card state-card">현재 활성 장애 의심 신호가 없습니다.</section>}
    </>
  );
}

export default function Dashboard() {
  const [snapshot, setSnapshot] = useState<SignalDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const nextAllowedAt = useRef(0);

  const load = async (initial = false): Promise<number> => {
    const remainingDelay = nextAllowedAt.current - Date.now();
    if (remainingDelay > 0) return remainingDelay;
    if (initial) setLoading(true);
    try {
      setSnapshot(await getSignalDashboard());
      nextAllowedAt.current = 0;
      setError("");
      return 5000;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "운영 상황판을 불러오지 못했습니다.");
      const retryDelay = dashboardRetryDelay(reason);
      if (reason instanceof ApiError && reason.status === 429) nextAllowedAt.current = Date.now() + retryDelay;
      return retryDelay;
    } finally {
      if (initial) setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    let timer = 0;
    const poll = async (initial = false) => {
      const delay = await load(initial);
      if (active) timer = window.setTimeout(() => void poll(), delay);
    };
    void poll(true);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, []);

  return (
    <div className="dashboard">
      <header className="dashboard-heading">
        <div>
          <span className="section-kicker">OPERATIONS</span>
          <h1>운영 상황판</h1>
          <p>고객 제보에서 탐지된 활성 장애 의심 신호를 확인합니다.</p>
        </div>
        <span className="updated-at">{snapshot ? `${formatTime(snapshot.updated_at)} 서버 갱신 · 5초 조회` : "연결 중"}</span>
      </header>
      {error ? <p className="dashboard-warning" role="alert">{error} <button type="button" onClick={() => void load(true)}>다시 시도</button></p> : null}
      {loading && !snapshot ? <section className="dashboard-card state-card">상황판을 불러오는 중입니다.</section> : null}
      {snapshot ? <DashboardData snapshot={snapshot} /> : null}
    </div>
  );
}
