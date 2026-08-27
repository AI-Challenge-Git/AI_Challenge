# 주문 방향·AI 제한·보존 기준 변경

## 상태

확정 — 2026-08-24

## 결정과 이유

- 실제 사용자 흐름이 매수와 매도를 모두 다루므로 주문 방향을 기존 `SELL`, `UNKNOWN`에서
  `BUY`, `SELL`, `UNKNOWN`으로 확장한다.
- 변경된 AI 모델의 구조화 응답 시간을 수용하기 위해 백엔드 전체 AI 호출 제한을 90초로
  통일한다. 이 값은 provider 재시도를 포함한 adapter 전체 예산이다. blocking provider thread는
  timeout으로 중단되지 않으므로 백엔드 service가 동시 실행 수를 제한하고 adapter task가 실제
  종료될 때 permit을 반환한다.
- 이미지 개인정보는 OCR로 판정하지 않는다. 사용자가 직접 가렸음을 확인한 multipart 요청만
  백엔드가 수락한다.
- 업무 데이터 최대 보존기간을 기존 2주에서 생성 후 72시간으로 단축한다. 접근 TTL 2시간과
  보존기간은 별개다.

## 데이터와 migration 영향

주문 방향 변경은 현재 Alembic head 뒤에 새 revision을 추가해
`consultation_cards.action` CHECK를 확장한다. 기존 `SELL`, `UNKNOWN` row는 변환 없이
호환된다. API enum과 ORM metadata도 같은 세 값만 허용한다.

후속 lifecycle revision은 `reports.purge_at`을 nullable로 추가하고 기존 `received_at + 72시간`을
backfill·검증한 뒤 NOT NULL과 조회 index를 적용한다. 실패 멱등 기록에는 안전한 상태·code와
시각만 남기고, attachment object key는 report와 독립된 deletion job으로 이관한다. 자동 purge는
bounded batch와 PostgreSQL row lock·`SKIP LOCKED`를 사용하며 실제 object I/O는 DB transaction
밖에서 처리한다.

## rollback

애플리케이션을 이전 계약으로 되돌리기 전에 `BUY` row를 업무 절차에 따라 제거하거나 이관해야
한다. migration downgrade는 `BUY` row가 하나라도 있으면 명시적으로 실패해 데이터 손실을
막고, 없을 때만 기존 `SELL`, `UNKNOWN` CHECK를 복구한다. lifecycle revision downgrade는
미완료 object deletion job이 하나라도 있으면 거부한다. 먼저 purge CLI를 반복 실행해
`retry_waiting=0`을 확인해야 한다. 이미 물리 삭제된 report·분석·상담카드·감사·멱등 데이터와
object는 schema downgrade로 복원되지 않으므로, rollback이 필요한 운영 환경은 삭제 실행 전
별도 backup·restore 절차를 준비해야 한다.
