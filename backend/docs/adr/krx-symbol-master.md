# ADR: versioned KRX Symbol Master와 종목코드 계약

## 결정

- 원천은 KRX Data Marketplace `전종목 기본정보` CSV다.
- `시장구분`이 KOSPI·KOSDAQ·KOSDAQ GLOBAL이고 `주식종류=보통주`인 행만 적재한다.
- KOSDAQ GLOBAL은 서비스 시장을 KOSDAQ으로 정규화하되 원본 시장값을 보존한다.
- 종목명 기반 임의 필터는 사용하지 않는다.
- 종목코드는 원천의 접두 `A`가 제외된 단축코드이며 `^[0-9A-Z]{6}$`다. 숫자 6자리와 영문 포함
  보통주를 모두 허용하고 lowercase는 허용하지 않는다.
- 수집본은 기준일·원천 hash·encoding·schema version과 함께 immutable version으로 보존하고 한
  version만 활성화한다.
- 고객 최종 저장과 상담원 확인은 활성 version에서 코드 존재와 종목명·코드 일치를 검증하고 사용한
  version FK를 저장한다. null은 종목 미확정 상태로 허용한다.

## 적재와 실패 원자성

CSV 전체를 UTF-8-SIG, CP949 순으로 decode하고 필수 header, 중복, 코드 형식, 빈 이름과 대상 건수를
모두 검증한 뒤 PostgreSQL advisory transaction lock 안에서 새 version과 symbols를 저장하고 활성
version을 교체한다. 어느 행이든 실패하면 transaction 전체가 rollback되어 기존 활성 version은
유지된다. 같은 version과 동일 hash의 재실행은 idempotent하다.

## Migration과 rollback

이 revision은 기존 숫자 전용 상담 종목 CHECK를 대문자 영숫자 6자리로 확장하고 Master table과
검증 version FK를 additive하게 추가한다. 기존 숫자 코드는 새 제약의 부분집합이라 그대로 호환된다.
Master 데이터가 있거나 상담·확인 row에 영문 코드가 존재하면 downgrade를 거부한다. 운영 rollback은
먼저 참조 관계와 활성 Master 사용 여부를 확인해야 하며, 과거 수집본을 조용히 삭제하지 않는다.

## 경계

정규장·거래일·실시간 가격 검증은 포함하지 않는다. KRX Master는 공유 기준 데이터이므로 report의
72시간 purge에 포함하지 않는다.
