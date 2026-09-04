# Backend deployment

Railway API service의 Root Directory는 `/backend`, Config File 경로는 `/backend/railway.json`으로
설정한다. Config File은 Root Directory를 자동으로 따라가지 않으므로 절대 경로를 별도로 지정해야
한다. API 시작 명령은 Dockerfile의 `uvicorn` CMD이며 Railway가 주입한 `PORT`를 사용한다. migration은
API process가 아니라 단일 pre-deploy command인 `alembic upgrade head`에서 실행한다. pre-deploy가
실패하면 새 API release를 시작하지 않는다.

## Required Railway services

같은 backend image를 다음 service에 재사용한다.

| Service | Start command | Schedule |
| --- | --- | --- |
| API | Dockerfile CMD | 상시 |
| Signal worker | `python -m scripts.process_signal_jobs --forever --max-jobs 100` | always-on, 5-second idle polling |
| Retention worker | `python -m scripts.purge_data --execute --batch-size 100` | `17 * * * *` |
| Operations monitor | `python -m scripts.check_operational_health` | every 5 minutes |

Signal worker is an always-on Railway worker, not a cron job. It polls every
`SIGNAL_WORKER_POLL_SECONDS` (default 5 seconds). Retryable provider failures remain queued with
backoff; configuration failures terminate the process so Railway can restart and alert it.

`/health/ready`는 DB 연결뿐 아니라 설정된 공식 안내 policy snapshot의 URL·content hash·필수 문구,
활성 signal policy와 runtime embedding 계약, 활성 KRX Symbol Master를 검사한다. 계정 provisioning은
API process 시작 전제는 아니므로 별도 gate에서 확인한다. migrations와 일회성 provisioning 이후 다음
read-only deployment gate를 실행한다.

```powershell
python -m scripts.check_runtime_readiness
```

Deployment is not ready until it reports an active signal policy matching the runtime embedding
contract, an active KRX Symbol Master, and at least one active AGENT and OPERATOR account.

## Signal policy rollout

canonical symptom처럼 embedding 입력 의미가 바뀌면 기존 policy row를 수정하지 않습니다. Signal
worker를 먼저 중지하고 AI 담당자가 확정한 새 model revision·taxonomy version과 threshold `0.56`으로
새 EXPERIMENTAL policy를 등록·활성화합니다. 그 다음 아래 명령을 결과의 `created`와 `reset`이 모두
0이 될 때까지 반복하고 worker를 다시 시작합니다.

현재 canonical symptom 계약의 taxonomy version은 `issue-type-canonical.v2`입니다. API extractor와
활성 signal policy의 taxonomy version이 다르면 정책 활성화, readiness, worker 시작이 모두 실패합니다.

```powershell
python -m scripts.requeue_signal_policy `
  --policy-version <new-policy-version> `
  --batch-size 100
```

이 명령은 보존기간 안의 확정 제보 중 새 policy의 taxonomy version과 일치하는 항목만 재처리합니다.
구 taxonomy의 자유형 symptom을 canonical 값으로 추측 변환하지 않습니다. 전환 후 공개 dashboard와
상담카드 관련 신호는 활성 policy만 사용하고, 구정책 cluster는 운영자 전체 목록에서만 조회됩니다.

`/backend/railway.json`은 API service에만 연결한다. Signal worker는
`/backend/railway.signal-worker.json`, Retention worker는
`/backend/railway.retention-worker.json`, Operations monitor는
`/backend/railway.operations-monitor.json`을 Config File로 지정한다. worker 설정에는 API용
pre-deploy migration이 없으며 bounded command와 UTC cron schedule만 포함한다.

Operations monitor는 signal ready backlog, 반복 실패, dead-letter, 최근 15분 AI provider 오류,
object deletion retry를 비식별 JSON 한 줄로 출력한다. 실패 상태가 있으면 non-zero로 종료한다. Railway
service failure notification을 이 cron service에 연결해야 실제 경보가 완성된다. 원문·token·object key와
provider 응답은 metric이나 log에 포함하지 않는다. 운영자는 `GET /api/operator/operations/metrics`로 같은
집계를 조회할 수 있다.

