# 상담원 인증·카드 접근·조회 제한

## 상태

확정 — 2026-08-26

## 결정

- 데모 상담원 password는 `pwdlib`의 권장 Argon2 설정으로 hash한다. migration이나 source에 데모
  password와 고정 account hash를 넣지 않고 명시적인 seed CLI로만 만든다.
- access token은 CSPRNG 256-bit opaque 값이며 응답에서 한 번만 평문으로 반환한다. DB에는 고객
  session·참조번호와 다른 전용 key의 HMAC-SHA256 digest만 저장한다.
- access token TTL은 환경설정 가능하며 MVP 기본값은 30분이다. refresh token과 실제 금융 인증은
  범위 밖이다.
- 상담원 역할은 `AGENT`, `OPERATOR`를 저장할 수 있지만 현재 카드 API는 `AGENT`만 허용한다.
- 카드 목록은 opaque `card_id`를 사용한다. 참조번호는 발급 이후에도 평문 사본을 만들지 않는다.
- 카드 상세와 verification은 2시간 TTL을 강제한다. 만료 카드는 report의 72시간 물리 삭제 전까지
  목록에만 회색 표시용 상태로 남는다.
- 로그인 실패와 lookup 제한은 PostgreSQL bucket의 원자적 upsert를 사용한다. employee·agent와
  client 주소는 전용 rate-limit key로 HMAC fingerprint한 값만 저장한다.
- 실패 지연은 비동기 sleep이며 기본 로그인 300ms, lookup 250ms다. 제한값·window·지연은
  환경설정으로 바꿀 수 있다.
- audit에는 내부 agent UUID, 안전한 event/outcome과 비식별 resource fingerprint만 저장한다.
  password, token/digest, 참조번호/digest, raw IP, 주문 상세와 object key는 저장하지 않는다.

## 데이터와 migration 영향

새 additive revision은 `agent_accounts`, `agent_access_tokens`, `agent_verifications`,
`rate_limit_buckets`를 만들고 기존 `audit_logs`에 nullable `actor_id`와 안전한 `outcome`을 추가한다.
verification은 consultation card 삭제에 `CASCADE`되어 report root 삭제와 72시간 purge에 함께
제거된다. 만료 token과 rate-limit bucket은 purge service·CLI의 독립 기록 정리에 포함한다.
agent account 자체는 report 보존정책으로 삭제하지 않는다.

## 보안·운영 경계

존재하지 않는 employee ID도 process별 dummy Argon2 hash를 검증해 잘못된 password와 외부 오류 및
주요 CPU 경로를 맞춘다. raw client 주소는 요청 메모리에서 fingerprint 생성에만 사용한다.
development/test는 ASGI `request.client.host`, production은 Railway edge가 주입하는 유효한
`X-Real-IP`를 사용한다. 누락·형식 오류는 공용 fail-closed bucket으로 제한하고 spoof header overwrite는
배포 E2E에서 검증한다.

attachment는 운영 signed URL이 없으므로 상담원 응답에 URL을 넣지 않고 `has_attachment`만
제공한다. 장애 신호 vertical slice가 없으므로 `related_signals=[]`이며 similarity를 만들지 않는다.

## rollback

빈 agent table에서는 downgrade/upgrade 왕복이 가능하다. 계정·token·verification·rate bucket 또는
agent audit event가 있으면 downgrade를 명시적으로 거부한다. 필요한 audit를 export하고 token과
업무 row를 명시적으로 정리한 뒤에만 schema rollback을 수행한다. report purge로 이미 물리 삭제된
verification은 migration downgrade로 복구되지 않는다.
