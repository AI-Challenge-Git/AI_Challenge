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

분석 API는 `pending`, `confirmation`, `failed`, `complete` 상태를 반환합니다. adapter 기본값은
deterministic Fake이며 `AI_ADAPTER=nvidia`일 때 실제 provider adapter를 사용합니다. 백엔드
전체 호출 timeout은 10초이고 동기 provider 호출은 기본 4개로 제한됩니다.

스크린샷 multipart 요청은 `screenshot_redacted_confirmed=true`가 필수입니다. 이미지가 없는
기존 JSON 요청에는 이 필드를 보내지 않습니다.

## 로컬 데모 상담원 계정

운영 환경에서는 데모 계정을 자동 생성하지 않습니다. migration 후 아래 명령을 명시적으로 한 번
실행하면 `CS1024 / demo` 계정을 만들거나 같은 사번의 기존 row를 갱신합니다. password 환경변수가
CLI 인자보다 우선하며 password와 hash는 출력하지 않습니다.

```powershell
cd backend
$env:DEMO_AGENT_PASSWORD='demo'
uv run python -m scripts.seed_agent
Remove-Item Env:DEMO_AGENT_PASSWORD
```

로그인 성공 응답의 opaque token을 이후 상담원 API의
`Authorization: Bearer <access_token>`으로 보냅니다. token MVP 기본 만료는 30분입니다.

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
연결해야 합니다. scheduler와 운영 Object Storage 연결 자체는 아직 구현하지 않았습니다.

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

작업 종료 시 데이터 볼륨을 보존하도록 다음 명령을 사용합니다.

```powershell
docker compose down
```

`docker compose down -v`는 DB 데이터를 삭제하므로 사용하지 않습니다.
