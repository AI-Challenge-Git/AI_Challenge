import { type FormEvent, useRef, useState } from "react";
import { DEMO_REFERENCE_NUMBER, getConsultationCard, loginAgent, saveAgentVerification } from "./api";
import type {
  AgentCase,
  AgentSession,
  AgentVerificationInput,
  AgentVerificationResult,
  ConsultationData,
  TechnicalData,
} from "./types";

const ACTION_LABEL: Record<ConsultationData["action"], string> = {
  SELL: "매도",
  BUY: "매수",
  UNKNOWN: "모름",
};

const ORDER_TYPE_LABEL: Record<ConsultationData["order_type"], string> = {
  LIMIT: "지정가",
  MARKET: "시장가",
  UNKNOWN: "모름",
};

const SUBMISSION_LABEL: Record<TechnicalData["submission_status"], string> = {
  SUBMITTED: "제출됨",
  NOT_SUBMITTED: "제출되지 않음",
  UNKNOWN: "확인 불가",
};

const formatTime = (value: string | null) =>
  value ? new Date(value).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" }) : "모름";

const ISSUE_VALUE_LABEL: Record<string, Record<string, string>> = {
  action: { SELL: "매도", BUY: "매수", UNKNOWN: "모름" },
  order_type: { LIMIT: "지정가", MARKET: "시장가", UNKNOWN: "모름" },
  submission_status: { SUBMITTED: "제출됨", NOT_SUBMITTED: "제출되지 않음", UNKNOWN: "확인 불가" },
};
const formatIssueValue = (field: string, value: string) => ISSUE_VALUE_LABEL[field]?.[value] ?? value;

