# 주문 방향·AI 제한·보존 기준 변경

## 상태

확정 — 2026-08-24

## 결정과 이유

- 실제 사용자 흐름이 매수와 매도를 모두 다루므로 주문 방향을 기존 `SELL`, `UNKNOWN`에서
  `BUY`, `SELL`, `UNKNOWN`으로 확장한다.
- 프론트, 백엔드와 AI 사이의 실패 시간을 예측할 수 있도록 백엔드 전체 AI 호출 제한을
  10초로 고정한다. blocking provider thread는 timeout으로 중단되지 않으므로 백엔드 service가
  동시 실행 수를 제한하고 adapter task가 실제 종료될 때 permit을 반환한다.
- 이미지 개인정보는 OCR로 판정하지 않는다. 사용자가 직접 가렸음을 확인한 multipart 요청만
  백엔드가 수락한다.
- 업무 데이터 최대 보존기간을 기존 2주에서 생성 후 72시간으로 단축한다. 접근 TTL 2시간과
  보존기간은 별개다.

## 데이터와 migration 영향

주문 방향 변경은 현재 Alembic head 뒤에 새 revision을 추가해
`consultation_cards.action` CHECK를 확장한다. 기존 `SELL`, `UNKNOWN` row는 변환 없이
호환된다. API enum과 ORM metadata도 같은 세 값만 허용한다.

72시간 purge는 이번 변경에 포함하지 않는다. 따라서 현재 DB row가 자동 삭제된다고 간주하면
안 되며, 별도 lifecycle vertical slice에서 attachment·멱등 기록·감사로그까지 함께 구현한다.

## rollback

애플리케이션을 이전 계약으로 되돌리기 전에 `BUY` row를 업무 절차에 따라 제거하거나 이관해야
한다. migration downgrade는 `BUY` row가 하나라도 있으면 명시적으로 실패해 데이터 손실을
막고, 없을 때만 기존 `SELL`, `UNKNOWN` CHECK를 복구한다. 72시간 보존 기준은 purge 구현 전
문서·운영 기준 변경만 되돌릴 수 있으며 데이터 복원은 보장하지 않는다.
