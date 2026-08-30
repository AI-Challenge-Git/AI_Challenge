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
| Signal worker | `python -m scripts.process_signal_jobs --max-jobs 100` | `*/5 * * * *` |
| Retention worker | `python -m scripts.purge_data --execute --batch-size 100` | `17 * * * *` |

`/backend/railway.json`은 API service에만 연결한다. Signal·Retention worker는 Root Directory만
`/backend`로 설정하고 Config File은 연결하지 않아 API용 pre-deploy migration을 반복 실행하지 않는다.
두 worker의 Start command와 Cron Schedule은 각 service 설정에서 위 표대로 지정한다.

Railway cron은 UTC 기준이며 5분보다 짧게 실행할 수 없다. 각 CLI는 한 bounded batch를 처리하고 DB
connection을 닫은 뒤 종료한다. 이전 실행이 끝나지 않았으면 다음 실행은 Railway가 건너뛴다.

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

Railway Bucket variable reference로 `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION`,
`ENDPOINT`를 주입하고 다음 값을 사용한다.

```text
ATTACHMENT_STORAGE_BACKEND=s3
ATTACHMENT_SIGNED_URL_TTL_SECONDS=300
S3_ADDRESSING_STYLE=virtual
```

기존 bucket의 Credentials 탭이 path-style을 요구할 때만 `S3_ADDRESSING_STYLE=path`로 바꾼다.

## Vercel and CORS

Vercel에는 공개값 `VITE_API_BASE_URL=https://<railway-api-domain>`만 설정한다. API의
`CORS_ORIGINS`에는 실제 production Vercel origin을 JSON 배열로 정확히 하나씩 등록한다. wildcard,
preview wildcard, cookie credential은 허용하지 않는다. production 설정은 정확한 HTTPS origin이
없으면 시작을 거부한다.

staging에서는 별도의 DB·Bucket·Secret을 사용하고 다음 순서로 확인한다: migration head, health,
KRX Master, 고객 이미지 제보, signal worker, dashboard, OPERATOR mutation, 상담원 signed URL,
고객 삭제, retention worker, 로그 secret 미노출. 실제 cloud 자원 생성과 Secret 등록은 사용자 승인
후 수행한다.
