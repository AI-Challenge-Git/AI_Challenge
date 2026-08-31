# ADR: 고객 제보 기반 장애 의심 신호

작성일: 2026-08-29
갱신일: 2026-08-30

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

반면 `600초`, 유사도 임계값, `5개 고유 세션`, `10개 검토 우선순위`를 정답으로 뒷받침하는 공통
공식 기준은 없다. AI 담당자는 한국어 금융 VOC 평가 결과로 `0.58`, average linkage, medoid를
전달했다. 백엔드는 이 값을 immutable `EXPERIMENTAL` policy에 기록하며 법령·업계 표준 또는
`APPROVED` 값으로 표현하지 않는다.

## AI 경계

백엔드는 `model_id`, `model_revision`, `dimension`, `normalization`, `input_format`,
`distance_metric`이 policy와 일치하는 typed embedding 결과만 저장한다. 실제 모델 선택·로딩·embedding
생성·차원 결정·threshold 평가·singleton/medoid 품질 정책은 AI 담당 범위다.

현재 팀 계약은 `text-embedding-3-small`, 1024차원, L2 normalization, cosine distance,
`passage` 입력이다. provider model revision은 아직 환경변수로 명시적으로 받아야 하며 추측하지 않는다.
제보 확정 시 processing job만
`PENDING`으로 만들고 활성 policy가 생길 때까지 provider를 호출하지 않는다. provider 실패 또는
metadata 불일치는 job만 `FAILED`로 남기며 report와 consultation card는 보존한다.

pgvector column은 model version별 공존을 위해 dimensionless `vector`로 저장하고 각 row의 dimension
CHECK와 policy metadata 검증을 강제한다. 합의된 1024차원 L2·cosine row만 대상으로 full-precision
`vector(1024)` HNSW expression index를 둔다. 다른 model version과 dimension은 이 index에 섞지 않는다.
AI adapter는 provider의 `dimensions=1024` 요청과 L2 정규화를 적용하고 1024 평가 회귀 결과를
제공해야 한다.

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

AI 담당이 제공한 metadata와 별도로 받은 model revision을 사용해 다음 CLI로 immutable
`EXPERIMENTAL` policy를 등록한다.

```powershell
uv run python -m scripts.register_signal_policy `
  --policy-version signal-openai-embed-avg-medoid-v1 `
  --model-id text-embedding-3-small `
  --model-revision $env:SIGNAL_EMBEDDING_MODEL_REVISION `
  --dimension 1024 `
  --normalization L2 `
  --input-format passage `
  --taxonomy-version issue-type.v1 `
  --similarity-threshold 0.58 `
  --linkage-method AVERAGE `
  --representative-method MEDOID `
  --activate
```

`--activate`를 생략하면 등록만 하고 사용하지 않는다. policy version은 수정하지 않고 새 version으로
교체한다.

processing worker는 다음처럼 bounded batch로 실행한다. 실제 scheduler 연결은 배포 단계에서 한다.

```powershell
uv run python -m scripts.process_signal_jobs --max-jobs 100
```

worker는 provider I/O 동안 DB transaction을 유지하지 않으며 90초 timeout과 동시 thread 제한을
적용한다. `asyncio.to_thread()` timeout은 이미 실행 중인 provider thread를 중단하지 않으므로 slot은
실제 thread 종료 후에만 반환한다.

## Migration과 rollback

새 signal table은 현재 Alembic head 뒤에 additive하게 추가한다. 빈 table 상태에서는 downgrade/upgrade가
가능하다. policy, embedding, job, cluster, member 또는 signal audit가 하나라도 있으면 downgrade를
거부한다. 이미 purge된 report·membership·embedding은 downgrade로 복구할 수 없다.
