import { useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BrainCircuit,
  Check,
  CheckCircle2,
  Clock3,
  Copy,
  ExternalLink,
  FileCheck2,
  Info,
  PencilLine,
  ShieldCheck,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import {
  analyzeReport,
  ApiError,
  deleteConsultationCard,
  discardAnalysis,
  saveConfirmedReport,
  validateScreenshot,
} from "./api";
import AgentDesk from "./AgentDesk";
import Dashboard from "./Dashboard";
import type {
  AnalysisResponse,
  ConsultationData,
  FieldStatus,
  SavedCard,
  TechnicalData,
} from "./types";

const STATUS_LABEL: Record<FieldStatus, string> = {
  CONFIRMED_FROM_TEXT: "원문 확인",
  NEEDS_CONFIRMATION: "확인 필요",
  UNKNOWN: "모름",
  OUT_OF_SCOPE: "범위 밖",
};

const ISSUE_LABEL: Record<TechnicalData["issue_type"], string> = {
  ORDER_SUBMISSION_FAILURE: "주문 제출 단계 오류",
  ORDER_RESULT_UNCONFIRMED: "주문 결과 미확인",
  LOGIN_ACCESS_FAILURE: "로그인·접속 오류",
  BALANCE_INQUIRY_ERROR: "잔고 조회 오류",
  DEVICE_NETWORK_SUSPECTED: "기기·네트워크 의심",
  UNRELATED_OR_AMBIGUOUS: "기타·불명확",
  UNKNOWN: "모름",
};

const SUBMISSION_LABEL: Record<TechnicalData["submission_status"], string> = {
  SUBMITTED: "제출됨",
  NOT_SUBMITTED: "제출되지 않음",
  UNKNOWN: "확인 불가",
};

const ACTION_LABEL = {
  SELL: "매도",
  UNKNOWN: "모름",
};

const ORDER_TYPE_LABEL: Record<ConsultationData["order_type"], string> = {
  LIMIT: "지정가",
  MARKET: "시장가",
  UNKNOWN: "모름",
};

type Stage = "input" | "review" | "complete";
type AnalysisState = "idle" | "pending" | "confirmation" | "failed" | "complete";

interface ResultFieldProps {
  label: string;
  status: FieldStatus;
  evidence?: string;
  edited?: boolean;
  children: React.ReactNode;
}

function StatusBadge({ status, edited = false }: { status: FieldStatus; edited?: boolean }) {
  if (edited) {
    return (
      <span className="status-badge status-edited">
        <PencilLine size={12} aria-hidden="true" /> 고객 수정
      </span>
    );
  }
  return <span className={`status-badge status-${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>;
}

function ResultField({ label, status, evidence, edited, children }: ResultFieldProps) {
  return (
    <div className="result-field">
      <div className="field-label-row">
        <span className="field-label">{label}</span>
        <StatusBadge status={status} edited={edited} />
      </div>
      {children}
      {evidence && !edited ? <p className="evidence">원문 근거 “{evidence}”</p> : null}
    </div>
  );
}

function Stepper({ stage }: { stage: Stage }) {
  const active = stage === "input" ? 1 : stage === "review" ? 2 : 3;
  const steps = ["오류 제보", "분석 확인", "준비 완료"];
  return (
    <ol className="stepper" aria-label="제보 진행 단계">
      {steps.map((step, index) => {
        const number = index + 1;
        const done = number < active;
        const current = number === active;
        return (
          <li key={step} className={current ? "current" : done ? "done" : ""} aria-current={current ? "step" : undefined}>
            <span className="step-number">{done ? <Check size={14} /> : number}</span>
            <span>{step}</span>
          </li>
        );
      })}
    </ol>
  );
}

export default function App() {
  const [view, setView] = useState<"report" | "dashboard" | "agent">("report");
  const [stage, setStage] = useState<Stage>("input");
  const [reportText, setReportText] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [savedCard, setSavedCard] = useState<SavedCard | null>(null);
  const [editedFields, setEditedFields] = useState<Set<string>>(new Set());
  const [analysisState, setAnalysisState] = useState<AnalysisState>("idle");
  const [occurredAtConfirmed, setOccurredAtConfirmed] = useState(false);
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [screenshotName, setScreenshotName] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const analyzeRequestId = useRef("");
  const saveRequestId = useRef("");
  const discardRequestId = useRef("");
  const deleteRequestId = useRef("");

  const reportLength = [...reportText.trim().normalize("NFC")].length;
  const isValidReport = reportLength >= 20 && reportLength <= 500;

  const markEdited = (field: string) => {
    setEditedFields((current) => new Set(current).add(field));
    saveRequestId.current = "";
  };

  const updateTechnical = <K extends keyof TechnicalData>(key: K, value: TechnicalData[K]) => {
    setAnalysis((current) =>
      current ? { ...current, technical: { ...current.technical, [key]: value } } : current,
    );
    if (key === "occurred_date" || key === "occurred_at") setOccurredAtConfirmed(false);
    markEdited(`technical.${String(key)}`);
  };

  const updateConsultation = <K extends keyof ConsultationData>(key: K, value: ConsultationData[K]) => {
    setAnalysis((current) =>
      current ? { ...current, consultation: { ...current.consultation, [key]: value } } : current,
    );
    markEdited(`consultation.${String(key)}`);
  };

  const handleAnalyze = async () => {
    if (!isValidReport) {
      setError("오류 상황을 20자 이상 500자 이하로 입력해 주세요.");
      return;
    }
    setIsLoading(true);
    setAnalysisState("pending");
    setError(null);
    try {
      analyzeRequestId.current ||= crypto.randomUUID();
      const result = await analyzeReport(reportText, analyzeRequestId.current, screenshot ?? undefined);
      if (result.status === "failed") {
        setAnalysisState("failed");
        analyzeRequestId.current = "";
        setError("분석을 완료하지 못했습니다. 다시 시도해 주세요.");
        return;
      }
      if (result.status === "complete") {
        setAnalysisState("complete");
        analyzeRequestId.current = "";
        setError("이미 확인이 완료된 분석입니다. 새 제보로 다시 시작해 주세요.");
        return;
      }
      if (result.status === "pending") return;
      setReportText("");
      setScreenshot(null);
      setScreenshotName("");
      setAnalysis(result);
      setEditedFields(new Set());
      setOccurredAtConfirmed(false);
      setAnalysisState("confirmation");
      analyzeRequestId.current = "";
      discardRequestId.current = "";
      setStage("review");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (reason) {
      setAnalysisState("failed");
      setError(reason instanceof Error ? reason.message : "분석 중 문제가 발생했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSave = async () => {
    if (!analysis) return;
    if ((analysis.technical.occurred_date || analysis.technical.occurred_at)
      && (!analysis.technical.occurred_date || !analysis.technical.occurred_at || !occurredAtConfirmed)) {
      setError("발생 날짜와 시각을 직접 확인해 주세요.");
      return;
    }
    if (analysis.consultation.order_type === "LIMIT"
      && (!Number.isInteger(analysis.consultation.price) || (analysis.consultation.price ?? 0) <= 0)) {
      setError("지정가는 0보다 큰 정수 가격을 입력해 주세요.");
      return;
    }
    if (analysis.consultation.order_type === "MARKET" && analysis.consultation.price !== null) {
      setError("시장가는 가격을 입력하지 않아야 합니다.");
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      saveRequestId.current ||= crypto.randomUUID();
      const saved = await saveConfirmedReport({
        analysis_id: analysis.analysis_id,
        analysis_version: analysis.analysis_version,
        attachment_id: analysis.attachment?.id ?? null,
        masked_text: analysis.masked_text,
        technical: analysis.technical,
        consultation: analysis.consultation,
      }, saveRequestId.current);
      setSavedCard(saved);
      setReportText("");
      setAnalysisState("complete");
      saveRequestId.current = "";
      setStage("complete");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (reason) {
      if (reason instanceof ApiError
        && (reason.code === "STALE_ANALYSIS" || reason.code === "ANALYSIS_NOT_READY")) {
        setReportText(analysis.masked_text);
        setAnalysis(null);
        setAnalysisState("idle");
        setStage("input");
        analyzeRequestId.current = "";
        saveRequestId.current = "";
      }
      setError(reason instanceof Error ? reason.message : "저장 중 문제가 발생했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  const discardReport = async () => {
    if (!analysis) return;
    setIsLoading(true);
    setError(null);
    try {
      discardRequestId.current ||= crypto.randomUUID();
      await discardAnalysis(analysis.analysis_id, discardRequestId.current);
      reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "미확정 제보를 폐기하지 못했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  const reset = () => {
    setStage("input");
    setReportText("");
    setAnalysis(null);
    setSavedCard(null);
    setEditedFields(new Set());
    setAnalysisState("idle");
    setOccurredAtConfirmed(false);
    setScreenshot(null);
    setScreenshotName("");
    setError(null);
    setCopied(false);
    analyzeRequestId.current = "";
    saveRequestId.current = "";
    discardRequestId.current = "";
    deleteRequestId.current = "";
  };

  const copyReference = async () => {
    if (!savedCard) return;
    await navigator.clipboard.writeText(savedCard.reference_number);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  const deleteCard = async () => {
    if (!savedCard || !window.confirm("이 참조번호와 연결된 제보 전체를 삭제할까요? 삭제 후에는 다시 조회할 수 없습니다.")) return;
    setIsLoading(true);
    setError(null);
    try {
      deleteRequestId.current ||= crypto.randomUUID();
      await deleteConsultationCard(savedCard.reference_number, deleteRequestId.current);
      reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "상담 준비카드를 삭제하지 못했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="MTS SOS Desk 홈" onClick={() => setView("report")}>
          <span className="brand-mark"><ShieldCheck size={21} /></span>
          <span>MTS SOS <strong>Desk</strong></span>
        </a>
        <nav className="main-nav" aria-label="주요 화면">
          <button type="button" className={view === "report" ? "current" : ""} aria-pressed={view === "report"} onClick={() => setView("report")}>고객 제보</button>
          <button type="button" className={view === "dashboard" ? "current" : ""} aria-pressed={view === "dashboard"} onClick={() => setView("dashboard")}>운영 상황판</button>
          <button type="button" className={view === "agent" ? "current" : ""} aria-pressed={view === "agent"} onClick={() => setView("agent")}>상담원</button>
        </nav>
      </header>

      <main id="top">
        {view === "report" ? (
          <>
        <section className="hero">
          <div className="hero-copy">
            <h1>당황스러운 주문 오류!<br /><em>상담에 필요한 내용</em>부터<br /> 정리해 드립니다.</h1>
            <p>겪으신 상황을 작성해주시면 AI가 기술 증상과 주문 상담정보를 나누고, 저장 전 직접 확인할 수 있게 도와드립니다.</p>
          </div>
          <div className="hero-cards" aria-hidden="true">
            <div className="hero-card hero-card-back">
              <span>문제 제보 분리</span>
              <strong>의미 기반</strong>
            </div>
            <div className="hero-card hero-card-front">
              <span>MTS 장애 제보</span>
              <strong>손쉬운<br />장애대응 제공</strong>
              <span className="hero-card-badge"><BrainCircuit size={17} /></span>
              <span className="hero-bars"><i /><i /><i /></span>
            </div>
          </div>
        </section>

        <section className="workspace" aria-label="고객 제보 작성">
          <Stepper stage={stage} />

          {stage === "input" ? (
            <div className="panel input-panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">STEP 1</span>
                  <h2>어떤 문제가 있었나요?</h2>
                  <p>발생 시각, 화면, 버튼을 누른 뒤의 상태를 함께 적으면 더 정확하게 정리할 수 있어요.</p>
                </div>
              </div>

              <div className={`textarea-shell ${error ? "has-error" : ""}`}>
                <textarea
                  value={reportText}
                  onChange={(event) => {
                    if ([...event.target.value.normalize("NFC")].length <= 500) setReportText(event.target.value);
                    analyzeRequestId.current = "";
                    setAnalysisState("idle");
                    if (error) setError(null);
                  }}
                  placeholder="예: 9시쯤 KB 앱에서 매도 주문을 눌렀는데 계속 로딩되고 주문번호를 확인하지 못했어요."
                  aria-describedby="report-help report-error"
                  aria-label="MTS 오류 상황"
                  aria-invalid={Boolean(error)}
                />
                <span className={reportLength > 480 ? "char-count limit" : "char-count"}>
                  {reportLength} / 500자
                </span>
              </div>
              <div className="input-meta">
                <p id="report-help">계좌번호·전화번호·이메일은 자동 마스킹됩니다. 주민등록번호·비밀번호·OTP가 포함되면 요청을 거부합니다.</p>
              </div>
              <label className="image-upload">
                <span>오류 화면 이미지 (선택)</span>
                <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => {
                  const file = event.target.files?.[0] ?? null;
                  try {
                    if (file) validateScreenshot(file);
                    setScreenshot(file);
                    setScreenshotName(file?.name ?? "");
                    analyzeRequestId.current = "";
                    setAnalysisState("idle");
                    setError(null);
                  } catch (reason) {
                    event.target.value = "";
                    setScreenshot(null);
                    setScreenshotName("");
                    setError(reason instanceof Error ? reason.message : "이미지를 첨부하지 못했습니다.");
                  }
                }} />
                <small>{screenshotName || "PNG·JPG·WebP, 최대 5MB · 계좌번호 등 개인정보가 보이지 않게 가려 주세요."}</small>
              </label>
              {error ? <p id="report-error" className="error-message" role="alert"><TriangleAlert size={16} /> {error}</p> : null}

              <button className="primary-button analyze-button" type="button" onClick={handleAnalyze} disabled={analysisState === "pending" || !isValidReport}>
                  {analysisState === "pending" ? <><span className="spinner" /> 안전하게 분석 중...</> : analysisState === "failed" ? <>다시 분석하기 <ArrowRight size={18} /></> : <>AI로 내용 정리하기 <ArrowRight size={18} /></>}
              </button>
            </div>
          ) : null}

          {stage === "review" && analysis ? (
            <div className="review-flow">
              <div className="review-heading">
                <div>
                  <span className="section-kicker">STEP 2</span>
                  <h2>AI가 정리한 내용이 맞는지 확인해 주세요.</h2>
                  <p>틀린 값은 바로 고치고, 기억나지 않는 값은 ‘모름’으로 두어도 됩니다.</p>
                </div>
                <button className="text-button" type="button" onClick={discardReport} disabled={isLoading}><ArrowLeft size={16} /> {isLoading ? "폐기 중..." : "제보 다시 쓰기"}</button>
              </div>

              {analysis.masked_items.length > 0 ? (
                <div className="mask-result"><ShieldCheck size={17} /> {analysis.masked_items.join(", ")} 정보를 찾아 안전하게 가렸습니다.</div>
              ) : null}

              <details className="original-text">
                <summary><FileCheck2 size={17} /> 마스킹된 제보 원문 보기</summary>
                <p>{analysis.masked_text}</p>
              </details>

              {analysis.attachment ? (
                <figure className="attachment-preview">
                  <img src={analysis.attachment.url} alt="고객이 첨부한 MTS 오류 화면" />
                  <figcaption>첨부한 오류 화면 · 최종 저장 후 상담원이 함께 확인합니다.</figcaption>
                </figure>
              ) : null}

              <div className="result-grid">
                <article className="result-card technical-card">
                  <header>
                    <span className="card-icon"><BrainCircuit size={21} /></span>
                    <div><span>집계에 사용</span><h3>기술 증상</h3></div>
                    <span className="privacy-chip">개인 주문정보 제외</span>
                  </header>
                  <div className="field-grid">
                    <ResultField label="발생 날짜" status={analysis.technical.field_statuses.occurred_date} evidence={analysis.technical.evidence.occurred_date} edited={editedFields.has("technical.occurred_date")}>
                      <input aria-label="발생 날짜" type="date" value={analysis.technical.occurred_date ?? ""} onChange={(e) => updateTechnical("occurred_date", e.target.value || null)} />
                    </ResultField>
                    <ResultField label="이용 채널" status={analysis.technical.field_statuses.channel} evidence={analysis.technical.evidence.channel} edited={editedFields.has("technical.channel")}>
                      <select aria-label="이용 채널" value={analysis.technical.channel} onChange={(e) => updateTechnical("channel", e.target.value as TechnicalData["channel"])}>
                        <option value="M-able">M-able</option><option value="UNKNOWN">모름</option>
                      </select>
                    </ResultField>
                    <ResultField label="발생 시각" status={analysis.technical.field_statuses.occurred_at} evidence={analysis.technical.evidence.occurred_at} edited={editedFields.has("technical.occurred_at")}>
                      <input aria-label="발생 시각" type="time" value={analysis.technical.occurred_at ?? ""} onChange={(e) => updateTechnical("occurred_at", e.target.value || null)} />
                    </ResultField>
                    {analysis.technical.occurred_at ? <label className="verification-check"><input type="checkbox" checked={occurredAtConfirmed} onChange={(e) => setOccurredAtConfirmed(e.target.checked)} /> 발생 날짜와 시각을 직접 확인했습니다.</label> : null}
                    <ResultField label="기능 영역" status={analysis.technical.field_statuses.feature_area} evidence={analysis.technical.evidence.feature_area} edited={editedFields.has("technical.feature_area")}>
                      <select aria-label="기능 영역" value={analysis.technical.feature_area} onChange={(e) => updateTechnical("feature_area", e.target.value as TechnicalData["feature_area"])}>
                        <option value="DOMESTIC_STOCK_ORDER">국내주식 주문</option><option value="UNKNOWN">모름</option>
                      </select>
                    </ResultField>
                    <ResultField label="오류 유형" status={analysis.technical.field_statuses.issue_type} evidence={analysis.technical.evidence.issue_type} edited={editedFields.has("technical.issue_type")}>
                      <select aria-label="오류 유형" value={analysis.technical.issue_type} onChange={(e) => updateTechnical("issue_type", e.target.value as TechnicalData["issue_type"])}>
                        {Object.entries(ISSUE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </ResultField>
                    <ResultField label="공통 증상" status={analysis.technical.field_statuses.symptom} evidence={analysis.technical.evidence.symptom} edited={editedFields.has("technical.symptom")}>
                      <input aria-label="공통 증상" value={analysis.technical.symptom} onChange={(e) => updateTechnical("symptom", e.target.value)} />
                      <p className="evidence">종목·수량·가격·주문 구분·개인정보는 입력하지 마세요. 저장 시 서버가 다시 검사합니다.</p>
                    </ResultField>
                    <ResultField label="주문 제출 여부" status={analysis.technical.field_statuses.submission_status} evidence={analysis.technical.evidence.submission_status} edited={editedFields.has("technical.submission_status")}>
                      <select aria-label="주문 제출 여부" value={analysis.technical.submission_status} onChange={(e) => updateTechnical("submission_status", e.target.value as TechnicalData["submission_status"])}>
                        {Object.entries(SUBMISSION_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </ResultField>
                  </div>
                </article>

                <article className="result-card consultation-card">
                  <header>
                    <span className="card-icon"><FileCheck2 size={21} /></span>
                    <div><span>현재 고객만 확인</span><h3>상담 준비정보</h3></div>
                    <span className="privacy-chip">군집화에서 제외</span>
                  </header>
                  <div className="field-grid">
                    <ResultField label="주문 구분" status={analysis.consultation.field_statuses.action} evidence={analysis.consultation.evidence.action} edited={editedFields.has("consultation.action")}>
                      <select aria-label="주문 구분" value={analysis.consultation.action} onChange={(e) => updateConsultation("action", e.target.value as ConsultationData["action"])}>
                        {Object.entries(ACTION_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </ResultField>
                    <ResultField label="종목명" status={analysis.consultation.field_statuses.symbol_name} evidence={analysis.consultation.evidence.symbol_name} edited={editedFields.has("consultation.symbol_name")}>
                      <input aria-label="종목명" value={analysis.consultation.symbol_name ?? ""} placeholder="모름" onChange={(e) => updateConsultation("symbol_name", e.target.value || null)} />
                    </ResultField>
                    <ResultField label="종목코드" status={analysis.consultation.field_statuses.symbol_code} evidence={analysis.consultation.evidence.symbol_code} edited={editedFields.has("consultation.symbol_code")}>
                      <input aria-label="종목코드" inputMode="numeric" maxLength={6} value={analysis.consultation.symbol_code ?? ""} placeholder="모름" onChange={(e) => updateConsultation("symbol_code", e.target.value.replace(/\D/g, "") || null)} />
                    </ResultField>
                    <ResultField label="수량" status={analysis.consultation.field_statuses.quantity} evidence={analysis.consultation.evidence.quantity} edited={editedFields.has("consultation.quantity")}>
                      <div className="suffix-input"><input aria-label="수량" type="number" min="1" value={analysis.consultation.quantity ?? ""} placeholder="모름" onChange={(e) => updateConsultation("quantity", e.target.value ? Number(e.target.value) : null)} /><span>주</span></div>
                    </ResultField>
                    <ResultField label="주문 방식" status={analysis.consultation.field_statuses.order_type} evidence={analysis.consultation.evidence.order_type} edited={editedFields.has("consultation.order_type")}>
                      <select aria-label="주문 방식" value={analysis.consultation.order_type} onChange={(e) => updateConsultation("order_type", e.target.value as ConsultationData["order_type"])}>
                        {Object.entries(ORDER_TYPE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </ResultField>
                    <ResultField label="희망 가격" status={analysis.consultation.field_statuses.price} evidence={analysis.consultation.evidence.price} edited={editedFields.has("consultation.price")}>
                      <div className="suffix-input"><input aria-label="희망 가격" type="number" min="1" step="100" value={analysis.consultation.price ?? ""} placeholder="모름" onChange={(e) => updateConsultation("price", e.target.value ? Number(e.target.value) : null)} /><span>원</span></div>
                    </ResultField>
                  </div>
                </article>
              </div>

              {error ? <p className="error-message" role="alert"><TriangleAlert size={16} /> {error}</p> : null}
              <div className="review-actions">
                <button className="primary-button" type="button" onClick={handleSave} disabled={isLoading}>
                  {isLoading ? <><span className="spinner" /> 저장 중...</> : <>이 내용으로 상담 준비하기 <ArrowRight size={18} /></>}
                </button>
              </div>
            </div>
          ) : null}

          {stage === "complete" && savedCard ? (
            <div className="panel complete-panel">
              <span className="complete-icon"><CheckCircle2 size={34} /></span>
              <span className="section-kicker">STEP 3</span>
              <h2>상담 준비가 완료됐어요.</h2>
              <p>상담원에게 아래 참조번호를 알려주시면 확인한 내용을 빠르게 찾을 수 있습니다.</p>
              <div className="reference-card">
                <span>상담 참조번호</span>
                <strong>{savedCard.reference_number}</strong>
                <button type="button" onClick={copyReference}>{copied ? <><Check size={16} /> 복사됨</> : <><Copy size={16} /> 번호 복사</>}</button>
              </div>
              <div className="expiry"><Clock3 size={17} /> {new Date(savedCard.expires_at).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" })}까지 유효 · 2시간 후 자동 만료</div>
              <div className="order-warning"><TriangleAlert size={19} /><p><strong>상담 준비카드는 주문 접수증이 아닙니다.</strong><span>실제 주문은 공식 고객센터 또는 영업점에서 본인확인과 주문내용 재확인을 거쳐야 합니다.</span></p></div>
              {error ? <p className="error-message" role="alert"><TriangleAlert size={16} /> {error}</p> : null}
              <button className="secondary-button" type="button" onClick={reset}>새 제보 작성하기</button>
              <button className="delete-button" type="button" onClick={deleteCard} disabled={isLoading}><Trash2 size={15} /> {isLoading ? "삭제 중..." : "제보 전체 삭제"}</button>
            </div>
          ) : null}

          <div className="safety-notice" role="note">
            <Info size={17} aria-hidden="true" />
            <p>현재 표시되는 내용은 고객 제보를 바탕으로 탐지된 <strong>장애 의심 신호</strong>이며, 공식 확인한 장애가 아닐 수 있습니다.<br />상담 준비정보는 주문 접수·체결 증빙이 아니므로 공식 채널에서 주문 상태를 확인해 주세요.</p>
            <a href="https://www.kbsec.com/go.able?linkcd=m06030002" target="_blank" rel="noreferrer">KB증권 고객센터 1588-6611 <ExternalLink size={12} aria-hidden="true" /></a>
          </div>
        </section>
          </>
        ) : view === "dashboard" ? <Dashboard /> : <AgentDesk />}
      </main>

    </>
  );
}
