import type {
  AnalysisResponse,
  ConsultationData,
  FieldStatus,
  TechnicalData,
} from "../types";

type MaskRule = { label: string; pattern: RegExp };

const REJECT_RULES: MaskRule[] = [
  { label: "주민등록번호", pattern: /(?<!\d)\d{6}[\s.\/‐‑‒–—―−-]*[1-4]\d{6}(?!\d)/g },
  { label: "OTP", pattern: /(?:OTP|일회용\s*비밀번호)\s*[:：은는]?\s*\d{4,8}/gi },
  { label: "비밀번호", pattern: /(?:비밀번호|패스워드)\s*[:：은는]?\s*[A-Za-z0-9!@#$%^&*]{4,30}/gi },
];

const MASK_RULES: MaskRule[] = [
  { label: "전화번호", pattern: /(?<!\d)\(?(?:01[016789]|0[2-6][1-5]?)\)?[\s.\/‐‑‒–—―−-]*\d{3,4}[\s.\/‐‑‒–—―−-]*\d{4}(?!\d)/g },
  { label: "이메일", pattern: /\b[A-Z0-9._%+-]+\s*@\s*[A-Z0-9.-]+\s*\.\s*[A-Z]{2,}\b/gi },
  { label: "계좌번호", pattern: /(?:입금\s*)?계좌(?:번호)?\s*[:：은는]?\s*\d{8,16}(?!\d)/g },
  { label: "카드번호", pattern: /(?<!\d)\d{4}(?:[\s.\/‐‑‒–—―−-]*\d{4}){3}(?!\d)/g },
  { label: "계좌번호", pattern: /(?<!\d)(?:\d{2,6}[\s.\/‐‑‒–—―−-]+){2,3}\d{4,8}(?!\d)/g },
];

const SYMBOLS = [
  { aliases: ["삼성전자", "삼전"], name: "삼성전자", code: "005930" },
  { aliases: ["SK하이닉스", "하이닉스"], name: "SK하이닉스", code: "000660" },
  { aliases: ["카카오"], name: "카카오", code: "035720" },
  { aliases: ["네이버", "NAVER"], name: "NAVER", code: "035420" },
];

const KOREAN_NUMBERS: Record<string, number> = {
  한: 1,
  두: 2,
  세: 3,
  네: 4,
  다섯: 5,
  여섯: 6,
  일곱: 7,
  여덟: 8,
  아홉: 9,
  열: 10,
  스무: 20,
  서른: 30,
  마흔: 40,
  쉰: 50,
};

const confirmed = (value: unknown): FieldStatus =>
  value === null || value === undefined ? "UNKNOWN" : "CONFIRMED_FROM_TEXT";

function firstMatch(text: string, pattern: RegExp): string | null {
  const match = text.match(pattern);
  return match?.[0] ?? null;
}

export function maskSensitiveText(text: string): { text: string; detected: string[] } {
  for (const { label, pattern } of REJECT_RULES) {
    pattern.lastIndex = 0;
    if (pattern.test(text)) throw new Error(`${label}는 입력할 수 없습니다. 해당 정보를 지우고 다시 시도해 주세요.`);
  }
  let masked = text;
  const detected: string[] = [];
  for (const { label, pattern } of MASK_RULES) {
    pattern.lastIndex = 0;
    if (pattern.test(masked)) {
      detected.push(label);
      pattern.lastIndex = 0;
      masked = masked.replace(pattern, `[${label}]`);
    }
  }
  return { text: masked, detected };
}

function extractTime(text: string): { value: string | null; evidence: string | null } {
  const clock = text.match(/\b([01]?\d|2[0-3]):([0-5]\d)\b/);
  if (clock) {
    return {
      value: `${Number(clock[1]).toString().padStart(2, "0")}:${clock[2]}`,
      evidence: clock[0],
    };
  }
  const korean = text.match(/\b([01]?\d|2[0-3])\s*시(?:\s*([0-5]?\d)\s*분)?(?:\s*쯤)?/);
  if (!korean) return { value: null, evidence: null };
  return {
    value: `${Number(korean[1]).toString().padStart(2, "0")}:${Number(korean[2] ?? 0)
      .toString()
      .padStart(2, "0")}`,
    evidence: korean[0],
  };
}

function extractQuantity(text: string): { value: number | null; evidence: string | null } {
  const match = text.match(
    /(\d{1,7}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰)\s*주(?!문)/,
  );
  if (!match) return { value: null, evidence: null };
  return {
    value: /^\d+$/.test(match[1]) ? Number(match[1]) : KOREAN_NUMBERS[match[1]],
    evidence: match[0],
  };
}

function extractPrice(text: string): { value: number | null; evidence: string | null } {
  const manwon = text.match(/\b(\d{1,5})\s*만\s*원/);
  if (manwon) return { value: Number(manwon[1]) * 10_000, evidence: manwon[0] };
  const won = text.match(/\b(\d{1,3}(?:,\d{3})+|\d{4,9})\s*원/);
  if (!won) return { value: null, evidence: null };
  return { value: Number(won[1].replaceAll(",", "")), evidence: won[0] };
}

export function analyzeLocally(input: string): AnalysisResponse {
  const masked = maskSensitiveText(input);
  const text = masked.text;
  const occurred = extractTime(text);
  const channelEvidence = firstMatch(text, /M-?able|엠에이블|KB\s*(?:앱|증권)?/i);
  const orderEvidence = firstMatch(text, /주문|매도|매수|팔(?:려고|았|기)|사(?:려고|기)/);
  const loadingEvidence = firstMatch(
    text,
    /빙글빙글|로딩(?:만|이)?\s*(?:계속|지속)?|화면이?\s*(?:멈|안\s*넘어)|멈췄|넘어가지\s*않/,
  );
  const resultEvidence = firstMatch(
    text,
    /결과(?:가|는)?\s*(?:안|표시되지\s*않|확인되지\s*않)|주문번호(?:를|는)?\s*(?:못|확인하지\s*못)/,
  );
  const submittedEvidence = firstMatch(text, /주문번호(?:를|는)?\s*(?:받|확인했|봤)|접수(?:됐|되었|완료)/);

  let issueType: TechnicalData["issue_type"] = "UNKNOWN";
  let symptom = "기술 증상 확인 필요";
  let symptomEvidence: string | null = null;
  if (loadingEvidence) {
    issueType = "ORDER_SUBMISSION_FAILURE";
    symptom = "주문 버튼 이후 지속 로딩";
    symptomEvidence = loadingEvidence;
  } else if (resultEvidence) {
    issueType = "ORDER_RESULT_UNCONFIRMED";
    symptom = "주문 결과 미확인";
    symptomEvidence = resultEvidence;
  } else if (orderEvidence) {
    issueType = "UNRELATED_OR_AMBIGUOUS";
    symptom = "주문 단계 오류(상세 확인 필요)";
    symptomEvidence = orderEvidence;
  }

  const technicalEvidence: TechnicalData["evidence"] = {};
  if (occurred.evidence) technicalEvidence.occurred_at = occurred.evidence;
  if (channelEvidence) technicalEvidence.channel = channelEvidence;
  if (orderEvidence) technicalEvidence.feature_area = orderEvidence;
  if (symptomEvidence) {
    technicalEvidence.issue_type = symptomEvidence;
    technicalEvidence.symptom = symptomEvidence;
  }
  if (submittedEvidence || resultEvidence) {
    technicalEvidence.submission_status = submittedEvidence ?? resultEvidence!;
  }

  const technical: TechnicalData = {
    occurred_date: null,
    occurred_at: occurred.value,
    channel: channelEvidence ? "M-able" : "UNKNOWN",
    feature_area: orderEvidence ? "DOMESTIC_STOCK_ORDER" : "UNKNOWN",
    issue_type: issueType,
    symptom,
    submission_status: submittedEvidence ? "SUBMITTED" : "UNKNOWN",
    error_code: null,
    field_statuses: {
      occurred_date: occurred.value ? "NEEDS_CONFIRMATION" : "UNKNOWN",
      occurred_at: occurred.value ? "NEEDS_CONFIRMATION" : "UNKNOWN",
      channel: channelEvidence ? "CONFIRMED_FROM_TEXT" : "NEEDS_CONFIRMATION",
      feature_area: orderEvidence ? "CONFIRMED_FROM_TEXT" : "NEEDS_CONFIRMATION",
      issue_type: issueType === "UNKNOWN" ? "UNKNOWN" : "CONFIRMED_FROM_TEXT",
      symptom: symptomEvidence ? "CONFIRMED_FROM_TEXT" : "NEEDS_CONFIRMATION",
      submission_status: submittedEvidence ? "CONFIRMED_FROM_TEXT" : "UNKNOWN",
      error_code: "UNKNOWN",
    },
    evidence: technicalEvidence,
  };

  const sellEvidence = firstMatch(text, /매도|팔(?:려고|았|기|고)/);
  const buyEvidence = firstMatch(text, /매수|사(?:려고|기|고)/);
  const symbol = SYMBOLS.find(({ aliases }) =>
    aliases.some((alias) => text.toLocaleLowerCase().includes(alias.toLocaleLowerCase())),
  );
  const symbolEvidence = symbol?.aliases.find((alias) =>
    text.toLocaleLowerCase().includes(alias.toLocaleLowerCase()),
  );
  const quantity = extractQuantity(text);
  const price = extractPrice(text);
  const orderTypeEvidence = firstMatch(text, /지정가|시장가/);

  const consultationEvidence: ConsultationData["evidence"] = {};
  if (sellEvidence || buyEvidence) consultationEvidence.action = sellEvidence ?? buyEvidence!;
  if (symbolEvidence) {
    consultationEvidence.symbol_name = symbolEvidence;
    consultationEvidence.symbol_code = symbolEvidence;
  }
  if (quantity.evidence) consultationEvidence.quantity = quantity.evidence;
  if (price.evidence) consultationEvidence.price = price.evidence;
  if (orderTypeEvidence) consultationEvidence.order_type = orderTypeEvidence;
  if (occurred.evidence) consultationEvidence.attempted_at = occurred.evidence;

  const action: ConsultationData["action"] = sellEvidence ? "SELL" : "UNKNOWN";
  const orderType: ConsultationData["order_type"] = orderTypeEvidence
    ? orderTypeEvidence.includes("지정가")
      ? "LIMIT"
      : "MARKET"
    : "UNKNOWN";
  const consultation: ConsultationData = {
    action,
    symbol_name: symbol?.name ?? null,
    symbol_code: symbol?.code ?? null,
    quantity: quantity.value,
    order_type: orderType,
    price: price.value,
    attempted_at: occurred.value,
    field_statuses: {
      action: action === "UNKNOWN" ? "UNKNOWN" : buyEvidence ? "OUT_OF_SCOPE" : "CONFIRMED_FROM_TEXT",
      symbol_name: confirmed(symbol?.name),
      symbol_code: confirmed(symbol?.code),
      quantity: confirmed(quantity.value),
      order_type: orderTypeEvidence
        ? "CONFIRMED_FROM_TEXT"
        : price.value
          ? "NEEDS_CONFIRMATION"
          : "UNKNOWN",
      price: confirmed(price.value),
      attempted_at: occurred.value ? "NEEDS_CONFIRMATION" : "UNKNOWN",
    },
    evidence: consultationEvidence,
  };

  return {
    analysis_id: crypto.randomUUID(),
    analysis_version: 1,
    status: "confirmation",
    attachment: null,
    masked_text: masked.text,
    masked_items: masked.detected,
    technical,
    consultation,
  };
}
