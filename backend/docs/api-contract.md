# MTS SOS Desk API contract

실제 단일 원본은 `backend/openapi.json`이며 `frontend/src/generated/api.ts`는 이 파일에서
생성한다.

## 공통 규칙

- 업무 prefix: `/api`
- 고객 인증: `Authorization: Bearer <43-char-base64url-token>`
- 변경 요청: UUID v4 `client_request_id`
- 민감 응답: `Cache-Control: no-store`
- 오류: `application/problem+json`
- 참조번호와 token은 URL에 넣지 않는다.

## 구현 endpoint

| Method | Path | 의미 |
|---|---|---|
| POST | `/api/reports/analyze` | 서버 개인정보 검사·마스킹과 adapter 이중 구조화 |
| POST | `/api/reports` | 고객 전체 확인, 기술 증상·상담정보 분리 저장, 상담카드 발급 |
| DELETE | `/api/reports` | 저장 전 이탈한 미확정 제보 폐기 |
| DELETE | `/api/consultation-cards` | 소유권 확인 후 report root 전체 삭제 |

분석 요청은 16 KiB 이하 JSON이며 제보문은 NFC 정규화 후 20~500자로 제한한다. 같은
principal·작업·요청 ID의 재시도는 기존 결과를 반환하고 payload가 다르면 `409`다.
분석 row를 `PENDING`으로 먼저 commit한 뒤 transaction 밖에서 adapter를 호출하며 응답
상태는 `pending`, `confirmation`, `failed`, `complete` 중 하나다. 실패 code는 `TIMEOUT`,
`INVALID_SCHEMA`, `PROVIDER_UNAVAILABLE`만 외부에 노출한다.

제보문의 `[전화번호]`, `[계좌번호]`, `[이메일]`은 서버가 각각 `[PHONE]`, `[ACCOUNT]`,
`[EMAIL]`로 정규화한다. 저장값, AI 입력, API 응답과 `masked_items`에는 영문 placeholder만
사용한다. 실제 개인정보 재탐지·마스킹과 고위험 민감정보 거부도 계속 적용한다.

이미지가 없는 요청은 기존 JSON 계약을 사용한다. 이미지가 있으면 multipart의 `text`,
`client_request_id`, `screenshot`, `screenshot_redacted_confirmed=true`를 모두 보내야 한다.
확인값이 누락되거나 정확히 `true`가 아니면 파일 저장과 AI 호출 전에 `422`로 거부한다.
현재 attachment는 private local volume에 정제·재인코딩해 저장하며 응답에는 data URL
preview를 반환한다. Private Object Storage와 signed URL은 아직 구현하지 않았다.

AI adapter 전체 호출 제한은 10초이며 NVIDIA adapter의 동기 provider 호출은 최대 4개로
제한한다. `asyncio.to_thread()` timeout은 이미 실행 중인 provider thread를 중단하지 않으므로,
timeout 뒤에도 thread가 끝날 때까지 concurrency permit을 유지한다.

고객 확인은 `analysis_id`, 최신 `analysis_version`, 서버의 `masked_text`, 전체 `technical`과
`consultation`을 전송한다. 최신 `SUCCEEDED` 분석만 확정할 수 있으며 지정가는 양의 정수
가격이 필수이고 시장가 가격은 `null`이어야 한다. 성공 시 128-bit HMAC 기반 난수형
참조번호와 서버 기준 2시간 만료시각을 반환한다. DB에는 참조번호 평문을 저장하지 않는다.
주문 방향은 `BUY`, `SELL`, `UNKNOWN`을 지원한다.

미확정 제보 폐기 요청은 body에 `analysis_id`와 `client_request_id`를 넣는다. 현재 익명
세션이 소유한 미확정 report root만 삭제하며 같은 요청 재시도는 `204`, 확정된 제보는
`409`다. API consumer는 폐기 성공 후에만 분석 화면을 초기화해야 한다.

삭제 요청은 body에 `reference_number`와 `client_request_id`를 넣는다. 성공 시 `204`이며
report, analysis, 기술 증상, 상담카드를 cascade 삭제하고 비식별 audit와 삭제 멱등 기록만
남긴다.

실제 provider 사용 여부는 `AI_ADAPTER` 설정으로 선택하며 기본값은 deterministic Fake다.
NVIDIA 내부 client timeout은 AI 담당 파일의 별도 정합화가 필요하다.
