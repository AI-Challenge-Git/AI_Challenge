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