export default function AgentDesk() {
  const [agent, setAgent] = useState<AgentSession | null>(null);
  const [caseFile, setCaseFile] = useState<AgentCase | null>(null);
  const [result, setResult] = useState<AgentVerificationResult | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const verificationRequestId = useRef("");

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLoggingIn(true);
    setError("");
    try {
      setAgent(await loginAgent(String(data.get("agentId")).trim(), String(data.get("password"))));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인하지 못했습니다.");
    } finally {
      setLoggingIn(false);
    }
  };

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const agentToken = agent?.access_token;
    if (!agentToken) return;
    setSearching(true);
    setError("");
    setResult(null);
    try {
      setCaseFile(await getConsultationCard(String(new FormData(event.currentTarget).get("reference")), agentToken));
      verificationRequestId.current = "";
    } catch (reason) {
      setCaseFile(null);
      setError(reason instanceof Error ? reason.message : "상담 준비카드를 조회하지 못했습니다.");
    } finally {
      setSearching(false);
    }
  };

  const verify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const agentToken = agent?.access_token;
    if (!caseFile || !agentToken) return;
    const data = new FormData(event.currentTarget);
    const payload: AgentVerificationInput = {
      action: String(data.get("action")) as ConsultationData["action"],
      symbol_name: String(data.get("symbol_name")).trim() || null,
      symbol_code: String(data.get("symbol_code")).trim() || null,
      quantity: data.get("quantity") ? Number(data.get("quantity")) : null,
      price: data.get("price") ? Number(data.get("price")) : null,
      order_type: String(data.get("order_type")) as ConsultationData["order_type"],
      submission_status: String(data.get("submission_status")) as TechnicalData["submission_status"],
      order_history_checked: true,
    };
    setSaving(true);
    setError("");
    try {
      verificationRequestId.current ||= crypto.randomUUID();
      setResult(await saveAgentVerification(caseFile.reference_number, payload, agentToken, verificationRequestId.current));
      setCaseFile(await getConsultationCard(caseFile.reference_number, agentToken));
      verificationRequestId.current = "";
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "재확인 결과를 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const logout = () => {
    setAgent(null);
    setCaseFile(null);
    setResult(null);
    setError("");
  };

  if (!agent) {
    return (
      <div className="agent-page">
        <section className="dashboard-card agent-login">
          <header><div><span className="section-kicker">AGENT ACCESS</span><h1>상담원 로그인</h1></div></header>
          <form className="agent-form" onSubmit={login}>
            <label>사번<input name="agentId" defaultValue="CS1024" autoComplete="username" minLength={4} required /></label>
            <label>비밀번호<input name="password" type="password" defaultValue="demo" autoComplete="current-password" minLength={4} required /></label>
            <button className="primary-button" type="submit" disabled={loggingIn}>{loggingIn ? "로그인 중..." : "로그인"}</button>
            {error ? <p className="error-message" role="alert">{error}</p> : null}
            <p className="agent-note">로그인 API가 발급한 토큰으로만 상담원 기능을 요청합니다.</p>
          </form>
        </section>
      </div>
    );
  }

  return (
    <div className="agent-page">
      <div className="agent-shell">
        <header className="agent-heading">
          <div><span className="section-kicker">COUNSELOR DESK</span><h1>상담원 화면</h1><p>고객의 참조번호로 상담 준비카드와 관련 장애 맥락을 확인합니다.</p></div>
          <div className="agent-profile"><span>{agent.agent_label} 로그인</span><button type="button" onClick={logout}>로그아웃</button></div>
        </header>

        <section className="dashboard-card reference-search">
          <form onSubmit={search}>
            <label htmlFor="reference">상담 참조번호</label>
            <div><input id="reference" name="reference" defaultValue={DEMO_REFERENCE_NUMBER} required maxLength={32} /><button className="primary-button" type="submit" disabled={searching}>{searching ? "검색 중..." : "조회"}</button></div>
          </form>
          {error ? <p className="error-message" role="alert">{error}</p> : null}
        </section>

        {caseFile ? (
          <>
            <section className="agent-case-grid">
              <article className="dashboard-card case-card">
                <header><div><span className="section-kicker">CONSULTATION CARD</span><h2>상담 준비카드</h2></div><span className="signal-status signal-resolved">고객 확인 완료</span></header>
                <div className="reference-line"><span>참조번호</span><strong>{caseFile.reference_number}</strong><small>{formatTime(caseFile.expires_at)}까지 유효</small></div>
                <dl className="case-data">
                  <div><dt>발생 일시</dt><dd>{caseFile.technical.occurred_date ? `${caseFile.technical.occurred_date} ${caseFile.technical.occurred_at ?? ""}` : caseFile.technical.occurred_at ?? "모름"}</dd></div>
                  <div><dt>이용 채널</dt><dd>{caseFile.technical.channel}</dd></div>
                  <div><dt>주문 구분</dt><dd>{ACTION_LABEL[caseFile.consultation.action]}</dd></div>
                  <div><dt>종목</dt><dd>{caseFile.consultation.symbol_name ?? "모름"} {caseFile.consultation.symbol_code ? `(${caseFile.consultation.symbol_code})` : ""}</dd></div>
                  <div><dt>수량</dt><dd>{caseFile.consultation.quantity === null ? "모름" : `${caseFile.consultation.quantity}주`}</dd></div>
                  <div><dt>희망 가격</dt><dd>{caseFile.consultation.price === null ? "모름" : `${caseFile.consultation.price.toLocaleString()}원`}</dd></div>
                  <div><dt>주문 방식</dt><dd>{ORDER_TYPE_LABEL[caseFile.consultation.order_type]}</dd></div>
                  <div><dt>제출 여부</dt><dd>{SUBMISSION_LABEL[caseFile.technical.submission_status]}</dd></div>
                </dl>
                {caseFile.attachment_url ? (
                  <a className="agent-attachment" href={caseFile.attachment_url} target="_blank" rel="noreferrer">
                    <img src={caseFile.attachment_url} alt="고객이 첨부한 MTS 오류 화면" />
                    <span>첨부 오류 화면 크게 보기</span>
                  </a>
                ) : null}
              </article>

              <aside className="dashboard-card incident-card">
                <header><div><span className="section-kicker">INCIDENT CONTEXT</span><h2>관련 장애 맥락</h2></div>{caseFile.related_signal ? <span className="signal-status signal-urgent">장애 의심</span> : null}</header>
                {caseFile.related_signal ? (
                  <>
                    <div className="incident-title"><strong>{caseFile.related_signal.title}</strong><span>유사도 {caseFile.similarity === null ? "-" : `${Math.round(caseFile.similarity * 100)}%`}</span></div>
                    <dl><div><dt>유사 제보</dt><dd>{caseFile.related_signal.report_count}건</dd></div><div><dt>최초 감지</dt><dd>{formatTime(caseFile.related_signal.first_seen)}</dd></div></dl>
                    <p>{caseFile.related_signal.symptom}</p>
                    <div className="incident-guide"><strong>상담 안내</strong><span>{caseFile.related_signal.action}</span></div>
                    <div className="incident-guide"><strong>공식 공지</strong><span>{caseFile.related_signal.official_notice_url ? <a href={caseFile.related_signal.official_notice_url} target="_blank" rel="noreferrer">연결된 공식 공지 보기</a> : "공식 공지 연결 전"}</span></div>
                  </>
                ) : <p className="empty-copy">연결된 장애 의심 신호가 없습니다.</p>}
              </aside>
            </section>

            <section className="dashboard-card mismatch-card">
              <header><div><span className="section-kicker">MISMATCH CHECK</span><h2>상담 재확인·불일치</h2></div><span className={`signal-status ${result ? "signal-resolved" : "signal-watch"}`}>{result ? "저장 완료" : "확인 필요"}</span></header>
              <form key={caseFile.reference_number} className="verification-form" onSubmit={verify} onChange={() => { verificationRequestId.current = ""; setResult(null); }}>
                <div className="verification-grid">
                  <label>주문 구분<select name="action" defaultValue={caseFile.consultation.action}><option value="SELL">매도</option><option value="BUY">매수</option><option value="UNKNOWN">모름</option></select></label>
                  <label>상담 확인 종목<input name="symbol_name" defaultValue={caseFile.consultation.symbol_name ?? ""} placeholder="모름" /></label>
                  <label>종목코드<input name="symbol_code" inputMode="numeric" maxLength={6} defaultValue={caseFile.consultation.symbol_code ?? ""} placeholder="모름" /></label>
                  <label>상담 확인 수량<input name="quantity" type="number" min="1" defaultValue={caseFile.consultation.quantity ?? ""} placeholder="모름" /></label>
                  <label>상담 확인 가격<input name="price" type="number" min="1" step="100" defaultValue={caseFile.consultation.price ?? ""} placeholder="모름" /></label>
                  <label>주문 방식<select name="order_type" defaultValue={caseFile.consultation.order_type}><option value="LIMIT">지정가</option><option value="MARKET">시장가</option><option value="UNKNOWN">모름</option></select></label>
                  <label>주문 제출 여부<select name="submission_status" defaultValue={caseFile.technical.submission_status}><option value="SUBMITTED">제출됨</option><option value="NOT_SUBMITTED">제출되지 않음</option><option value="UNKNOWN">확인 불가</option></select></label>
                </div>
                <label className="verification-check"><input type="checkbox" required /> 기존 주문 제출·체결 내역을 확인했습니다.</label>
                <button className="primary-button" type="submit" disabled={saving}>{saving ? "저장 중..." : "재확인 결과 저장"}</button>
              </form>
              {result ? (
                <div className="verification-result" aria-live="polite">
                  <strong>{result.issues.length ? `${result.issues.length}개 확인 필요` : "불일치 없음"}</strong>
                  {result.issues.length ? <ul>{result.issues.map((issue) => <li key={issue.field}><span className={`issue-${issue.level.toLowerCase().replace("_", "-")}`}>{issue.level === "IMPORTANT" ? "중요" : "확인 필요"}</span><strong>{issue.label}</strong><small>{formatIssueValue(issue.field, issue.customer_value)} → {formatIssueValue(issue.field, issue.agent_value)}</small></li>)}</ul> : <p>고객 확인값과 상담 재확인값이 일치합니다.</p>}
                </div>
              ) : null}
              <p className="mismatch-note">상담 준비카드는 주문 접수증이 아닙니다. 실제 주문은 공식 고객센터 또는 영업점에서 본인확인과 주문내용 재확인을 거쳐야 합니다.</p>
            </section>
          </>
        ) : null}
        <p className="service-disclaimer">현재 표시되는 내용은 고객 제보를 바탕으로 탐지된 장애 의심 신호이며, 공식 확인한 장애가 아닐 수 있습니다. 상담 준비정보는 주문 접수·체결 증빙이 아니므로 공식 채널에서 주문 상태를 확인해 주세요.</p>
      </div>
    </div>
  );
}
