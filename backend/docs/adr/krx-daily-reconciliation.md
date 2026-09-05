# ADR: KRX 상장정보 API 일일 보수적 동기화

## 결정

금융위원회 KRX 상장종목정보 API는 매일 최신 상장 snapshot을 확인하는 보조 원천으로 사용한다.
이 API에는 보통주 여부가 없으므로 기존 KRX `전종목 기본정보` CSV에서 확인된 보통주 allowlist를
확장하지 않는다.

- 기존 allowlist 코드가 API에 있으면 상장 확인일을 갱신한다.
- API에 처음 보이지 않은 코드는 새 Master에 유지하고 누락 시작일을 기록한다.
- 서로 다른 다음 기준일 snapshot에서도 보이지 않을 때만 새 Master에서 제외한다.
- CSV에서 확인됐던 제외 종목이 API에 다시 나타나면 같은 보통주 기준으로 자동 복구한다.
- API에만 있는 신규 코드는 종류를 추측하지 않고 자동 등록하지 않는다.
- 종목명 차이는 계수만 기록하며 CSV 이름을 유지한다. 시장 불일치는 전체 갱신을 거부한다.
- 전체 기존 종목의 99% 미만만 확인되면 부분 응답으로 보고 전체 갱신을 거부한다.

매 snapshot은 원 API 응답을 정규화한 SHA-256, 기준일, API URL, 부모 Master version과 함께 immutable
version으로 저장한다. 각 reconciliation은 직전 version과 기준 CSV version을 함께 참조한다.
advisory transaction lock 안에서 새 version을 완성한 후 활성 version을 교체하므로 실패하면 기존
Master가 유지된다.

## 운영

공식 데이터가 영업일 다음 날 오후 1시 이후 제공되는 점을 고려해 Railway cron을 매일
14:30 KST(`30 5 * * *` UTC)에 실행한다. 오늘 이전 14일 안에서 최신 제공 기준일을 찾으며 같은
snapshot 재실행은 idempotent하다. 인증키는 `KRX_LISTED_INFO_API_KEY` Secret으로만 주입한다.

## 제한과 후속 전환

신규 상장 보통주는 새 CSV에서 종류가 확인될 때까지 allowlist에 들어오지 않는다. 공식 CSV 자동
수집 원천이 준비되면 기존 CSV importer로 완전한 새 기준 version을 적재하고, 이후 API reconciliation은
그 version을 부모로 계속 수행한다.

## Migration과 rollback

Master version에 source 종류와 부모 version을, symbol row에 API 확인일과 누락 시작일을 additive하게
추가한다. API reconciliation version이 존재하면 provenance를 잃지 않도록 downgrade를 거부한다.
운영 rollback은 cron을 중지하고 직전 CSV 또는 검증된 부모 version을 새 활성 version으로 복구한 뒤
reconciliation version 정리 여부를 별도로 결정한다.
