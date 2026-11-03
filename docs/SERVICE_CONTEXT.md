# ChungnamOn 서비스 컨텍스트

> Claude Code가 이 레포에서 작업할 때 참고하는 서비스 요약 문서다.
> 원본: Notion "그려톤 기능 명세서" (Manyfast PRD · 요구사항 6개 · 기능 17개 기준, 2026-11-02)
> 팀 결정 반영: 2026-11-03 (§10)
> 해커톤: 그려톤 2026-11-21

---

## 1. 서비스 개요

- **이름**: ChungnamOn — 충남 외국인 주민 행정문서 해석·안내 서비스
- **한 줄 설명**: 충남 거주 외국인이 받은 한국어 행정문서(고지서·안내문·민원서류)를 사진/PDF로 올리면, 모국어로 해석하고 충남·중앙정부 공식 자료와 대조해 출처와 함께 보여주며, 체크리스트·마감 알림·문서 챗봇으로 실제 처리까지 돕는다.
- **대상 지역**: 충청남도 전체 (15개 시·군). **지역 선택 기능 없음.** 담당기관은 문서의 **발신기관** 기준으로 찾고, 공식 자료 검색 범위는 충남 + 중앙정부로 고정.
- **사용자 역할**
  - 외국인 주민 사용자: 문서 업로드, 결과 확인, 체크리스트, 일정, 챗봇
  - 관리자: 공식 자료 출처·최신성 관리 (Django admin으로 대체 가능)
- **디바이스**: 모바일(PWA) + 웹

### 핵심 원칙 (모든 기능 공통)
1. 근거는 충청남도, 충남 15개 시·군, 중앙정부의 **공식 자료로만** 제한한다.
2. 근거가 없으면 단정하지 않고 `UNVERIFIED`(미확인), 근거가 충돌하면 `MISMATCH`(불일치)로 표시한다.
3. 모든 근거에 **출처와 확인일**을 붙인다.
4. 결과는 참고용이다. 접수·납부 대행, 법적 효력 판정, 자격 확정은 하지 않고 최종 확인은 담당기관으로 연결한다.
5. 업로드 문서의 개인정보(외국인등록번호·주소 등)는 마스킹한다.

### 대표 시나리오
아산시 거주 베트남 근로자가 주민세 고지서를 촬영해 업로드 → 베트남어 번역·요약·용어·마감일(8/31) 확인 → 아산시 공식 자료와 대조한 항목별 상태·출처·확인일 확인 → 담당기관·준비물 확인, 체크리스트 사용 → 마감일 일정 등록 → "외국인등록증이 없으면 어떻게 내나요?" 챗봇 질문 → 마감 7일 전·1일 전 알림 → 납부 후 체크리스트 완료.
예외: 사진이 흐리면 재촬영 안내, 공식 자료가 없거나 다르면 미확인·불일치와 담당기관 연락처 표시.

---

## 2. 기술 스택과 구조

| 항목 | 내용 |
| --- | --- |
| 프론트 | React + Vite + TypeScript (PWA), Vercel — https://chungnam-on.vercel.app |
| 백엔드 | Django + DRF, simplejwt, Railway |
| DB | PostgreSQL (Railway), 벡터 검색은 pgvector 예정 |
| 레포 | github.com/cha771133-sys/ChungnamOn (monorepo: `frontend/`, `backend/`) |

**Django 앱**

| 앱 | 역할 |
| --- | --- |
| `accounts` | 회원가입·로그인·JWT·프로필, 커스텀 User(`accounts.User`) |
| `documents` | 문서 업로드, 비동기 처리 상태, 해석 결과, 교차 검증 결과, 체크리스트, 일정, 문서함 |
| `chatbot` | 문서 챗봇 질문·답변 저장, 대화 이력 |
| `sources` | 공식 자료 수집·저장(원본·청크·로그), 담당기관 |

