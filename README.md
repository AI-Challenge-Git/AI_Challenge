# MTS SOS Desk

MTS SOS Desk는 고객의 MTS 오류 제보를 AI로 구조화하고, 비식별 제보를 장애 의심 신호로 묶어
상담원과 운영 상황판에 연결하는 증권사 도입형 웹서비스 MVP입니다.

AI는 장애를 공식 확정하거나 주문을 실행하지 않습니다. 실제 주문 상태는 반드시 증권사의 공식
채널에서 다시 확인해야 합니다.

## 핵심 흐름

### 고객 제보

1. 고객이 20~500자의 오류 상황과 선택적 스크린샷을 제출합니다.
2. 전화번호·계좌번호·이메일 등은 마스킹하고, 주민등록번호·비밀번호·OTP가 포함된 요청은 거부합니다.
3. 스크린샷은 고객이 민감정보를 직접 가렸다고 확인한 경우에만 전송합니다.
4. AI가 기술 증상과 개인 주문 상담정보를 분리하고, 고객이 결과를 수정·확정합니다.
5. 확인된 제보에 2시간 유효한 상담 참조번호를 발급합니다.

### 상담원

- Bearer token 로그인 후 조회 가능한 상담카드 목록을 확인합니다.
- 고객 확인값과 주문 내역 재확인값의 일치·누락·중요 불일치를 표시합니다.
- signed URL로 첨부 이미지를 확인하며, URL 만료 시 카드 상세를 다시 조회합니다.
- 관련 장애 신호의 AI 관련성·확인 질문을 보고 `관련 있음`, `관련 없음`, `확인 보류`를 저장합니다.
- 서버의 `OPEN | VERIFIED`, `expired`, `can_open` 상태만 사용하며 브라우저에 가짜 완료 상태를 저장하지 않습니다.

### 운영 상황판

- `SIGNAL_DETECTED | UNDER_REVIEW` 상태의 활성 장애 의심 신호만 표시합니다.
- 시간대별 제보량, 비식별 제보 세션 수, 적용 중인 실험 정책을 확인합니다.
- `official_incident=false`인 신호를 공식 장애나 실제 피해 고객 수로 표현하지 않습니다.
- `429` 응답 시 `Retry-After` 이후에만 다시 조회합니다.

## MVP 범위

- KB증권 M-able 국내주식 매수·매도 오류 제보
- 텍스트·이미지 제보, AI 구조화, 개인정보 보호
- PostgreSQL·pgvector 기반 의미 임베딩과 장애 의심 신호 생성
- 상담카드 조회·재확인·관련 신호 확인
- 운영 상황판 조회
- issue type·임베딩·군집화 평가 도구

## 기술 구성

- Frontend: React, Vite, TypeScript, OpenAPI generated types
- Backend: Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic
- AI: OpenAI 구조화 추출 및 임베딩
- Database: PostgreSQL 16, pgvector
- Attachment: 개발용 local volume, 운영용 private S3-compatible Object Storage
- Deployment: Vercel frontend, Railway API·signal worker·retention/KRX maintenance worker

## 저장소 구조

```text
frontend/                     React 웹 애플리케이션
backend/                      FastAPI·AI·DB·worker
backend/openapi.json          프론트–백엔드 API 계약의 단일 원본
backend/docs/                 API·정책·배포 문서
backend/railway*.json         Railway API·worker 설정
compose.yaml                  로컬 PostgreSQL·API 환경
```

백엔드 실행과 검증 방법은 [backend/README.md](backend/README.md)를 참고하세요.
