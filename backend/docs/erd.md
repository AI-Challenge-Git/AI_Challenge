# MTS SOS Desk ERD

## 전체 MVP 논리 ERD

```mermaid
erDiagram
    POLICY_SNAPSHOTS ||--o{ REPORTS : governs
    REPORTS ||--o{ REPORT_ANALYSES : has_versions
    REPORTS ||--o| TECHNICAL_SYMPTOMS : confirms
    REPORTS ||--o| CONSULTATION_CARDS : creates
    TECHNICAL_SYMPTOMS ||--o{ TECHNICAL_EMBEDDINGS : embeds_later
    TECHNICAL_SYMPTOMS ||--o{ SIGNAL_MEMBERS : joins_later
    SIGNAL_CLUSTERS ||--o{ SIGNAL_MEMBERS : contains_later
    CLUSTERING_POLICIES ||--o{ SIGNAL_CLUSTERS : configures_later
    CONSULTATION_CARDS ||--o{ AGENT_VERIFICATIONS : verifies_later
    CONSULTATION_CARDS ||--o{ REFERENCE_LOOKUP_EVENTS : accessed_later
    APP_USERS ||--o{ USER_ROLE_ASSIGNMENTS : receives_later
    ROLES ||--o{ USER_ROLE_ASSIGNMENTS : grants_later
    APP_USERS ||--o{ AUDIT_LOGS : acts_later
    REPORTS ||--o{ EVALUATION_EVENTS : measures_later
```

## 현재 물리 ERD

```mermaid
erDiagram
    policy_snapshots ||--o{ reports : governs
    reports ||--o{ report_analyses : has_versions
    reports ||--o| technical_symptoms : confirms
    reports ||--o| consultation_cards : creates

    policy_snapshots {
        uuid id PK
        varchar version UK
        text source_url
        date source_checked_on
        jsonb content
        char content_sha256
        timestamptz created_at
    }

    reports {
        uuid id PK
        bytea session_digest
        uuid client_request_id
        uuid policy_snapshot_id FK
        varchar pii_policy_version
        text masked_text
        char request_payload_sha256
        varchar status
        timestamptz received_at
        timestamptz confirmed_at
        timestamptz updated_at
    }

    report_analyses {
        uuid id PK
        uuid report_id FK
        int version
        varchar schema_version
        varchar taxonomy_version
        varchar adapter_name
        varchar model_id
        varchar status
        jsonb technical_candidate
        jsonb consultation_candidate
        varchar safe_error_code
        timestamptz created_at
        timestamptz completed_at
    }

    technical_symptoms {
        uuid id PK
        uuid report_id FK,UK
        varchar taxonomy_version
        varchar channel
        varchar feature_area
        varchar issue_type
        varchar symptom
        varchar submission_status
        varchar error_code
        timestamptz reported_occurred_at
        timestamptz confirmed_at
    }

    consultation_cards {
        uuid id PK
        uuid report_id FK,UK
        varchar action
        varchar symbol_name
        varchar symbol_code
        bigint quantity
        varchar order_type
        bigint price_krw
        timestamptz attempted_at
        bytea reference_digest UK
        timestamptz expires_at
        uuid confirmation_request_id
        char confirmation_payload_sha256
        timestamptz created_at
        timestamptz updated_at
    }

    idempotency_records {
        uuid id PK
        bytea principal_digest
        varchar operation
        uuid client_request_id
        char payload_sha256
        int response_status
        timestamptz created_at
    }

    audit_logs {
        uuid id PK
        varchar actor_type
        varchar action
        char resource_fingerprint
        timestamptz created_at
    }
```

후속 migration이 추가될 때 이 문서에도 실제 반영된 엔터티와 관계를 누적한다. 아래 데이터
경계는 일정과 무관하게 유지한다.

## 데이터 경계

- `reports`에는 마스킹된 텍스트와 HMAC session digest만 저장한다.
- 검증된 AI 후보는 versioned `report_analyses`에만 JSONB로 저장한다.
- 확정 기술정보와 주문상담정보는 각각 `technical_symptoms`, `consultation_cards`로
  물리 분리한다.
- `technical_symptoms`에는 종목·수량·가격·매매 방향 컬럼이 없다.
- 참조번호 digest·TTL·삭제 멱등 기록·비식별 삭제 감사는 구현되어 있다.
- vector·군집·상담원 역할과 조회 감사는 후속 migration에서 추가한다.
