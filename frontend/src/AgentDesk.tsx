import { type FormEvent, useRef, useState } from "react";
import {
  ApiError,
  getConsultationCard,
  getConsultationCards,
  loginAgent,
  normalizeSymbolCode,
  saveAgentVerification,
} from "./api";
import type {
  AgentCardListItem,
  AgentCardSelector,
  AgentCase,
  AgentSession,
  AgentVerificationInput,
  AgentVerificationResult,
} from "./types";

const ACTION_LABEL: Record<AgentCase["consultation"]["action"], string> = {
  SELL: "매도",
  BUY: "매수",
  UNKNOWN: "모름",
};

const ORDER_TYPE_LABEL: Record<AgentCase["consultation"]["order_type"], string> = {
  LIMIT: "지정가",
  MARKET: "시장가",
  UNKNOWN: "모름",
};

const SUBMISSION_LABEL: Record<AgentCase["technical"]["submission_status"], string> = {
  CUSTOMER_REPORTED_SUBMITTED: "고객 진술: 제출됨",
  CUSTOMER_REPORTED_NOT_SUBMITTED: "고객 진술: 제출되지 않음",
  UNKNOWN: "확인 불가",
};

const VERIFICATION_LABEL: Record<NonNullable<AgentCase["verification_status"]>, string> = {
  MATCHED: "일치",
  NEEDS_CONFIRMATION: "확인 필요",
  IMPORTANT: "중요 불일치",
};

const FIELD_LABEL: Record<AgentVerificationResult["fields"][number]["field"], string> = {
  action: "주문 구분",
  symbol_name: "종목명",
  symbol_code: "종목코드",
  quantity: "수량",
  order_type: "주문 방식",
  price_krw: "가격",
  submission_status: "주문 제출 여부",
};

const formatTime = (value: string | null) =>
  value ? new Date(value).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" }) : "모름";

const formatValue = (field: string, value: string | number | null) => {
  if (value === null || value === "UNKNOWN") return "모름";
  if (field === "action") return ACTION_LABEL[value as keyof typeof ACTION_LABEL] ?? String(value);
  if (field === "order_type") return ORDER_TYPE_LABEL[value as keyof typeof ORDER_TYPE_LABEL] ?? String(value);
  if (field === "submission_status") return SUBMISSION_LABEL[value as keyof typeof SUBMISSION_LABEL] ?? String(value);
  if (field === "quantity") return `${value}주`;
  if (field === "price_krw") return `${Number(value).toLocaleString()}원`;
  return String(value);
};

