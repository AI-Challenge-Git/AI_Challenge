# Backend

MTS SOS Desk의 FastAPI 백엔드입니다. Python 3.13과 uv를 사용합니다.

## 로컬 검증

```powershell
uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy app tests
uv run pytest -q
```

## Docker Compose 실행

저장소 루트에서 실행합니다.

```powershell
docker compose up --build
```

- API liveness: `http://localhost:8000/health/live`
- API readiness: `http://localhost:8000/health/ready`
- 제보 분석: `POST http://localhost:8000/api/reports/analyze`
- 고객 확인·상담카드 발급: `POST http://localhost:8000/api/reports`
- 미확정 제보 폐기: `DELETE http://localhost:8000/api/reports`
- 제보 전체 삭제: `DELETE http://localhost:8000/api/consultation-cards`
- 상담원 로그인: `POST http://localhost:8000/api/auth/login`
- 상담카드 목록: `GET http://localhost:8000/api/agent/consultation-cards`
- 상담카드 조회: `POST http://localhost:8000/api/consultation-cards/lookup`
- 상담원 재확인: `POST http://localhost:8000/api/consultation-cards/verifications`
- 장애 의심 신호 상황판: `GET http://localhost:8000/api/signals/dashboard`
- 운영자 신호 변경: `/api/operator/signals/*`

분석 API는 `pending`, `confirmation`, `failed`, `complete` 상태를 반환합니다. runtime adapter는
`AI_ADAPTER=openai`만 허용하며 실제 provider를 사용합니다. deterministic Fake는 dependency를
명시적으로 교체하는 테스트에서만 사용하며 runtime 설정으로 선택할 수 없습니다. 백엔드 전체 호출
timeout은 90초이고 동기 provider 호출은 기본 4개로 제한됩니다.

스크린샷 multipart 요청은 `screenshot_redacted_confirmed=true`가 필수입니다. 이미지가 없는
기존 JSON 요청에는 이 필드를 보내지 않습니다.

## 상담원 계정 provision

운영 환경에서는 계정을 자동 생성하지 않습니다. migration 후 아래 명령을 명시적으로 실행하면
계정을 만들거나 같은 사번의 기존 row를 갱신합니다. password 환경변수가 CLI 인자보다 우선하며
password와 hash는 출력하지 않습니다.

```powershell
cd backend
$env:AGENT_INITIAL_PASSWORD='<strong-random-password>'
uv run python -m scripts.seed_agent
Remove-Item Env:AGENT_INITIAL_PASSWORD
```

로그인 성공 응답의 opaque token을 이후 상담원 API의
`Authorization: Bearer <access_token>`으로 보냅니다. token MVP 기본 만료는 30분입니다.

## KRX 종목 Master 적재

KRX `전종목기본정보.CSV`는 UTF-8-SIG 또는 CP949로 읽고, `시장구분`이 KOSPI·KOSDAQ·
KOSDAQ GLOBAL이면서 `주식종류=보통주`인 행만 한 transaction으로 적재합니다. 이름을 이용한
임의 제외 규칙은 사용하지 않습니다. KOSDAQ GLOBAL은 검증 시장을 KOSDAQ으로 정규화하되 원본
시장값도 보존합니다.

```powershell
cd backend
uv run python -m scripts.import_krx_symbols "..\..\전종목기본정보.csv" --as-of 2026-08-28
uv run python -m scripts.sync_krx_symbols "..\..\전종목기본정보.csv" --as-of 2026-08-28
```

종목코드는 KRX 원천의 접두 `A`가 제외된 단축코드를 사용하며 정확히 6자리 대문자 영숫자
`^[0-9A-Z]{6}$`입니다. 따라서 `005930`과 `0011A0`이 모두 유효합니다. 중복 코드, 잘못된 코드,
빈 대상 데이터가 하나라도 있으면 새 version은 전혀 활성화되지 않습니다. 고객 최종 저장과 상담원
재확인은 활성 Master의 코드 존재 여부와 종목명·코드 일치를 검사합니다. Master 미적재는
`503 SYMBOL_MASTER_UNAVAILABLE`, 미지원 코드는 `422 UNSUPPORTED_SYMBOL`, 불일치는
`422 SYMBOL_MISMATCH`입니다. null은 종목 미확정 상태로 계속 허용합니다.

## 72시간 보존 정리

상담카드 상세 접근 TTL은 발급 후 2시간이고, report root와 관련 업무 데이터의 물리
보존기간은 서버 `received_at`부터 72시간입니다. 기본 명령은 대상 개수만 확인하는 dry-run이며
실제 삭제에는 `--execute`가 필요합니다.

```powershell
cd backend
uv run python -m scripts.purge_data
uv run python -m scripts.purge_data --execute --batch-size 100
```

출력은 report·독립 기록·object 처리 개수만 포함하며 참조번호, 입력값, object key는 출력하지
않습니다. 운영에서는 Railway scheduler가 위 `--execute` 명령을 단일 작업으로 주기 실행하게
연결해야 합니다. Railway service 설정과 private S3-compatible Object Storage 연결 방법은
[deployment 문서](docs/deployment.md)에 정리되어 있습니다.

