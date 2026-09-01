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
| POST | `/api/auth/login` | 데모 상담원 계정 로그인과 opaque access token 발급 |
| GET | `/api/agent/consultation-cards` | 72시간 내 상담카드 목록 조회 |
| POST | `/api/consultation-cards/lookup` | 참조번호 또는 card ID로 활성 카드 상세 조회 |
| POST | `/api/consultation-cards/verifications` | 상담원 재확인값 비교·저장 |

분석 요청은 16 KiB 이하 JSON이며 제보문은 NFC 정규화 후 20~500자로 제한한다. 같은
principal·작업·요청 ID의 재시도는 기존 결과를 반환하고 payload가 다르면 `409`다.
분석 row를 `PENDING`으로 먼저 commit한 뒤 transaction 밖에서 adapter를 호출하며 응답
상태는 `pending`, `confirmation`, `failed`, `complete` 중 하나다. 실패 code는 `TIMEOUT`,
`INVALID_SCHEMA`, `PROVIDER_UNAVAILABLE`만 외부에 노출한다.

새 분석 호출과 stale 분석 재처리는 비식별 client fingerprint별 PostgreSQL 원자적 bucket으로
제한한다. 기본값은 5회/60초이며 초과 시 `429`와 `Retry-After`를 반환한다. 멱등 replay는 AI를
호출하지 않으므로 이 제한을 소비하지 않는다. `PENDING`이 기본 180초 이상 갱신되지 않으면 같은
report와 analysis를 lease 방식으로 점유해 재처리한다. stale 기준은 AI timeout보다 길어야 한다.

AI timeout·schema 오류·provider 오류가 나면 미완성 report, analysis, attachment metadata와
저장된 이미지는 즉시 삭제한다. 같은 principal·요청 ID·payload의 재전송에는 독립된 안전한
실패 멱등 기록으로 최초 `failed` 응답을 재생해 AI를 다시 호출하지 않는다. 실패 응답의
`analysis_id`는 프론트 호환을 위한 결정론적 opaque 값이며 저장된 분석 resource를 뜻하지
않는다. 다른 payload는 `409`, 재분석은 새 UUID v4 요청 ID가 필요하다.

제보문의 `[전화번호]`, `[계좌번호]`, `[이메일]`은 서버가 각각 `[PHONE]`, `[ACCOUNT]`,
`[EMAIL]`로 정규화한다. 저장값, AI 입력, API 응답과 `masked_items`에는 영문 placeholder만
사용한다. 실제 개인정보 재탐지·마스킹과 고위험 민감정보 거부도 계속 적용한다.

이미지가 없는 요청은 기존 JSON 계약을 사용한다. 이미지가 있으면 multipart의 `text`,
`client_request_id`, `screenshot`, `screenshot_redacted_confirmed=true`를 모두 보내야 한다.
확인값이 누락되거나 정확히 `true`가 아니면 파일 저장과 AI 호출 전에 `422`로 거부한다.
현재 attachment는 private local volume에 정제·재인코딩해 저장하며 응답에는 data URL
preview를 반환한다. Private Object Storage와 signed URL은 아직 구현하지 않았다.

백엔드 service의 AI 전체 호출 제한은 90초이며 동시 AI 호출은 최대 4개로 제한한다.
`asyncio.to_thread()` timeout은 이미 실행 중인 provider thread를 중단하지 않으므로,
timeout 뒤에도 adapter task가 끝날 때까지 concurrency permit을 유지한다.

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
남긴다. attachment object 삭제가 실패해도 object key는 API에 노출하지 않고 독립 deletion
job으로 재시도한다.

상담카드 상세 접근 가능 시간은 발급 후 2시간이며 만료시각부터 조회할 수 없다. report와 현재
관련 업무 데이터는 서버 `received_at + 72시간`에 물리 삭제 대상이 된다. 접근 TTL과 물리
보존기간은 서로 독립적이다. purge는 backend CLI로 수행하며 기본은 dry-run이다.

runtime은 `AI_ADAPTER=openai`만 허용해 실제 provider를 사용한다. Fake는 dependency를 명시적으로
교체하는 테스트 격리에만 사용하며 runtime 설정으로 선택할 수 없다. OpenAI 내부 provider 호출과
correction retry는 백엔드의 전체 90초 예산 안에 끝나야 한다.

## 상담원 인증과 카드 조회

`POST /api/auth/login`은 `employee_id`, `password`를 받고 256-bit opaque `access_token`,
`token_type=bearer`, `expires_at`, `agent_label`, `role`을 반환한다. 기본 만료는 30분이며 환경변수로
조정한다. password는 Argon2 hash로만, token은 전용 HMAC digest로만 DB에 저장한다. 존재하지 않는
ID, 잘못된 password, disabled account는 같은 `401` 외부 오류를 사용한다.

