import { useEffect, useState } from "react";
import { getSignalDashboard } from "./api";
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

export function DashboardData({ snapshot }: { snapshot: SignalDashboard }) {
  return (
    <>
      <section className="dashboard-card signal-summary">
        <div><span>활성 장애 의심 신호</span><strong>{snapshot.items.length}개</strong></div>
        <div><span>평소 대비 기준선</span><strong>비교 이력 축적 중</strong><small>초기 운영의 정상 상태입니다.</small></div>
      </section>

      {snapshot.items.length ? (
        <section className="dashboard-signals" aria-label="활성 장애 의심 신호">
          {snapshot.items.map((signal) => (
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
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);

  const load = async (initial = false) => {
    if (initial) setLoading(true);
    try {
      setSnapshot(await getSignalDashboard());
      setFetchedAt(new Date());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "운영 상황판을 불러오지 못했습니다.");
    } finally {
      if (initial) setLoading(false);
    }
  };

  useEffect(() => {
    void load(true);
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="dashboard">
      <header className="dashboard-heading">
        <div>
          <span className="section-kicker">OPERATIONS</span>
          <h1>운영 상황판</h1>
          <p>고객 제보에서 탐지된 활성 장애 의심 신호를 확인합니다.</p>
        </div>
        <span className="updated-at">{fetchedAt ? `${fetchedAt.toLocaleTimeString("ko-KR")} 조회 · 5초 주기` : "연결 중"}</span>
      </header>
      {error ? <p className="dashboard-warning" role="alert">{error} <button type="button" onClick={() => void load(true)}>다시 시도</button></p> : null}
      {loading && !snapshot ? <section className="dashboard-card state-card">상황판을 불러오는 중입니다.</section> : null}
      {snapshot ? <DashboardData snapshot={snapshot} /> : null}
    </div>
  );
}