로컬 Compose는 API 시작 전에 `alembic upgrade head`를 실행합니다. 운영 이미지의 기본
명령은 API만 시작하며 migration은 단일 pre-deploy 단계에서 별도로 실행해야 합니다.

OpenAPI와 프론트 생성 타입을 갱신하려면 다음을 실행합니다.

```powershell
cd backend
uv run python -m scripts.export_openapi openapi.json
cd ../frontend
npm run generate:api
```

프론트 파일을 변경하지 않고 계약 생성 가능 여부만 확인하려면 저장소 루트에서 다음처럼
임시 경로를 사용합니다.

```powershell
frontend\node_modules\.bin\openapi-typescript.cmd backend\openapi.json -o "$env:TEMP\mts-sos-api.ts"
```

핵심 업무 테이블과 개인정보 분리 원칙은 [ERD](docs/erd.md), 백엔드 API 기준안은
[API contract](docs/api-contract.md)에 정리되어 있습니다.
프론트 종목코드 입력 변경사항은 [frontend handoff](docs/frontend-handoff.md)에 정리되어 있습니다.

작업 종료 시 데이터 볼륨을 보존하도록 다음 명령을 사용합니다.

```powershell
docker compose down
```

`docker compose down -v`는 DB 데이터를 삭제하므로 사용하지 않습니다.

## 장애 의심 신호 policy

제보 확정 후 생성된 signal processing job은 `scripts.process_signal_jobs` worker가 처리합니다.
AI 담당과 합의한 embedding 계약은 1024차원·L2·cosine·`passage` 입력이며, 모델 revision은
`SIGNAL_EMBEDDING_MODEL_REVISION`으로 명시해야 합니다. revision을 추측하거나 자동 seed하지 않습니다.

Docker Compose의 `${...}` 값은 저장소 루트의 `.env`에서 읽습니다. `backend/.env`를 재사용하려면
모든 Compose 명령에 `--env-file backend/.env`를 붙여야 합니다. 실제 provider를 사용할 때는
`AI_ADAPTER=openai`, `OPENAI_API_KEY`, 확정된 `SIGNAL_EMBEDDING_MODEL_REVISION`을 같은 환경에서
주입하고 secret 값은 Git에 추가하지 않습니다.

AI 담당자가 전달한 `similarity_threshold=0.58`, average linkage, medoid 계약은
`scripts.register_signal_policy`로 immutable `EXPERIMENTAL` policy에 등록합니다.
`600초·5건·10건`도 법령이나 업계 표준이 아닌 MVP 실험값입니다.

```powershell
uv run python -m scripts.register_signal_policy `
  --policy-version signal-openai-embed-avg-medoid-baseline-v1 `
  --model-id text-embedding-3-small `
  --model-revision $env:SIGNAL_EMBEDDING_MODEL_REVISION `
  --dimension 1024 `
  --normalization L2 `
  --input-format passage `
  --taxonomy-version issue-type.v1 `
  --baseline-policy-version previous-window-distinct-sessions.v1 `
  --similarity-threshold 0.58 `
  --linkage-method AVERAGE `
  --representative-method MEDOID `
  --activate

uv run python -m scripts.process_signal_jobs --forever --max-jobs 100
```

배포 초기 데이터 등록 후 다음 read-only gate가 `runtime_ready=true`인지 확인합니다.

```powershell
uv run python -m scripts.check_runtime_readiness
```

기존 policy row는 immutable이므로 `baseline_policy_version=null`인 활성 policy를 migration에서
수정하지 않습니다. 배포 후 위와 같이 새 `policy-version`으로 등록·활성화해야 baseline이
`INSUFFICIENT_HISTORY`에서 실제 계산 상태로 전환됩니다.

`--activate`와 worker 시작 시 활성 policy의 model ID·revision·dimension·normalization·input
format·distance metric을 실제 embedding adapter 계약과 비교합니다. 불일치하면 provider 호출과 job
상태 변경 전에 non-zero로 종료하므로 policy와 환경변수를 먼저 맞춘 뒤 다시 실행합니다.

worker의 영구 오류는 즉시 `DEAD_LETTER`, 일시 오류는 `SIGNAL_WORKER_MAX_ATTEMPTS`까지 재시도한 뒤
`DEAD_LETTER/RETRY_EXHAUSTED`로 전이합니다. 한 건이라도 실패 또는 dead-letter 처리한 실행은
non-zero로 종료합니다. 공개 분석은 client fingerprint별 기본 5회/60초로 제한하고, 기본 180초보다
오래 갱신되지 않은 `PENDING`은 동일 report에서 재처리합니다.

provider timeout은 90초이며, timeout이 이미 실행 중인 thread를 중단하지 못하므로 adapter가 동시
provider thread 수를 제한합니다. 자세한 근거와 삭제 재계산 방식은
[incident signal ADR](docs/adr/incident-signal-policy.md)을 확인합니다.