`GET /api/agent/consultation-cards`는 기본 50개, 최대 100개를 `limit`·`offset`으로 조회한다.
`reports.purge_at`이 지나지 않은 카드만 최신 접수 순으로 반환하며 2시간 TTL이 지난 카드도
`expired=true`, `can_open=false`로 목록에 남는다. 목록 식별자는 참조번호가 아니라 opaque
`card_id`다. 목록에는 주문 상세, 고객 session, masked text, 참조번호·digest와 attachment 내부값이
없다.

`POST /api/consultation-cards/lookup`은 body에 `reference_number` 또는 `card_id` 중 정확히 하나를
받는다. 유효한 `AGENT` token, report 보존기간과 카드 TTL을 모두 검증한다. 없는·삭제된·만료된
카드는 동일한 `404`를 반환한다. 응답에는 비식별 기술 증상, 고객 확정 consultation 값,
verification 상태, 안전 안내문과 `has_attachment`만 포함한다. 운영 signed URL과 장애 신호가 아직
없으므로 attachment URL은 반환하지 않는다. `related_signals`에는 현재 카드의 제보가 속한
`SIGNAL_DETECTED` 또는 `UNDER_REVIEW` 신호만 typed DTO로 포함되며 내부 `CANDIDATE`는 노출하지 않는다.

`POST /api/consultation-cards/verifications`도 `reference_number | card_id` 중 하나와 UUID v4
`client_request_id`를 받는다. `action`, 종목, 수량, 주문 방식, `price_krw`, 고객 진술 제출 상태와
`order_history_checked`를 저장한다. 지정가는 양의 가격 필수, 시장가 가격은 `null`이며 BUY·SELL을
모두 지원한다. 실제 주문 성공·체결 결과 필드는 허용하지 않는다.

비교는 AI 없이 구조화 값으로 수행한다. 구체 값 불일치는 `IMPORTANT`, 한쪽이 null·UNKNOWN이면
`NEEDS_CONFIRMATION`, 일치하면 `MATCHED`다. 주문내역 확인을 수행하지 않았으면 전체 결과가 최소
`NEEDS_CONFIRMATION`이다. 같은 agent·operation·요청 ID와 같은 payload는 최초 verification을
재생하고 다른 payload는 `409`다.

로그인 실패 제한은 employee/client HMAC fingerprint 기준 기본 5회/5분, lookup은 agent/client
fingerprint 기준 기본 10회/60초다. bucket은 PostgreSQL 원자적 upsert로 공유되며 제한 시 `429`와
`Retry-After`를 반환한다. raw IP와 employee ID는 rate-limit·audit에 저장하지 않는다. lookup의
성공·실패·만료·제한과 로그인·verification 보안 event는 주문 상세 없이 audit한다.

## KRX 종목 검증

고객 최종 저장과 상담원 재확인의 `symbol_code`는 null 또는 KRX 접두 `A`가 제외된 정확히 6자리
대문자 영숫자 `^[0-9A-Z]{6}$`다. 숫자 6자리의 leading zero도 문자열로 보존한다. 종목이 미확정된
`UNKNOWN` 상태는 code를 null로 전달한다. 구체적인 코드가 있으면 활성 KRX Symbol Master에서 코드
존재 여부와 NFC 정규화된 `symbol_name`의 정확한 일치를 저장 전에 검사한다.

| HTTP | code | 의미 |
|---|---|---|
| 503 | `SYMBOL_MASTER_UNAVAILABLE` | 활성 Master가 없어 안전한 검증 불가 |
| 422 | `UNSUPPORTED_SYMBOL` | 활성 Master에 코드가 없음 |
| 422 | `SYMBOL_MISMATCH` | 코드의 공식 종목명과 요청 종목명이 다름 |

기존 멱등 결과 replay는 최초 처리 결과를 그대로 반환하고 Master 교체로 소급 재검증하지 않는다.
새 저장 row는 사용한 `symbol_master_version_id`를 보존한다.

## 고객 제보 기반 장애 의심 신호

`GET /api/signals/dashboard`는 고객 익명 Bearer token을 요구하며, 고객 제보로 감지한 활성
`SIGNAL_DETECTED | UNDER_REVIEW` 신호만 최신순으로 반환한다. `CANDIDATE`, 원문, 세션 digest,
주문 상세, embedding과 내부 유사도는 반환하지 않는다. 응답의 `official_incident`는 항상 `false`다.

