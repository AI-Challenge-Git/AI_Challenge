# ADR: 고객 제보 기반 장애 의심 신호

작성일: 2026-08-29

## 결정

서비스와 API는 고객 제보만으로 실제 전산장애·원인·영향을 확정하지 않는다. 외부에는
`고객 제보 기반 장애 의심 신호`만 제공하고 `official_incident=false`를 유지한다.

자동 군집의 순서는 다음과 같다.

1. server UTC `received_at` rolling window를 적용한다.
2. `channel + feature_area + issue_type`이 같은 제보만 후보로 삼는다.
3. 양쪽 값이 알려진 `submission_status` 또는 `error_code`가 충돌하면 후보에서 제외한다.
4. policy와 model metadata가 모두 같은 embedding끼리만 cosine similarity를 비교한다.
5. membership 편입, raw report 수, `COUNT(DISTINCT session_digest)`, 상태 전이를 한 transaction으로
   처리한다.

상태는 `CANDIDATE → SIGNAL_DETECTED → UNDER_REVIEW → CLOSED`다. `CANDIDATE`는 내부 전용이며
`CLOSED`에는 closure reason과 시간이 필수다. 공지 연결은 상태가 아니라 별도 nullable metadata다.

## 외부 근거와 프로젝트 실험값 구분

구조화 필드와 의미 유사도의 결합, 동일 제보자 반복 집계 제한, 내부 처리 상태와 공식 공지 분리,
개인정보 보유 목적 종료 후 삭제 원칙은 공식 규정·제품 문서에서 참고한 구조다.

반면 `600초`, `0.80`, `5개 고유 세션`, `10개 검토 우선순위`를 정답으로 뒷받침하는 공통 공식
기준은 없다. 네 값은 `EXPERIMENTAL` MVP 기본값일 뿐이다. 현재 저장소에는 same/different 한국어
금융 VOC 평가 데이터가 없으며, backend 규칙 검증용 최소 fixture만 존재한다.

## AI 경계

백엔드는 `model_id`, `model_revision`, `dimension`, `normalization`, `input_format`,
`distance_metric`이 policy와 일치하는 typed embedding 결과만 저장한다. 실제 모델 선택·로딩·embedding
생성·차원 결정·threshold 평가·singleton/medoid 품질 정책은 AI 담당 범위다.

AI metadata가 없을 때 정책을 추측하거나 자동 seed하지 않는다. 제보 확정 시 processing job만
`PENDING`으로 만들고 활성 policy가 생길 때까지 provider를 호출하지 않는다. provider 실패 또는
metadata 불일치는 job만 `FAILED`로 남기며 report와 consultation card는 보존한다.

pgvector column은 모델 미확정 상태를 지원하기 위해 dimensionless `vector`로 저장한다. 각 row의
dimension CHECK와 policy metadata 검증을 강제하며, 실제 dimension 승인 전에는 ANN index를 만들지
않고 exact scan을 사용한다.

## 동시성·삭제·보안

- processing worker는 `FOR UPDATE SKIP LOCKED`와 5분 lease로 여러 worker 간 job 중복 점유를 막는다.
- provider I/O 동안 DB transaction을 유지하지 않는다.
- 완료 단계는 hard gate별 PostgreSQL transaction advisory lock으로 동시 cluster 생성을 직렬화한다.
- `(signal_id, report_id)` UNIQUE가 동일 report의 중복 membership을 차단한다.
- 고객 삭제와 72시간 purge는 report 삭제 transaction 안에서 membership을 제거하고 cluster count를
  재계산한다. 기준 미달이면 `CLOSED/EVIDENCE_RECALCULATED`로 전이하고 대표 증상 FK를 null 처리한다.
- dashboard에는 `SIGNAL_DETECTED`, `UNDER_REVIEW`만 노출하며 원문·PII·session digest·주문 상세·vector를
  반환하지 않는다.
- 운영 mutation은 `OPERATOR` token, row lock, UUID v4 idempotency와 비식별 audit를 요구한다.

## Policy 등록

실제 AI metadata를 받은 뒤에만 다음 CLI로 immutable `EXPERIMENTAL` policy를 등록한다.

```powershell
uv run python -m scripts.register_signal_policy `
  --policy-version signal-exp-v1 `
  --model-id '<AI 담당 제공값>' `
  --model-revision '<AI 담당 제공값>' `
  --dimension '<AI 담당 제공값>' `
  --normalization L2 `
  --input-format '<AI 담당 제공값>' `
  --taxonomy-version '<AI 담당 제공값>' `
  --activate
```

`--activate`를 생략하면 등록만 하고 사용하지 않는다. policy version은 수정하지 않고 새 version으로
교체한다.

## Migration과 rollback

새 signal table은 현재 Alembic head 뒤에 additive하게 추가한다. 빈 table 상태에서는 downgrade/upgrade가
가능하다. policy, embedding, job, cluster, member 또는 signal audit가 하나라도 있으면 downgrade를
거부한다. 이미 purge된 report·membership·embedding은 downgrade로 복구할 수 없다.
