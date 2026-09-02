import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  acknowledgeOperatorSignal,
  ApiError,
  closeOperatorSignal,
  getOperatorSignals,
  getSignalDashboard,
  loginAgent,
} from "./api";
import type {
  AgentSession,
  OperatorSignalClosureReason,
  OperatorSignalListItem,
  SignalDashboard,
  SignalDashboardItem,
} from "./types";

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

const OPERATOR_STATUS_LABEL: Record<OperatorSignalListItem["status"], string> = {
  CANDIDATE: "후보 비교 중",
  SIGNAL_DETECTED: "장애 의심 신호",
  UNDER_REVIEW: "운영 검토 중",
  CLOSED: "종료",
};

const CLOSURE_LABEL: Record<OperatorSignalClosureReason, string> = {
  WINDOW_EXPIRED: "공개 시간창 만료",
  FALSE_POSITIVE: "오탐",
  MERGED: "다른 신호에 병합",
  OFFICIAL_INCIDENT_RESOLVED: "공식 장애 해소",
  EVIDENCE_RECALCULATED: "근거 재계산",
};

const formatTime = (value: string) => new Date(value).toLocaleString("ko-KR", {
  dateStyle: "short",
  timeStyle: "short",
});

export const dashboardRetryDelay = (reason: unknown) =>
  reason instanceof ApiError && reason.status === 429
    ? Math.max(1, reason.retryAfterSeconds ?? 5) * 1000
    : 5000;

export const operatorVisibilityLabel = (signal: OperatorSignalListItem, now = Date.now()) => {
  if (signal.status === "CLOSED") return "종료됨";
  if (!signal.public_visible) return "공개 상황판 숨김 · 종료 아님";
  if (signal.status === "UNDER_REVIEW") return "검토 중 · 공개 유지";
  const remainingMinutes = Math.max(1, Math.ceil((Date.parse(signal.window_expires_at) - now) / 60_000));
  return `${remainingMinutes}분 후 공개 만료`;
};

