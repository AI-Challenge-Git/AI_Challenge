import { useEffect, useState } from "react";
import { getDashboardSnapshot } from "./api";
import type { DashboardSnapshot, SignalStatus } from "./types";

const STATUS: Record<SignalStatus, { label: string; tone: string }> = {
  CANDIDATE: { label: "후보", tone: "watch" },
  SIGNAL_DETECTED: { label: "신호 탐지", tone: "watch" },
  REVIEW_REQUIRED: { label: "운영자 검토 필요", tone: "urgent" },
  OFFICIAL_NOTICE_LINKED: { label: "공식 공지 연결", tone: "linked" },
  RESOLVED: { label: "해소", tone: "resolved" },
};

const FEATURE_LABEL: Record<string, string> = { DOMESTIC_STOCK_ORDER: "국내주식 주문" };
const formatTime = (value: string) => new Date(value).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });

export default function Dashboard() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async (initial = false) => {
    if (initial) setLoading(true);
    try {
      setSnapshot(await getDashboardSnapshot());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "상황판을 불러오지 못했습니다.");
    } finally {
      if (initial) setLoading(false);
    }
  };

  useEffect(() => {
    void load(true);
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const signals = snapshot?.signals ?? [];
  const selected = signals.find((signal) => signal.id === selectedId) ?? signals[0];
  const peak = Math.max(1, ...(snapshot?.volume.map(({ count }) => count) ?? []));

  return (
    <div className="dashboard">
      <header className="dashboard-heading">
        <div>
          <span className="section-kicker">OPERATIONS</span>
          <h1>운영 상황판</h1>
          <p>개인 주문정보를 제외하고 서버 시각 기준 최근 10분의 기술 증상을 확인합니다.</p>
        </div>
        <span className="updated-at">
          {snapshot ? `${formatTime(snapshot.updated_at)} 갱신 · 5초 주기` : "연결 중"}
        </span>
      </header>

      {error ? <p className="dashboard-warning" role="alert">{error} <button type="button" onClick={() => void load(true)}>다시 시도</button></p> : null}
      {loading ? <section className="dashboard-card state-card">상황판을 불러오는 중입니다.</section> : null}

      {snapshot ? (
        <>
          <section className="dashboard-card volume-card" aria-labelledby="volume-title">
            <header>
              <div><span className="section-kicker">REPORT VOLUME</span><h2 id="volume-title">제보량 추이</h2></div>
              <span className="signal-status signal-urgent">평소 대비 {snapshot.baseline_ratio.toFixed(1)}배</span>
            </header>
            <ol className="volume-chart" aria-label="30분 단위 제보량">
              {snapshot.volume.map(({ time, count }) => (
                <li key={time}>
                  <span className="volume-track"><span className="volume-bar" style={{ height: `${Math.round((count / peak) * 100)}%` }}><strong>{count}</strong></span></span>
                  <time>{time}</time>
                </li>
              ))}
            </ol>
          </section>

          {selected ? (
            <section className="signal-workspace">
              <div className="dashboard-card signal-list">
                <header><div><span className="section-kicker">COMMON SYMPTOMS</span><h2>공통 증상 신호</h2></div><strong>{signals.length}개</strong></header>
                {signals.map((signal) => {
                  const status = STATUS[signal.status];
                  return (
                    <button key={signal.id} type="button" className={selected.id === signal.id ? "selected" : ""} aria-pressed={selected.id === signal.id} onClick={() => setSelectedId(signal.id)}>
                      <span><strong>{signal.title}</strong><small>{signal.change} · {signal.report_count}건</small></span>
                      <span className={`signal-status signal-${status.tone}`}>{status.label}</span>
                    </button>
                  );
                })}
              </div>

              <article className="dashboard-card signal-detail" aria-live="polite">
                <header>
                  <div><span className="section-kicker">SIGNAL DETAIL</span><h2>{selected.title}</h2></div>
                  <span className={`signal-status signal-${STATUS[selected.status].tone}`}>{STATUS[selected.status].label}</span>
                </header>
                <dl>
                  <div><dt>유효 제보</dt><dd>{selected.report_count}건</dd></div>
                  <div><dt>중복 제거 전</dt><dd>{selected.raw_report_count}건</dd></div>
                  <div><dt>최초 감지</dt><dd>{formatTime(selected.first_seen)}</dd></div>
                  <div><dt>최근 제보</dt><dd>{formatTime(selected.last_seen)}</dd></div>
                  <div><dt>영향 채널</dt><dd>{selected.channel}</dd></div>
                  <div><dt>기능 영역</dt><dd>{FEATURE_LABEL[selected.feature_area] ?? selected.feature_area}</dd></div>
                </dl>
                <div className="signal-copy"><span>공통 증상</span><p>{selected.symptom}</p></div>
                <div className="signal-copy"><span>비식별 대표 제보</span><p>“{selected.representative_report}”</p></div>
                <div className="signal-copy action"><span>운영 안내</span><p>{selected.action}</p></div>
                <div className="signal-copy official"><span>공식 공지 상태</span><p>{selected.official_notice_url ? <a href={selected.official_notice_url} target="_blank" rel="noreferrer">연결된 공식 공지 보기</a> : "공식 공지 연결 전"}</p></div>
              </article>
            </section>
          ) : <section className="dashboard-card state-card">현재 활성 장애 의심 신호가 없습니다.</section>}

          <section className="dashboard-card policy-source">
            <div><span className="section-kicker">POLICY SOURCE</span><strong>{snapshot.policy.title}</strong><small>{snapshot.policy.version} · {snapshot.policy.checked_at} 확인</small></div>
            {snapshot.policy.source_url ? <a href={snapshot.policy.source_url} target="_blank" rel="noreferrer">공식 문서 보기</a> : <span>출처 URL 연결 대기</span>}
          </section>
          <p className="service-disclaimer">현재 표시되는 내용은 고객 제보를 바탕으로 탐지된 장애 의심 신호이며, 공식 확인한 장애가 아닐 수 있습니다. 상담 준비정보는 주문 접수·체결 증빙이 아니므로 공식 채널에서 주문 상태를 확인해 주세요.</p>
        </>
      ) : null}
    </div>
  );
}
