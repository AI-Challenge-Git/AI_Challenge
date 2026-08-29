# Frontend handoff: KRX 종목코드

백엔드 OpenAPI의 `symbol_code` 계약이 숫자 전용에서 정확히 6자리 대문자 영숫자
`^[0-9A-Z]{6}$`로 확장됐다. KRX 원천의 접두 `A`는 제외한 단축코드이며 `005930`, `0011A0`이 모두
유효하다.

프론트 담당 반영사항:

- 고객 확인 화면과 상담원 재확인 화면의 `.replace(/\D/g, "")` 같은 숫자 전용 필터 제거
- 종목코드 입력을 대문자 영숫자 최대 6자리로 제한하고 lowercase 입력은 대문자로 변환
- `inputMode="numeric"` 제거 또는 영숫자 입력에 맞는 값으로 변경
- 최신 `backend/openapi.json`에서 generated TypeScript type 재생성
- `503 SYMBOL_MASTER_UNAVAILABLE`, `422 UNSUPPORTED_SYMBOL`, `422 SYMBOL_MISMATCH` 표시 처리
- 종목이 미확정이면 임의 문자열 `UNKNOWN` 대신 기존 계약대로 `symbol_code=null` 전송

이 작업에서는 `frontend/**`를 수정하지 않았다.
