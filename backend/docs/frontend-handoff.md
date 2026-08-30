# Frontend handoff

## 장애 의심 신호

- 최신 `backend/openapi.json`에서 generated type을 다시 생성해야 한다.
- `GET /api/signals/dashboard`는 고객 익명 Bearer token을 사용한다.
- 외부에 보이는 상태는 `SIGNAL_DETECTED | UNDER_REVIEW`뿐이며 `CANDIDATE`는 오지 않는다.
- 응답의 `updated_at`, `hourly_volume`, `applied_policy`를 최신 OpenAPI 타입으로 수용해야 한다.
  `hourly_volume`은 현재 노출 중인 신호의 제보를 server UTC `received_at` 시간 단위로 집계한 값이다.
- 정책 rolling window가 지난 `SIGNAL_DETECTED`는 목록에서 빠지며 `UNDER_REVIEW`는 계속 노출된다.
- 조회가 `429`이면 `Retry-After` 이후 재시도하며 polling을 즉시 반복하지 않는다.
- `baseline_status=INSUFFICIENT_HISTORY`, `baseline_ratio=null`은 정상적인 초기 운영 응답이다.
- `reporting_unique_sessions`는 고객 제보 기반 비식별 세션 수이며 실제 영향 고객 수로 표시하면 안 된다.
- 상담카드 상세 `related_signals`는 더 이상 무조건 빈 배열이 아니며 같은 typed 활성 신호가 포함될 수 있다.
- 상담카드 상세의 새 `attachment_url: string | null`은 인증·2시간 TTL 검증 후 발급된 짧은 signed
  URL이다. 값이 있을 때만 이미지를 표시하고 browser storage·console·analytics에 저장하지 않는다.
  URL이 만료되면 카드 상세를 다시 조회해 새 URL을 받아야 한다.
- 운영자 변경 API는 `OPERATOR` Bearer token이 필요하다. 일반 상담원 token으로 호출하면 `403`이다.
- 이번 조회 MVP에서는 운영자 회원가입·로그인·인증 및 상태변경 UI 연동은 작업 범위에서 제외한다.
- `official_incident=false`인 항목을 확정 장애로 표현하면 안 된다.

## KRX 종목코드

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