응답은 조회 시각인 `updated_at`, 현재 노출 중인 신호 member를 report `received_at`의 UTC 시간 단위로
집계한 `hourly_volume`, 현재 활성 `applied_policy` snapshot을 포함한다. 정책 rolling window가 지난
`SIGNAL_DETECTED`는 조회에서 제외하지만, 사람이 검토를 시작한 `UNDER_REVIEW`는 자동으로 숨기지 않는다.
조회 rate limit은 익명 session digest와 비식별 client fingerprint 조합별 PostgreSQL 원자적 bucket으로
적용한다. 기본값은 60회/60초이며 환경변수로 조정하고 초과 시 `429`와 `Retry-After`를 반환한다.

`previous-window-distinct-sessions.v1` 기준선은 신호의 최신 `received_at`을 끝점으로 한 현재 rolling
window의 고유 세션 수를 바로 앞의 동일 길이 window 고유 세션 수로 나눈 값이다. 두 window가
정책 생성 이후에 온전히 존재하지 않으면 `INSUFFICIENT_HISTORY`, 직전 window가 0이면
`ZERO_BASELINE`, 그 외에는 `AVAILABLE`과 0 이상의 `baseline_ratio`를 반환한다. 규모 필드
`reporting_unique_sessions`와 기준선은 비식별 `session_digest`의 distinct count이며 실제 영향 고객
수가 아니다.

자동 편입 hard gate는 동일한 `channel + feature_area + issue_type`이다. 양쪽 값이 알려진
`submission_status` 또는 `error_code`가 충돌하면 같은 embedding이라도 합치지 않는다. 그 다음에만
활성 policy와 metadata가 같은 embedding의 cosine similarity를 비교한다. 시간창은 server UTC
`received_at` 기준 rolling window다.

정책은 `clustering_policies`에서 version 관리한다. AI 담당자가 전달한
`similarity_threshold=0.58`, average linkage, medoid는 현재 `EXPERIMENTAL` policy 값이다. `window_seconds=600`,
`min_unique_sessions=5`, `review_priority_threshold=10`도 법령·업계 표준이 아닌 MVP 실험값이다.
팀 승인 전에는 `APPROVED`로 표현하지 않는다. model revision을 포함한 metadata가 없으면 활성 policy를
임의 생성하지 않고 신호 processing job을 호출하지 않는다.

제보 확정 transaction은 `PENDING` signal processing job까지만 저장한다. embedding provider 호출은
transaction 밖에서 실행한다. metadata·차원·입력 불일치는 재시도해도 복구되지 않으므로 즉시
`DEAD_LETTER`로 보낸다. provider 일시 실패는 기본 최대 5회까지 `FAILED`로 재시도하고 소진되면
`DEAD_LETTER/RETRY_EXHAUSTED`가 된다. 기존 report와 consultation card는 롤백하거나 삭제하지 않는다.
재처리는 만료된 processing lease를 포함해 `SKIP LOCKED`로 한 job씩 점유하며, worker CLI는 해당
실행에서 실패 또는 dead-letter가 하나라도 발생하면 non-zero로 종료한다.

기존 운영자 mutation은 `OPERATOR` Bearer token과 UUID v4 `client_request_id`를 요구한다. 현재 조회 MVP
범위에서는 운영자 회원가입·로그인·인증 또는 mutation 흐름을 추가로 확장하지 않는다.

| Method | Path | 의미 |
|---|---|---|
| POST | `/api/operator/signals/acknowledge` | `SIGNAL_DETECTED`를 `UNDER_REVIEW`로 전이 |
| POST | `/api/operator/signals/close` | closure reason을 남기고 `CLOSED`로 전이 |
| POST | `/api/operator/signals/merge` | 구조화 hard gate가 호환되는 두 신호를 수동 병합 |
| POST | `/api/operator/signals/split` | 선택한 report membership을 새 신호로 분리 |
| POST | `/api/operator/signals/official-notice` | HTTPS 공식 공지 metadata 연결 |

공식 공지 연결은 상태가 아니며, `official_incident`를 자동으로 true로 바꾸지 않는다. 모든 mutation은
기존 idempotency record와 row lock을 사용하고 상태 전이·merge·split·공지 연결을 비식별
`signal_audit_events`에 기록한다.

고객 삭제와 72시간 purge는 report 삭제 transaction 안에서 membership·embedding을 함께 제거하고
distinct session count를 재계산한다. 기준 미달 신호는 `CLOSED/EVIDENCE_RECALCULATED`로 전이하며
삭제된 원문에서 유래할 수 있는 대표 증상 FK는 null 처리한다.