**환경변수 (settings.py가 읽는 값)**: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_ORIGIN_REGEXES`, `DATABASE_URL`

**API 응답 키 형식**: camelCase. `djangorestframework-camel-case`를 `REST_FRAMEWORK` 전역 설정으로 적용한다 (B가 설정). 설정 전까지 각 앱 코드(serializer 필드 등)는 snake_case로 작성하고, 전역 설정이 들어오면 자동 변환된다. 이 문서의 JSON 예시는 최종 형태(camelCase)다.

---

## 3. 백엔드 / AI 역할 경계

| 백엔드 | AI 담당 |
| --- | --- |
| API, 모델(DB), 권한, 비동기 작업 큐, 상태 관리 | OCR, LLM 해석(번역·요약·용어·기한·발신기관 추출) |
| 공식 자료 수집 스크립트, 원본·메타데이터 저장 | 청크 분할, 임베딩, 벡터 검색 |
| 검증 결과·챗봇 대화 저장과 조회 API | 일치·불일치·미확인 판단, 챗봇 답변 생성 |

**규칙**: AI가 들어갈 자리(OCR, 마스킹, 해석, 검증, 챗봇 답변, 체크리스트 자동 생성)는 **더미 함수로 먼저 구현**한다. 더미가 실제와 같은 형식으로 데이터를 채우게 해서 프론트가 바로 연동할 수 있게 하고, AI 모듈이 준비되면 함수만 교체한다.

---

## 4. 요구사항과 기능 (R1~R6)

### R1. 다국어 행정문서 해석 및 개인정보 보호
| 기능 | 핵심 동작 | 백엔드 규칙 |
| --- | --- | --- |
| F1-1 업로드 및 해석 요청 | 사진/PDF 업로드 → 해석 처리 시작 | 형식 jpg·jpeg·png·pdf, 최대 20MB, 문서 1개/요청, 중복 접수 방지, 업로드 즉시 `PENDING` 반환 |
| F1-2 해석 결과 확인 | 번역, 핵심 요약, 행정용어 설명, 기한·발신기관·요청사항 | 추출 항목마다 `extracted`(bool)·`confidence`(0~1), 추출 못 한 값은 "확인되지 않음"으로 표시, 참고용 문구 |
| F1-3 낮은 OCR 신뢰도 재업로드 | 신뢰도 낮으면 재촬영·재업로드 안내 | 핵심 항목(기한·금액·기관명) 신뢰도 기준, 문서 단위 `lowConfidence`(bool), 낮은 값은 확정 정보로 내보내지 않음, 재업로드 파일을 원문서에 연결 |
| F1-4 개인정보 마스킹·보관 | 등록번호·주소 마스킹, 원본 보관 제한 | 마스킹 실패 시 결과 차단(`FAILED`), 저장 데이터 암호화, 원본 업로드 후 30일 자동 삭제 |

### R2. 공식 행정정보 교차 검증 및 출처 제공
| 기능 | 핵심 동작 | 백엔드 규칙 |
| --- | --- | --- |
| F2-1 항목별 교차 검증 | 해석 항목을 공식 자료와 대조 | 항목 상태 `MATCHED`/`MISMATCH`/`UNVERIFIED`, 문서 전체 `overall`은 여기에 `PARTIAL` 추가, 발신기관·지역 식별 실패 시 `UNVERIFIED`, 같은 문서 검증 중복 실행 금지, 실패 시 재시도 |
| F2-2 근거·최신성 조회 | 근거 자료의 출처·확인일·기준일 조회 | 확인일 = 서비스가 자료를 수집한 시각(`collected_at`), 기준일(`published_at`)은 있을 때만, 오래된 자료 기준은 운영자 설정 |

### R3. 행정 절차 행동 가이드 및 체크리스트
| 기능 | 핵심 동작 | 백엔드 규칙 |
| --- | --- | --- |
| F3-1 체크리스트 생성 | 준비서류·수행 단계·기한·납부 방법 항목화 | 확정 못 하는 항목은 `NEEDS_CHECK`, 반복 생성 시 중복 항목 금지, 항목에 근거 참조(`sourceIds`) 저장 |
| F3-2 진행 상태 관리 | 항목 완료/미완료, 전체 진행률 | 전체 완료는 모든 항목 완료 시에만, 기한 지난 미완료 항목은 `overdue`, 동시 수정은 최신 저장 반영 |
| F3-3 담당기관·개인 적용 안내 | 기관명·연락처·확인할 사항 | 발신기관 기준, 적용 상태 `CONFIRMED`/`CONDITIONAL`/`NEEDS_CHECK`/`MISMATCH`, 못 찾으면 미확인 |

### R4. 마감일 일정 등록 및 알림
| 기능 | 핵심 동작 | 백엔드 규칙 |
| --- | --- | --- |
| F4-1 마감일 일정 등록 | 확인된 마감일을 일정으로 등록 | 추출·확인된 마감일만 등록, 같은 문서·같은 마감일 중복 금지, 문서·체크리스트·기관 연결 |
| F4-2 일정·마감 상태 관리 | 목록·상세·완료 처리 | 현재 시각 기준 기한 경과(`overdue`) 갱신, 완료 전까지 경과 상태 유지 |
| F4-3 마감 사전 알림 | 마감 7일 전·1일 전 푸시 | 알림 ON 사용자만, 완료된 일정 제외, 소급 발송 금지, 중복 발송 금지, 발송 결과 기록 (FCM) |

### R5. 공식 근거 기반 문서 질의응답 (챗봇)
| 기능 | 핵심 동작 | 백엔드 규칙 |
| --- | --- | --- |
| F5-1 문서 맥락 질문·답변 | 해석 완료 문서에 자연어 질문 | 답변 생성은 AI. 백엔드는 API·권한·저장·이력. 질문마다 별도 레코드 |
| F5-2 답변 근거·미확인 상태 | 출처와 근거 여부 표시 | `grounded=false`면 담당기관 안내 함께 반환 |

### R6. 계정 관리 및 문서
| 기능 | 핵심 동작 | 백엔드 규칙 |
| --- | --- | --- |
| F6-1 회원가입·로그인·초기 프로필 | 이메일+비밀번호, 프로필 설정 | 안내 언어·국적·체류자격 세 항목 필수, 프로필은 이후 수정 가능, 자격 판정 근거 아님 |
| F6-2 내 문서함 | 내가 올린 문서 목록·상태·다시 보기 | 처리 중·실패 상태 구분 표시 |
| F6-3 공식 자료 수집·갱신 관리 (관리자) | 자료 출처·확인일·갱신 상태 관리 | 수집 실패 시 기존 데이터 유지, 출처 접근 불가 시 갱신 완료로 표시하지 않음 |

### 공통 권한 규칙
- 로그인 사용자는 **본인 문서와 그에 연결된 데이터만** 조회·수정 (남의 것은 404)
- 비로그인 사용자는 개인 데이터 접근 불가 (401)
- 관리자의 사용자 문서 접근은 마스킹된 결과만, 감사 로그 포함

---

## 5. 데이터 흐름과 API 형식

```
① 온보딩 → ② 문서 업로드 → ③ 해석 결과 조회 → ④ 체크리스트 → ⑤ 일정·알림
                                   └→ ⑥ 문서 챗봇