export default function AgentDesk() {
  const [agent, setAgent] = useState<AgentSession | null>(null);
  const [cards, setCards] = useState<AgentCardListItem[]>([]);
  const [caseFile, setCaseFile] = useState<AgentCase | null>(null);
  const [result, setResult] = useState<AgentVerificationResult | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const [loadingCards, setLoadingCards] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const verificationRequestId = useRef("");

  const showAgentError = (reason: unknown, fallback: string) => {
    if (reason instanceof ApiError && reason.status === 401) {
      setAgent(null);
      setCards([]);
      setCaseFile(null);
      setResult(null);
    }
    setError(reason instanceof Error ? reason.message : fallback);
  };

  const loadCards = async (token = agent?.access_token) => {
    if (!token) return;
    setLoadingCards(true);
    setError("");
    try {
      setCards((await getConsultationCards(token)).items);
    } catch (reason) {
      showAgentError(reason, "상담 목록을 불러오지 못했습니다.");
    } finally {
      setLoadingCards(false);
    }
  };

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLoggingIn(true);
    setError("");
    try {
      const session = await loginAgent(String(data.get("agentId")).trim(), String(data.get("password")));
      const list = await getConsultationCards(session.access_token);
      setAgent(session);
      setCards(list.items);
    } catch (reason) {
      showAgentError(reason, "로그인하지 못했습니다.");
    } finally {
      setLoggingIn(false);
    }
  };

  const openCard = async (selector: AgentCardSelector) => {
    const token = agent?.access_token;
    if (!token) return;
    setSearching(true);
    setError("");
    setResult(null);
    try {
      setCaseFile(await getConsultationCard(selector, token));
      verificationRequestId.current = "";
    } catch (reason) {
      setCaseFile(null);
      showAgentError(reason, "상담 준비카드를 조회하지 못했습니다.");
    } finally {
      setSearching(false);
    }
  };

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const referenceNumber = String(new FormData(event.currentTarget).get("reference")).trim();
    await openCard({ reference_number: referenceNumber });
  };

  const verify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const token = agent?.access_token;
    if (!caseFile || !token) return;
    const data = new FormData(event.currentTarget);
    const payload: AgentVerificationInput = {
      action: String(data.get("action")) as AgentVerificationInput["action"],
      symbol_name: String(data.get("symbol_name")).trim() || null,
      symbol_code: normalizeSymbolCode(String(data.get("symbol_code"))) || null,
      quantity: data.get("quantity") ? Number(data.get("quantity")) : null,
      price_krw: data.get("price_krw") ? Number(data.get("price_krw")) : null,
      order_type: String(data.get("order_type")) as AgentVerificationInput["order_type"],
      submission_status: String(data.get("submission_status")) as AgentVerificationInput["submission_status"],
      order_history_checked: true,
    };
    setSaving(true);
    setError("");
    try {
      verificationRequestId.current ||= crypto.randomUUID();
      const selector = { card_id: caseFile.card_id } as const;
      setResult(await saveAgentVerification(selector, payload, token, verificationRequestId.current));
      setCaseFile(await getConsultationCard(selector, token));
      await loadCards(token);
      verificationRequestId.current = "";
    } catch (reason) {
      showAgentError(reason, "재확인 결과를 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const logout = () => {
    setAgent(null);
    setCards([]);
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
            <label>사번<input name="agentId" placeholder="사번 입력" autoComplete="username" minLength={4} required /></label>
            <label>비밀번호<input name="password" type="password" placeholder="비밀번호 입력" autoComplete="current-password" required /></label>
            <button className="primary-button" type="submit" disabled={loggingIn}>{loggingIn ? "로그인 중..." : "로그인"}</button>
            {error ? <p className="error-message" role="alert">{error}</p> : null}
          </form>
        </section>
      </div>
    );
  }

  return (
    <div className="agent-page">
      <div className="agent-shell">
        <header className="agent-heading">
          <div><span className="section-kicker">COUNSELOR DESK</span><h1>상담원 화면</h1><p>상담 목록 또는 고객의 참조번호로 상담 준비카드를 확인합니다.</p></div>
          <div className="agent-profile"><span>{agent.agent_label} 로그인</span><button type="button" onClick={logout}>로그아웃</button></div>
        </header>

        <section className="dashboard-card agent-card-list">
          <header>
            <div><span className="section-kicker">CONSULTATION LIST</span><h2>최근 고객 상담</h2></div>
            <button type="button" onClick={() => void loadCards()} disabled={loadingCards}>{loadingCards ? "갱신 중..." : "새로고침"}</button>
          </header>
          <div className="agent-card-items">
            {cards.length ? cards.map((card) => (
              <button
                key={card.card_id}
                type="button"
                className={card.expired || !card.can_open ? "expired" : ""}
                disabled={searching || !card.can_open}
                onClick={() => void openCard({ card_id: card.card_id })}
              >
                <span>{formatTime(card.received_at)}</span>
                <strong>{card.technical_symptom ?? "기술 증상 미확인"}</strong>
                <small>{card.expired ? "만료 · 조회 불가" : card.verification_status ? `재확인 ${VERIFICATION_LABEL[card.verification_status]}` : "상담 확인 대기"}</small>
              </button>
            )) : <p className="empty-copy">조회할 상담카드가 없습니다.</p>}
          </div>
        </section>

        <section className="dashboard-card reference-search">
          <form onSubmit={search}>
            <label htmlFor="reference">상담 참조번호</label>
            <div><input id="reference" name="reference" placeholder="상담 참조번호 입력" required maxLength={32} /><button className="primary-button" type="submit" disabled={searching}>{searching ? "검색 중..." : "조회"}</button></div>
          </form>
          {error ? <p className="error-message" role="alert">{error}</p> : null}
        </section>

        {caseFile ? (
          <>
            <section className="agent-case-grid">
              <article className="dashboard-card case-card">
                <header><div><span className="section-kicker">CONSULTATION CARD</span><h2>상담 준비카드</h2></div><span className="signal-status signal-resolved">고객 확인 완료</span></header>
                <div className="reference-line"><span>카드 ID</span><strong>{caseFile.card_id}</strong><small>{formatTime(caseFile.expires_at)}까지 유효</small></div>
                <dl className="case-data">
                  <div><dt>발생 일시</dt><dd>{formatTime(caseFile.technical.reported_occurred_at)}</dd></div>
                  <div><dt>오류 유형</dt><dd>{caseFile.technical.issue_type}</dd></div>
                  <div><dt>공통 증상</dt><dd>{caseFile.technical.symptom ?? "모름"}</dd></div>
                  <div><dt>주문 구분</dt><dd>{ACTION_LABEL[caseFile.consultation.action]}</dd></div>
                  <div><dt>종목</dt><dd>{caseFile.consultation.symbol_name ?? "모름"} {caseFile.consultation.symbol_code ? `(${caseFile.consultation.symbol_code})` : ""}</dd></div>
                  <div><dt>수량</dt><dd>{caseFile.consultation.quantity == null ? "모름" : `${caseFile.consultation.quantity}주`}</dd></div>
                  <div><dt>희망 가격</dt><dd>{caseFile.consultation.price_krw == null ? "모름" : `${caseFile.consultation.price_krw.toLocaleString()}원`}</dd></div>
                  <div><dt>주문 방식</dt><dd>{ORDER_TYPE_LABEL[caseFile.consultation.order_type]}</dd></div>
                  <div><dt>제출 여부</dt><dd>{SUBMISSION_LABEL[caseFile.technical.submission_status]}</dd></div>
                </dl>
                {caseFile.has_attachment ? <p className="agent-attachment-note">첨부 이미지가 있습니다. 현재 상담원용 이미지 조회 URL은 제공되지 않습니다.</p> : null}
              </article>

              <aside className="dashboard-card incident-card">
                <header><div><span className="section-kicker">INCIDENT CONTEXT</span><h2>관련 장애 맥락</h2></div></header>
                <p className="empty-copy">{caseFile.related_signals.length ? `관련 장애 신호 ${caseFile.related_signals.length}건` : "연결된 장애 의심 신호가 없습니다."}</p>
                <div className="incident-guide"><strong>안전 안내</strong><span>{caseFile.safety_notice}</span></div>
              </aside>
            </section>

            <section className="dashboard-card mismatch-card">
              <header><div><span className="section-kicker">MISMATCH CHECK</span><h2>상담 재확인·불일치</h2></div><span className={`signal-status ${result?.status === "IMPORTANT" ? "signal-urgent" : result ? "signal-resolved" : "signal-watch"}`}>{result ? VERIFICATION_LABEL[result.status] : "확인 필요"}</span></header>
              <form key={caseFile.card_id} className="verification-form" onSubmit={verify} onChange={() => { verificationRequestId.current = ""; setResult(null); }}>
                <div className="verification-grid">
                  <label>주문 구분<select name="action" defaultValue={caseFile.consultation.action}><option value="SELL">매도</option><option value="BUY">매수</option><option value="UNKNOWN">모름</option></select></label>
                  <label>상담 확인 종목<input name="symbol_name" defaultValue={caseFile.consultation.symbol_name ?? ""} placeholder="모름" /></label>
                  <label>종목코드<input name="symbol_code" maxLength={6} pattern="[0-9A-Z]{6}" title="대문자 영문 또는 숫자 6자리" defaultValue={caseFile.consultation.symbol_code ?? ""} placeholder="모름" onInput={(event) => { event.currentTarget.value = normalizeSymbolCode(event.currentTarget.value); }} /></label>
                  <label>상담 확인 수량<input name="quantity" type="number" min="1" defaultValue={caseFile.consultation.quantity ?? ""} placeholder="모름" /></label>
                  <label>상담 확인 가격<input name="price_krw" type="number" min="1" step="100" defaultValue={caseFile.consultation.price_krw ?? ""} placeholder="모름" /></label>
                  <label>주문 방식<select name="order_type" defaultValue={caseFile.consultation.order_type}><option value="LIMIT">지정가</option><option value="MARKET">시장가</option><option value="UNKNOWN">모름</option></select></label>
                  <label>주문 제출 여부<select name="submission_status" defaultValue={caseFile.technical.submission_status}><option value="CUSTOMER_REPORTED_SUBMITTED">제출됨</option><option value="CUSTOMER_REPORTED_NOT_SUBMITTED">제출되지 않음</option><option value="UNKNOWN">확인 불가</option></select></label>
                </div>
                <label className="verification-check"><input type="checkbox" required /> 기존 주문 제출·체결 내역을 확인했습니다.</label>
                <button className="primary-button" type="submit" disabled={saving}>{saving ? "저장 중..." : "재확인 결과 저장"}</button>
              </form>
              {result ? (
                <div className="verification-result" aria-live="polite">
                  <strong>{VERIFICATION_LABEL[result.status]}</strong>
                  <p>{result.mismatch_fields.length ? `불일치 항목: ${result.mismatch_fields.map((field) => FIELD_LABEL[field]).join(", ")}` : "중요 불일치가 없습니다."}</p>
                  <ul>{result.fields.map((field) => <li key={field.field}><span className={`issue-${field.status.toLowerCase().replace("_", "-")}`}>{VERIFICATION_LABEL[field.status]}</span><strong>{FIELD_LABEL[field.field]}</strong><small>{formatValue(field.field, field.customer_value)} → {formatValue(field.field, field.agent_value)}</small></li>)}</ul>
                </div>
              ) : null}
              <p className="mismatch-note">{caseFile.safety_notice}</p>
            </section>
          </>
        ) : null}
        <p className="service-disclaimer">상담 준비정보는 주문 접수·체결 증빙이 아닙니다. 공식 채널에서 실제 주문 상태를 확인해 주세요.</p>
      </div>
    </div>
  );
}