function OperatorSignals() {
  const [operator, setOperator] = useState<AgentSession | null>(null);
  const [signals, setSignals] = useState<OperatorSignalListItem[]>([]);
  const [closureReasons, setClosureReasons] = useState<Record<string, OperatorSignalClosureReason>>({});
  const [loading, setLoading] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [mutatingId, setMutatingId] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const mutationRequest = useRef<{ key: string; id: string } | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const showError = (reason: unknown, fallback: string) => {
    if (reason instanceof ApiError && reason.status === 401) {
      setOperator(null);
      setSignals([]);
    }
    setError(reason instanceof Error ? reason.message : fallback);
  };

  const load = async (token = operator?.access_token) => {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      setSignals((await getOperatorSignals(token)).items);
    } catch (reason) {
      showError(reason, "운영자 신호 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLoggingIn(true);
    setError("");
    try {
      const session = await loginAgent(String(data.get("operatorId")).trim(), String(data.get("password")));
      if (session.role !== "OPERATOR") throw new ApiError("운영자 계정으로 로그인해 주세요.", "OPERATOR_ROLE_REQUIRED", 403);
      const response = await getOperatorSignals(session.access_token);
      setOperator(session);
      setSignals(response.items);
    } catch (reason) {
      showError(reason, "운영자 로그인을 완료하지 못했습니다.");
    } finally {
      setLoggingIn(false);
    }
  };

  const mutate = async (signal: OperatorSignalListItem, action: "acknowledge" | "close") => {
    const token = operator?.access_token;
    if (!token) return;
    const closureReason = closureReasons[signal.signal_id] ?? "WINDOW_EXPIRED";
    const key = `${action}:${signal.signal_id}:${action === "close" ? closureReason : "MANUAL_REVIEW"}`;
    if (mutationRequest.current?.key !== key) mutationRequest.current = { key, id: crypto.randomUUID() };
    setMutatingId(signal.signal_id);
    setError("");
    try {
      if (action === "acknowledge") {
        await acknowledgeOperatorSignal(signal.signal_id, token, mutationRequest.current.id);
      } else {
        await closeOperatorSignal(signal.signal_id, closureReason, token, mutationRequest.current.id);
      }
      mutationRequest.current = null;
      await load(token);
    } catch (reason) {
      showError(reason, action === "acknowledge" ? "검토를 시작하지 못했습니다." : "신호를 종료하지 못했습니다.");
    } finally {
      setMutatingId("");
    }
  };

  if (!operator) {
    return (
      <section className="dashboard-card operator-panel">
        <header><div><span className="section-kicker">OPERATOR ACCESS</span><h2>운영자 신호 관리</h2></div></header>
        <form className="agent-form" onSubmit={login}>
          <label>운영자 ID<input name="operatorId" autoComplete="username" required /></label>
          <label>비밀번호<input name="password" type="password" autoComplete="current-password" required /></label>
          <button className="primary-button" type="submit" disabled={loggingIn}>{loggingIn ? "로그인 중..." : "운영자 로그인"}</button>
          {error ? <p className="error-message" role="alert">{error}</p> : null}
        </form>
      </section>
    );
  }

  return (
    <section className="operator-management">
      <header className="operator-heading">
        <div><span className="section-kicker">OPERATOR SIGNALS</span><h2>전체 신호 관리</h2><p>공개 시간창이 지난 신호도 삭제·종료로 오인하지 않고 확인합니다.</p></div>
        <div className="agent-profile"><span>{operator.agent_label}</span><button type="button" onClick={() => { setOperator(null); setSignals([]); setError(""); }}>로그아웃</button></div>
      </header>
      <div className="operator-toolbar">
        <span>전체 {signals.length}개</span>
        <button type="button" onClick={() => void load()} disabled={loading}>{loading ? "갱신 중..." : "새로고침"}</button>
      </div>
      {error ? <p className="dashboard-warning" role="alert">{error}</p> : null}
      <div className="operator-signal-list">
        {signals.length ? signals.map((signal) => {
          const closureReason = closureReasons[signal.signal_id] ?? "WINDOW_EXPIRED";
          return (
            <article className={`dashboard-card operator-signal ${signal.public_visible ? "" : "operator-signal-hidden"}`} key={signal.signal_id}>
              <header>
                <div><span className="section-kicker">{signal.signal_id}</span><h3>{ISSUE_LABEL[signal.reported_symptom_type]}</h3></div>
                <span className={`signal-status ${signal.status === "CLOSED" ? "signal-resolved" : signal.status === "UNDER_REVIEW" ? "signal-urgent" : "signal-watch"}`}>{OPERATOR_STATUS_LABEL[signal.status]}</span>
              </header>
              <dl>
                <div><dt>공개 상태</dt><dd>{operatorVisibilityLabel(signal, now)}</dd></div>
                <div><dt>공개 시간창</dt><dd>{formatTime(signal.window_expires_at)}</dd></div>
                <div><dt>비식별 제보 세션</dt><dd>{signal.reporting_unique_sessions}개</dd></div>
                <div><dt>전체 제보</dt><dd>{signal.raw_report_count}건</dd></div>
                <div><dt>최근 제보</dt><dd>{formatTime(signal.last_report_at)}</dd></div>
                <div><dt>종료 사유</dt><dd>{signal.closure_reason ? CLOSURE_LABEL[signal.closure_reason] : "종료되지 않음"}</dd></div>
              </dl>
              {signal.status !== "CLOSED" ? (
                <div className="operator-actions">
                  {signal.status === "SIGNAL_DETECTED" ? <button type="button" disabled={mutatingId === signal.signal_id} onClick={() => void mutate(signal, "acknowledge")}>검토 시작</button> : null}
                  <select aria-label="종료 사유" value={closureReason} onChange={(event) => setClosureReasons((current) => ({ ...current, [signal.signal_id]: event.target.value as OperatorSignalClosureReason }))}>
                    {Object.entries(CLOSURE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                  <button type="button" className="danger-button" disabled={mutatingId === signal.signal_id} onClick={() => void mutate(signal, "close")}>종료</button>
                </div>
              ) : null}
            </article>
          );
        }) : <section className="dashboard-card state-card">조회된 신호가 없습니다.</section>}
      </div>
    </section>
  );
}

export function DashboardData({ snapshot }: { snapshot: SignalDashboard }) {
  const visibleItems = snapshot.items.filter(({ status }) => status === "SIGNAL_DETECTED" || status === "UNDER_REVIEW");
  const hourlyVolume = snapshot.hourly_volume.slice(-12);
  const maxHourlyReports = Math.max(1, ...hourlyVolume.map(({ raw_report_count }) => raw_report_count));
  const baseline = snapshot.baseline_status === "INSUFFICIENT_HISTORY"
    ? { value: "비교 이력 축적 중", detail: "초기 운영의 정상 상태입니다." }
    : snapshot.baseline_status === "ZERO_BASELINE"
      ? { value: "직전 시간대 제보 없음", detail: "비교할 직전 기준선이 없습니다." }
      : { value: `${snapshot.baseline_ratio?.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}배`, detail: "직전 시간대 대비 증가 배율" };

  return (
    <>
      <section className="dashboard-card signal-summary">
        <div><span>활성 장애 의심 신호</span><strong>{visibleItems.length}개</strong></div>
        <div><span>직전 시간대 대비</span><strong>{baseline.value}</strong><small>{baseline.detail}</small></div>
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
      <OperatorSignals />
    </div>
  );
}