```

### ① 회원가입·온보딩
- FE → BE: 이메일·비밀번호, 사용 언어, 국적, 체류자격
- BE → FE: JWT(access·refresh), 프로필
```json
{ "language": "vi", "nationality": "VN", "visaType": "E-9" }
```

### ② 문서 업로드 (비동기)
- FE → BE: 파일(multipart)
- BE → FE: 즉시 `{"documentId": 123, "status": "PENDING", "step": "QUEUED"}`
- BE 내부: 파일 저장 → 작업 큐 → OCR → 마스킹 → LLM 해석 → 발신기관 추출 → 공식 자료 RAG 교차 검증
- FE는 2~3초마다 상태 폴링

| 필드 | 값 |
| --- | --- |
| `status` | `PENDING` / `PROCESSING` / `DONE` / `FAILED` |
| `step` | `QUEUED` → `OCR` → `MASKING` → `INTERPRETING` → `VERIFYING` → `DONE` |

- 실패하면 `status`가 `FAILED`가 되고, `step`은 실패한 단계에 머문다.

```json
{ "documentId": 123, "status": "PROCESSING", "step": "INTERPRETING" }
```

### ③ 해석 결과 조회
```json
{
  "documentId": 123,
  "docType": "고지서",
  "title": "주민세 납부 안내",
  "lowConfidence": false,
  "originalText": "…(마스킹된 원문)…",
  "translatedText": "…(모국어 번역)…",
  "summary": ["8월 31일까지 주민세 6,250원 납부"],
  "terms": [{ "word": "납부기한", "explain": "돈을 내야 하는 마지막 날" }],
  "deadlines": [
    { "label": "납부 마감", "date": "2026-08-31", "extracted": true, "confidence": 0.94 }
  ],
  "amount": { "value": 6250, "extracted": true, "confidence": 0.91 },
  "requiredDocs": ["고지서", "외국인등록증"],
  "agency": {
    "name": "아산시청 세무과",
    "phone": "041-…",
    "source": "document",
    "extracted": true,
    "confidence": 0.88
  },
  "verification": {
    "overall": "PARTIAL",
    "items": [
      {
        "id": 1,
        "field": "deadline",
        "value": "2026-08-31",
        "status": "MATCHED",
        "sources": [
          {
            "title": "아산시 주민세 납부 안내",
            "url": "…",
            "agency": "아산시",
            "checkedAt": "2026-10-30",
            "publishedAt": "2026-07-01"
          }
        ]
      },
      { "id": 2, "field": "amount", "value": "6250", "status": "UNVERIFIED", "sources": [] }
    ]
  }
}
```
- 추출 항목(기한·금액·발신기관)은 값마다 `extracted`(bool), `confidence`(0~1)를 붙인다. `extracted=false`면 프론트는 "확인되지 않음"으로 표시한다.
- `lowConfidence`(문서 단위): 핵심 항목 신뢰도가 기준 미만이면 `true` → 프론트가 재촬영·재업로드 안내. 기준값은 AI와 합의.
- `agency.source`: `document`(문서에서 추출) / `official`(공식 자료에서 확인) / `unknown`(식별 실패). 사용자 프로필이 아니라 문서의 발신기관 기준.
- `verification.overall`: `MATCHED`(전부 일치) / `PARTIAL`(일부만 일치) / `MISMATCH`(불일치 있음) / `UNVERIFIED`(근거 없음). 프론트는 이 값으로 경고 배지를 표시한다.
- `verification.items[].status`: `MATCHED` / `MISMATCH` / `UNVERIFIED`
- `sources[].publishedAt`은 기준일이 있을 때만, 없으면 `null`.

### ④ 체크리스트
```json
{
  "documentId": 123,
  "progress": 50,
  "items": [
    {
      "id": 1,
      "text": "외국인등록증 준비",
      "order": 1,
      "done": true,
      "confirmStatus": "CONFIRMED",
      "dueDate": null,
      "overdue": false,
      "requiredDocs": ["외국인등록증"],
      "sourceIds": [1]
    },
    {
      "id": 2,
      "text": "은행 또는 위택스에서 납부",
      "order": 2,
      "done": false,
      "confirmStatus": "NEEDS_CHECK",
      "dueDate": "2026-08-31",
      "overdue": false,
      "requiredDocs": ["고지서"],
      "sourceIds": []
    }
  ]
}
```
- `confirmStatus`: `CONFIRMED`(확인됨) / `CONDITIONAL`(조건부) / `NEEDS_CHECK`(확인 필요) / `MISMATCH`(불일치)
- `overdue`: `dueDate`가 지났고 `done=false`이면 `true`
- `sourceIds`: 근거로 쓴 검증 항목·공식 자료 참조
- FE → BE: 항목 체크·해제, 직접 추가·수정·삭제

### ⑤ 일정·알림
- FE → BE: 일정 등록(③의 `deadlines`에서 선택), 알림 시점(D-7, D-1), FCM 토큰
- BE → FE: 일정 목록(다가오는 순)
```json
{
  "id": 7,
  "documentId": 123,
  "title": "주민세 납부 마감",
  "dueDate": "2026-08-31",
  "done": false,
  "overdue": false,
  "checklistId": 1,
  "agencyName": "아산시청 세무과"
}
```
- 스케줄러가 알림 시점에 FCM 푸시 (P3)

### ⑥ 문서 챗봇
```json
// 요청
{ "documentId": 123, "message": "외국인등록증이 없으면 어떻게 내나요?" }
// 응답
{
  "chatId": 45,
  "answer": "고지서에 있는 가상계좌로 입금하면 …",
  "sources": [{ "title": "아산시 지방세 납부 방법", "url": "…", "checkedAt": "2026-10-30" }],
  "grounded": true,
  "suggestedQuestions": ["기한이 지나면 어떻게 되나요?", "분할 납부가 되나요?"]
}
```

### 화면별 데이터
| 화면 | 보내는 것 | 받는 것 |
| --- | --- | --- |
| 온보딩 | 언어, 국적, 체류자격 | 토큰, 프로필 |
| 업로드 | 파일 | documentId, 상태 |
| 분석 중 | documentId (폴링) | status, step |
| 해석 결과 | documentId | 번역, 요약, 용어, 마감일, 금액, 서류, 발신기관, 검증 항목·출처 |
| 문서 챗봇 | documentId, 질문 | 답변, 출처, 추천 질문, 대화 이력 |
| 체크리스트 | 체크·수정 | 항목, 진행률 |
| 캘린더 | 일정 등록, 알림 설정 | 일정 목록 |
| 내 문서함 | (없음) | 문서 목록·상태 |

---

## 6. 공식 자료 수집 (sources)

교차 검증과 챗봇이 참고할 공식 자료 DB를 **미리** 만들어 두는 백엔드 기능. 사용자 요청 시에는 검색만 한다.

```
① 수집 → ② 정제 → ③ 청크 분할 → ④ 메타데이터 → ⑤ 임베딩 → ⑥ 벡터 DB 저장
                                              ↑ ⑦ 주기적 갱신