Railway cron은 UTC 기준이며 5분보다 짧게 실행할 수 없다. 각 CLI는 한 bounded batch를 처리하고 DB
connection을 닫은 뒤 종료한다. 이전 실행이 끝나지 않았으면 다음 실행은 Railway가 건너뛴다.
Signal worker는 영구 오류를 즉시 dead-letter 처리하고 일시 오류를
`SIGNAL_WORKER_MAX_ATTEMPTS`까지만 재시도한다. 해당 실행에서 실패 또는 dead-letter가 발생하면
non-zero로 종료하므로 Railway job 실패 알림을 연결해야 한다.

KRX 동기화는 금융위 상장정보 API만으로 보통주 여부를 확정할 수 없으므로 KRX `전종목기본정보.CSV`
원본도 필요하다. scheduler가 신뢰할 수 있는 CSV 원천을 받기 전에는 자동 등록하지 않는다. 원본을
준비한 뒤 다음 one-shot command를 매 영업일 API 갱신 이후 실행한다.

```powershell
python -m scripts.sync_krx_symbols 전종목기본정보.CSV --as-of YYYY-MM-DD
```

## Railway variables

API와 worker에는 `DATABASE_URL`, 서로 다른 네 HMAC key와 `OPENAI_API_KEY`를 Secret으로 설정한다.
`SIGNAL_EMBEDDING_MODEL_REVISION`은 재현성 metadata인 일반 환경변수로 명시한다. KRX worker에는
`KRX_LISTED_INFO_API_KEY` Secret을 추가한다. 값을 Git·Railway start command·로그에 넣지 않는다.
운영상황판 조회 제한은 기본 `SIGNAL_DASHBOARD_LIMIT=60`,
`SIGNAL_DASHBOARD_WINDOW_SECONDS=60`이며 트래픽 측정 후 환경변수로 조정한다.
공개 분석 제한은 기본 `REPORT_ANALYZE_LIMIT=5`, `REPORT_ANALYZE_WINDOW_SECONDS=60`이며 비식별
client fingerprint 단위다. `ANALYSIS_PENDING_STALE_SECONDS=180`은 `AI_TIMEOUT_SECONDS`보다 길게
유지하고, `SIGNAL_WORKER_MAX_ATTEMPTS=5`는 일시 provider 실패의 최대 시도 횟수다.
production의 client fingerprint는 Railway edge가 주입하는 유효한 `X-Real-IP`를 정규화한 뒤
HMAC 처리한다. 헤더가 없거나 IP 형식이 아니면 공용 fail-closed bucket으로 제한한다.
`APP_ENV=production`, `AI_ADAPTER=openai`를 명시한다. 네 HMAC key는 각각 32자 이상이어야 하며
서로 같은 값을 재사용하지 않는다. production은 확정된 `SIGNAL_EMBEDDING_MODEL_REVISION`, exact HTTPS
`CORS_ORIGINS`, S3-compatible private storage 설정이 하나라도 빠지면 시작을 거부한다.

Railway Bucket variable reference로 `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION`,
`ENDPOINT`를 주입하고 다음 값을 사용한다.

```text
ATTACHMENT_STORAGE_BACKEND=s3
ATTACHMENT_SIGNED_URL_TTL_SECONDS=300
S3_ADDRESSING_STYLE=virtual
```

기존 bucket의 Credentials 탭이 path-style을 요구할 때만 `S3_ADDRESSING_STYLE=path`로 바꾼다.

변수 연결 후 실제 private object write/read, signed GET, delete를 모두 검증한다. 이 명령은 임시
object를 항상 삭제하며 signed URL이나 credential을 출력하지 않는다.

