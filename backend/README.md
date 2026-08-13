# Backend

MTS SOS Desk의 FastAPI 백엔드입니다. Python 3.13과 uv를 사용합니다.

## 로컬 검증

```powershell
uv sync --frozen --dev
uv run pytest
uv run ruff check .
uv run mypy
```

## Docker Compose 실행

저장소 루트에서 실행합니다.

```powershell
docker compose up --build
```

- API liveness: `http://localhost:8000/health/live`
- API readiness: `http://localhost:8000/health/ready`

API 컨테이너는 시작할 때 `alembic upgrade head`를 실행합니다.

작업 종료 시 데이터 볼륨을 보존하도록 다음 명령을 사용합니다.

```powershell
docker compose down
```

`docker compose down -v`는 DB 데이터를 삭제하므로 사용하지 않습니다.

## 개발 경로

OneDrive 동기화 경로는 파일 잠금과 권한 충돌을 일으킬 수 있습니다. 2일차 작업 전
`C:\Users\hongh\src\AI_Challenge`처럼 동기화되지 않는 로컬 경로로 저장소를 옮기는 것을 권장합니다.