```
- 백엔드: ①②④⑦, 테이블 / AI: ③⑤ 검색 튜닝

| 출처 | 방식 |
| --- | --- |
| 공공데이터포털 (data.go.kr) | Open API |
| 국가법령정보센터 (법제처, 자치법규) | Open API |
| 충남도청·15개 시군 홈페이지 | 크롤링 (robots.txt·약관 확인) |
| 중앙부처 안내 페이지 | 크롤링 또는 수동 |

**테이블 (안)**
| 테이블 | 주요 컬럼 |
| --- | --- |
| `source_document` | id, source_url, title, agency, region, category, published_at, collected_at, content_hash, status |
| `source_chunk` | id, source_document_id, chunk_text, chunk_order, embedding |
| `crawl_log` | id, source_url, started_at, finished_at, result, error_message |

- 갱신 시 `content_hash` 비교로 바뀐 것만 재처리, 삭제된 페이지는 폐기 상태로 검색 제외, 실패 시 기존 데이터 유지

**해커톤 축소안**: 문서 유형 2~3개(주민세 고지서, 건강보험료 안내문 등), 지역은 천안·아산 + 충남도청 + 중앙정부 공통, 수동 URL 목록으로 수십 페이지, 갱신은 수동 실행 버튼.

---

## 7. 백엔드 작업 우선순위와 분담

| 순위 | 작업 | 담당 |
| --- | --- | --- |
| **P0** 1 | 회원가입·로그인, JWT, 프로필, `accounts.User` | B |
| **P0** 2 | 문서 업로드 API, Document 모델 | **A** |
| **P0** 3 | 비동기 처리 구조(스레드)와 상태 조회 | **A** |
| **P0** 4 | 해석 결과 모델과 조회 API | **A** |
| **P0** 5 | 공통 권한(IsOwner), 에러 형식, Swagger, camelCase 전역 설정 | B |
| **P1** 6 | 공식 자료 모델 (SourceDocument, SourceChunk, CrawlLog) | B |
| **P1** 7 | 수집 스크립트 (수동 URL 목록) | B |
| **P1** 8 | 검증 결과 모델(VerificationItem)과 근거 조회 API | **A** |
| **P1** 9 | 담당기관(Agency) 시드 데이터와 조회 API | B |
| **P2** 10 | 체크리스트 CRUD, 진행률 | **A** |
| **P2** 11 | 일정 등록·목록·완료, 기한 경과 | **A** |
| **P2** 12 | 내 문서함 | **A** |
| **P2** 13 | 챗봇 저장부 (ChatMessage, 질문 API 자리) | B |
| P3 | FCM 알림·cron(B), Celery worker 전환(A), OCR 재업로드 응답(A), 암호화·30일 자동 삭제(A), 공공 API·재수집(B), KPI 로그(B) | |

- **A = documents 앱 담당 (차성진)**, B = accounts·sources·chatbot·공통 담당
- **다른 앱은 담당자 외 수정 금지.** 의존 모델이 아직 없으면 임시 필드로 대체:
  - User → `settings.AUTH_USER_MODEL`로 FK
  - SourceDocument 없으면 근거 URL·제목·확인일을 문자열 필드로 임시 저장
  - Agency 없으면 기관명 문자열로 임시 저장
- 공통 에러 형식은 B가 정하기 전까지 DRF 기본 형식 유지

### User 모델 도입 순서
1. B가 `accounts.User`(AbstractUser 상속, 이메일 로그인)와 `AUTH_USER_MODEL = 'accounts.User'`를 먼저 만들어 dev에 머지한다.
2. 머지 후 로컬 `db.sqlite3`와 Railway DB를 초기화하고 처음부터 `migrate`한다.
3. documents는 User를 `settings.AUTH_USER_MODEL`로만 참조한다. **B 머지 전에는 documents 마이그레이션을 만들거나 migrate하지 않는다.**

---

## 8. 미결 사항 (추천안을 기본값으로 사용)

| 기능 | 질문 | 기본값(추천안) |
| --- | --- | --- |
| F1-1 | 파일 형식·크기 | jpg·jpeg·png·pdf, 최대 20MB |
| F1-1 | 요청당 문서 수 | 1개 |
| F1-1 | 지원 언어 | 목록 확정 전 (코드는 언어 코드 문자열로 유연하게) |
| F1-2 | 관리자 결과 접근 | 마스킹된 결과만 |
| F1-3 | 재업로드 기준 | 기한·금액·기관명 신뢰도 기준 (기준값은 AI와 합의) |
| F1-4 | 관리자 원본 접근 | 승인된 운영자만, 감사 로그 |
| F1-4 | 원본 보관 | 업로드 후 30일 자동 삭제 |
| F2-1 | 발신기관·지역 식별 실패 | UNVERIFIED + 담당기관 확인 안내 |
| F2-2 | 오래된 자료 기준 | 운영자 설정 기간 |
| F6-1 | 인증 수단 | 이메일 + 비밀번호 |
| F6-1 | 프로필 필수 | 언어·국적·체류자격 모두 필수 |
| F6-3 | 갱신 방식 | 정기 자동 수집 + 관리자 검토 (해커톤은 수동) |
| F6-3 | 오래됨 기준 | 자료 유형별 |

---

## 9. 작업 규칙

- 브랜치: `main` ← `dev`(기본) ← `feature/be-작업내용` · `fix/...` · `docs/...`
- feature → dev는 PR(Squash), dev → main은 머지 커밋. **머지는 차성진만** 한다. Claude Code는 PR 생성까지만.
- main·dev 직접 push 금지, force push 금지, main·dev 삭제 금지
- `.env`·비밀키 커밋 금지. `DJANGO_SECRET_KEY`는 Railway 환경변수에만.
- 프론트 `VITE_` 변수에는 API 키를 넣지 않는다 (빌드 시 공개됨).
- 마이그레이션 충돌은 `python manage.py makemigrations --merge`로 해결
- 업로드 파일 저장소는 **Railway Volume** (Railway 기본 파일 시스템은 재배포 시 초기화됨)
- PWA 서비스워커는 `/api` 요청을 캐시하지 않는다

---

## 10. 팀 결정 사항 (2026-11-03)

| # | 주제 | 결정 |
| --- | --- | --- |
| 1 | 응답 키 | camelCase. `djangorestframework-camel-case`를 `REST_FRAMEWORK` 전역으로 적용 (B와 합의 후 B가 설정). 그 전까지 documents는 snake_case로 작성 |
| 2 | 추출 항목 | 값마다 `extracted`(bool), `confidence`(0~1). 문서 단위 `lowConfidence`(bool). 금액은 `amount: {value, extracted, confidence}` |
| 3 | 검증 결과 | `verification = {overall: MATCHED\|PARTIAL\|MISMATCH\|UNVERIFIED, items: [{id, field, value, status, sources: [{title, url, agency, checkedAt, publishedAt}]}]}` |
| 4 | 체크리스트 항목 | `{id, text, order, done, confirmStatus: CONFIRMED\|CONDITIONAL\|NEEDS_CHECK\|MISMATCH, dueDate, overdue, requiredDocs, sourceIds}` |
| 5 | 처리 단계 `step` | `QUEUED`, `OCR`, `MASKING`, `INTERPRETING`, `VERIFYING`, `DONE` (실패 시 `status`가 `FAILED`) |
| 6 | `agency.source` | `document` \| `official` \| `unknown` |
| 7 | 일정 | `{id, documentId, title, dueDate, done, overdue, checklistId, agencyName}` |
| 8 | 배포 | P0 비동기는 스레드. Celery worker·알림 cron은 P3에서 Railway 별도 서비스. 업로드 저장소는 Railway Volume |
| 9 | `AUTH_USER_MODEL` | B가 `accounts.User`(AbstractUser, 이메일 로그인)를 먼저 만든다. 머지 후 로컬·Railway DB 초기화 후 migrate. documents는 `settings.AUTH_USER_MODEL`로 FK만, B 머지 전 migrate 금지 |

---

## 11. 레포와 다른 부분 (아직 없는 것)

2026-11-03 dev(`00b5249`) 기준. 구현되면 이 표에서 지운다.

| 문서 기준 | 현재 레포 | 담당 |
| --- | --- | --- |
| `accounts`·`documents`·`chatbot`·`sources` 앱 | `core` 앱(`/api/health`)만 있음 | A·B |
| `accounts.User`, `AUTH_USER_MODEL` | 설정 없음 (기본 User로 migrate된 상태) | B |
| simplejwt 인증 | 패키지만 설치, `INSTALLED_APPS`·`REST_FRAMEWORK` 설정 없음 | B |
| camelCase 응답 | `djangorestframework-camel-case` 미설치 | B |
| Swagger | `drf-spectacular` 등 미설치 | B |
| 이미지·PDF 업로드 | Pillow 미설치, `MEDIA_ROOT`/`MEDIA_URL` 미설정 | A |
| 업로드 저장소 | Railway Volume 미연결 | A |
| 비동기 처리 | P0 스레드 구조 없음 (Celery는 P3) | A |
| pgvector | 미설치 | B·AI |
| PWA | `vite-plugin-pwa` 미설치, 서비스워커 없음 | FE |
