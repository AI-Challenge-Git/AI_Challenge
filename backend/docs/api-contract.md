# MTS SOS Desk API contract

OpenAPI가 최종 단일 원본이며, 이 문서는 구현과 함께 계속 갱신하는 백엔드 기준안이다.
프론트엔드·AI 합의가 끝나지 않은 항목은 합의 gate에 남기고 실제 계약으로 확정하지 않는다.

## 공통 규칙

- Prefix: `/api/v1`
- 고객 capability: `Authorization: Bearer <43-char-base64url-token>`
- 성공 응답 중 report·analysis·card 데이터: `Cache-Control: no-store`
- 오류: RFC 9457 `application/problem+json`
- 요청 model은 예상하지 않은 필드를 거부한다.
- 서버 시각은 offset을 포함한 UTC ISO 8601로 반환한다.

## Endpoints

| Method | Path | 의미 |
|---|---|---|
| POST | `/api/v1/reports` | 원문 검사·마스킹·멱등 report 생성·Fake 분석 |
| GET | `/api/v1/reports/{report_id}` | 소유 session의 report와 최신 분석 조회 |
| PUT | `/api/v1/reports/{report_id}/confirmation` | 최신 분석에 대한 고객 최종값 전체 확정 |

일반 CRUD, 카드 DELETE, 분석 재시도, 상담원·운영자 API는 현재 만들지 않는다.

## POST `/api/v1/reports`

```json
{
  "client_request_id": "58e06f0a-1220-46a0-b30f-e840716846be",
  "text": "주문 버튼을 누른 뒤 계속 로딩되고 주문번호를 확인하지 못했어요."
}
```

- 신규 생성은 `201`, 같은 session과 `client_request_id` 재요청은 `200`이다.
- 원문은 trim·NFC 정규화 후 20~500 Unicode code point를 검사한다.
- 휴대전화·지역번호·070·080·15xx~18xx 전화번호와 이메일을 마스킹한다.
- 계좌번호 후보는 구분자 3그룹 또는 구분자 없는 10~14자리 숫자를 마스킹한다.
- 주민번호·비밀번호·OTP 후보는 `422`로 거부한다.
- 활성 policy snapshot 또는 마스킹 경계를 사용할 수 없으면 `503`이며 row를 만들지 않는다.

## GET `/api/v1/reports/{report_id}`

- token 없음·형식 오류는 `401`이다.
- 존재하지 않거나 다른 session의 report는 동일하게 `404`다.
- UUID만으로 masked text나 분석 후보를 조회할 수 없다.

## PUT `/api/v1/reports/{report_id}/confirmation`

```json
{
  "analysis_version": 1,
  "technical_symptom": {
    "issue_type": "UNKNOWN",
    "symptom": null,
    "submission_status": "UNKNOWN",
    "error_code": null,
    "reported_occurred_at": null
  },
  "consultation": {
    "action": "SELL",
    "symbol_name": "삼성전자",
    "symbol_code": "005930",
    "quantity": 20,
    "order_type": "LIMIT",
    "price_krw": 70000,
    "attempted_at": "2026-08-14T00:03:00Z"
  }
}
```

- `channel`, `feature_area`, `taxonomy_version`은 서버가 최신 분석과 scope에서 설정한다.
- 문자열 수정값은 서버에서 민감정보를 다시 검사한다.
- stale version이나 이미 확정된 다른 payload는 `409`다.
- 기술 증상과 상담카드 생성 및 report `CONFIRMED` 전이는 한 transaction이다.

## 합의 gate

- FE-01~11: OpenAPI type, session token, 멱등성, 상태 UX, confirmation payload 승인
- AI-01~11: 추출 schema, field status, evidence, taxonomy, provider metadata 승인
- TEAM-02~04: 보존기간, 카드 삭제 의미, 공식 policy source 승인

승인 전 실제 AI adapter, 카드 DELETE, 참조번호·TTL, 보호된 상담원·운영자 endpoint를
구현하지 않는다.