```powershell
python -m scripts.verify_object_storage
```

상담원 계정은 migration에서 자동 생성하지 않는다. 배포 환경의 일회성 secret으로 provision하고
명령 종료 후 secret을 제거하거나 회전한다.

```powershell
$env:AGENT_INITIAL_PASSWORD='<strong-random-password>'
python -m scripts.seed_agent --employee-id CS1024 --agent-label 'CS1024 상담원' --role AGENT
Remove-Item Env:AGENT_INITIAL_PASSWORD
```

## Vercel and CORS

Vercel에는 공개값 `VITE_API_BASE_URL=https://<railway-api-domain>`만 설정한다. API의
`CORS_ORIGINS`에는 실제 production Vercel origin을 JSON 배열로 정확히 하나씩 등록한다. wildcard,
preview wildcard, cookie credential은 허용하지 않는다. production 설정은 정확한 HTTPS origin이
없으면 시작을 거부한다.

staging에서는 별도의 DB·Bucket·Secret을 사용하고 다음 순서로 확인한다: migration head, health,
KRX Master, 고객 이미지 제보, signal worker, dashboard, OPERATOR mutation, 상담원 signed URL,
고객 삭제, retention worker, 로그 secret 미노출. 실제 cloud 자원 생성과 Secret 등록은 사용자 승인
후 수행한다.

[Railway 공식 public networking 계약](https://docs.railway.com/networking/public-networking/specs-and-limits)은
`X-Real-IP`를 client remote IP header로 제공한다.
배포 E2E에서는 동일 외부 client가 서로 다른 가짜 `X-Real-IP` 값을 보낸 경우에도 rate-limit bucket이
우회되지 않는지 아래 명령으로 확인한다. `--limit`은 배포된 `SIGNAL_DASHBOARD_LIMIT`와 같아야 한다.
명령은 매번 새 익명 token을 메모리에서 만들어 `limit + 1`회 요청하고 token이나 IP를 출력·저장하지
않는다. 이 플랫폼 동작은 ASGI 단위 테스트만으로 완료 처리하지 않는다.

```powershell
python -m scripts.verify_proxy_rate_limit --base-url https://<railway-api-domain> --limit 60
```

## Signal policy approval

새 정책은 항상 `EXPERIMENTAL`로 등록한다. locked 평가 결과와 팀 승인이 끝난 뒤 산출물 원본의 SHA-256을
계산하고 OPERATOR token으로 `POST /api/operator/signal-policies/approve`를 한 번 호출한다. 요청에는
`policy_version`, `evaluation_artifact_sha256`, UUID v4 `client_request_id`를 넣는다. 서버는 상태 전이를
row lock으로 직렬화하고 승인자·승인시각·평가 hash와 비식별 audit를 저장한다. 동일 request replay는
같은 결과를 반환하고 다른 payload 재사용은 409다. locked 평가가 끝나지 않았으면 이 API를 호출하지
않는다.

## Backup, rollback, load and cost gates

CI는 빈 PostgreSQL migration, 최신 migration downgrade/upgrade, 전체 test, OpenAPI snapshot, frontend
build와 `pg_dump`/`pg_restore` 왕복을 매 변경에서 수행한다. 이는 ephemeral DB 복구 검증이며 Railway
운영 backup의 보존·암호화·복구시간을 증명하지 않는다. 배포 전 staging backup으로 별도 DB에 복구한 뒤
migration head와 비식별 row count를 확인하고, 직전 image와 migration downgrade 호환성을 rehearse한다.

실제 AI 부하·비용은 staging의 합성 데이터로 동시성 1/4/8, p50/p95 latency, timeout/provider error,
제보당 extraction·embedding 호출 수와 provider 청구 usage를 기록한다. 실제 secret·유료 호출이 필요하므로
자동 CI에서는 실행하지 않으며 측정 결과와 허용 budget이 없으면 production capacity 완료로 표시하지 않는다.
