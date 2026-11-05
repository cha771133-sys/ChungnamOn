# P2-13 문서별 챗봇 저장 및 API

기존 `Document`, `InterpretationResult`, `SourceDocument`를 변경하지 않고 확장한다.
현재 챗봇 AI 모듈과 RAG 계약은 없으므로 외부 요청 없는 로컬 더미를 제공한다.
새 패키지, OCR/해석/검증 엔진, 세션 모델, 프론트 변경은 없다.

## API

`POST /api/documents/{documentId}/chat/`와 `GET /api/documents/{documentId}/chat/` 모두
`Authorization: Bearer <access>`가 필요하다. URL 이름은 `chatbot:document-chat`이다.
소유자는 서버의 문서 관계로 확인하며, 타인/없는 문서는 동일한 404다. 관리자도 우회하지 않는다.

POST 요청은 `{"message": "필요 서류를 확인하고 싶습니다."}`이다. 문자열만 허용하며 앞뒤
공백을 제거한 1~2,000자, 유니코드를 지원한다. 그 밖의 필드는 거부한다. 같은 질문을
재제출하면 매번 새 기록을 생성한다. HTTP 재시도에 대한 별도 idempotency 기능은 없다.
성공한 질문·답변 쌍 하나를 `ChatMessage` 하나로 저장한 뒤 201을 반환한다.

```json
{
  "chatId": 1,
  "documentId": 123,
  "question": "필요 서류를 확인하고 싶습니다.",
  "answer": "현재 공식 근거를 확인할 수 없습니다. 정확한 내용은 담당기관에 확인해 주세요.",
  "language": "ko",
  "grounded": false,
  "status": "UNVERIFIED",
  "sources": [],
  "suggestedQuestions": [],
  "createdAt": "2026-11-05T09:00:00+09:00"
}
```

POST는 `DONE`, 해석 결과 존재, `low_confidence=false`를 요구한다. 저신뢰 문서는
정확한 내용을 확인할 수 있게 재촬영/재업로드해야 한다. 기한·금액·기관을 확정하여 답변하지 않는다.
DONE인데 결과가 없는 경우 일관성 오류로 503을 반환한다. 기존 결과 조회 API는 변경하지 않았다.

언어는 **유효한 UserProfile.language → 유효한 InterpretationResult.language →
Django LANGUAGE_CODE의 지원 코드** 순으로 결정한다. 모두 미지원이면 프로젝트 기본 한국어
`ko`를 사용한다. 프로필은 자동 생성하지 않는다. 기존 문서 파이프라인은 별도의 기본 언어 `vi`를
사용하지만, 챗봇은 프로필을 우선하며 이 정책을 파이프라인에 소급 적용하지 않는다.
프로필에서 지원하는 7개 언어의 안전한 고정 안내문을 반환하고 실제 안내문의 언어를 저장한다.

GET은 처리 상태/저신뢰 여부/해석 결과 존재와 무관하게 과거 이력을 조회한다.
`StandardPagination`: 기본 20개, `page_size` 최대 100, `page` 페이지 번호.
응답은 `{count, next, previous, results}`이며 `count`는 해당 문서 전체 기록 수다.
결과는 `created_at DESC, id DESC` 순서다. 빈 문서는 200과 빈 results를 반환한다.
문서 삭제 시 대화도 CASCADE로 삭제한다. 페이지 요청 사이의 삽입/삭제 스냅샷은 보장하지 않는다.

| HTTP | code | 의미 |
| --- | --- | --- |
| 400 | VALIDATION_ERROR | 필수 질문/타입/길이/추가 필드 오류 |
| 401 | NOT_AUTHENTICATED / AUTHENTICATION_FAILED | JWT 누락/무효 |
| 404 | NOT_FOUND | 소유 문서 또는 페이지 없음 |
| 409 | DOCUMENT_NOT_READY | 미완료/저신뢰/AI 호출 중 해석 변경 |
| 503 | CHAT_SERVICE_UNAVAILABLE | AI 실패/시간 초과/형식 오류/결과 누락/출처 변경/DB 오류 |

일반 DRF 오류는 기존 공통 handler를 그대로 사용한다. 챗봇 409/503만 뷰에서 같은
`{code, message, details}` 형식의 고정 문구로 반환한다. 예외 원문은 응답이나 로그에 기록하지
않는다. AI 호출 전 성공 레코드를 만들지 않으며, 실패하면 기존 대화만 유지한다.

## AI 교체 계약

호출 경계는 `chatbot.services.ai_client.generate_answer`다. 뷰는 이 함수를 직접 호출하지 않는다.
기본 설정 `CHATBOT_AI_MODE=dummy`는 의도된 더미다. 현재 다른 모드는 503으로 차단하며
실패를 더미 성공으로 바꾸지 않는다. 실제 통신은 아직 없고 API Key도 필요하지 않다.
실제 어댑터 도입 시 이 함수의 호출 구현을 교체하고, 외부 호출의 연결/읽기 시간 제한을 설정한다.
`AIClientError`(시간 초과 포함) 또는 호출 예외는 안전한 503이 된다.

키워드 입력:

- `question`: 기본 개인정보 마스킹을 적용한 질문. DB에는 사용자가 제출한 질문을 보존한다.
- `interpretation`: `original_text`(기존 마스킹된 원문), `translated_text`, `summary`, `terms`,
  `deadlines`, `required_docs`, `agency`, `verification`, `language`.
- `history`: 같은 문서의 최근 최대 10개 쌍, 과거→현재 순. 각 항목은
  `question`, `answer`, `language`, `grounded`, `status`다. 과거 답변은 공식 근거가 아니다.
- `language`: 실제 답변할 지원 언어 코드.
- `official_sources`: 아래 서버 확인을 통과한 최소 메타데이터만 전달한다. 본문/청크는 보내지 않는다.

필수 출력은 `answer`(공백 제거 후 1~20,000자), `sources`(최대 20개), bool `grounded`,
`suggestedQuestions`(최대 5개, 항목당 200자)다. 추천 질문은 공백 제거/중복 제거한다.
`language`를 반환하면 요청 언어와 같아야 하며 생략 시 요청 언어로 간주한다.
`status` 생략은 미확인 응답에서만 허용한다. 근거 기반/불일치는 명시적 상태가 필요하다.
서비스는 결과 형식을 검증하며 실제 생성 문장의 언어/의미/정확성은 AI 담당자의 계약이다.

- `GROUNDED` ↔ grounded=true: AI가 명시적으로 판단하고 실제 연결된 공식 근거가 있어야 한다.
- `UNVERIFIED` ↔ grounded=false: 정상적인 근거 미확인. 장애와 구분한다. 더미는 항상 이 상태다.
- `MISMATCH` ↔ grounded=false: 기존 검증 결과도 MISMATCH이고 AI도 충돌 및 출처를 명시해야 한다.

## 공식 근거 연결 및 스냅샷

현재 `InterpretationResult.verification`은 `status`, `sources` JSON 계약이고, 파이프라인의
더미 출처에는 SourceDocument ID가 없다. 따라서 **현재 더미 문서의 공식 근거 입력은 항상 빈 배열**이다.
URL이 같다는 이유로 DB 자료를 찾아 연결하지 않는다. P1-8이나 RAG 검색을 대신 구현하지 않았다.

추후 검증 담당자는 기존 JSON의 sources 항목에 실제 `sourceDocumentId` 참조를 제공할 수 있다.
상위 검증 status는 MATCHED 또는 MISMATCH여야 한다. 이 연결은 검증 담당 모듈의 책임이며
이 API의 요청 본문으로 설정할 수 없다. AI 출력 출처도 해당 목록에 포함된 ID만 허용한다.
DB 존재, ACTIVE, 수집 확인 시각 존재, 기존 수집기 정책의 공식 HTTPS URL을 모두 검사한다.
PENDING/INACTIVE/확인일 없음/미연결/비공식 URL은 공식 근거 입력에서 제외한다.
AI가 그런 자료를 출처로 주장하거나 URL/제목/확인일을 바꾸면 계약 오류로 503을 반환한다.
AI가 근거 미확인으로 응답하면 정상 UNVERIFIED로 저장할 수 있다.

AI는 `sources: [{"sourceDocumentId": <실제 ID>}]`만 반환해도 된다. 서버는 실제 DB에서 아래
스냅샷을 구성하여 저장한다. AI가 메타데이터를 함께 반환하면 서버 값과 일치해야 한다.

| 필드 | 값 |
| --- | --- |
| sourceDocumentId | 실제 SourceDocument.pk |
| title | 생성 당시 제목 |
| url | 생성 당시 source_url |
| checkedAt | 실제 collected_at, 시간대가 있는 ISO 8601 |
| contentHash | 생성 당시 content_hash (없으면 빈 문자열) |

출처/확인일은 추측하거나 현재 시각으로 생성하지 않는다. 기존 근거 스냅샷은 이후 자료 수정·
비활성화·삭제에도 조용히 갱신하지 않는다. 내용 전체의 역사 보관은 이번 범위 밖이며 스냅샷은
자료 ID/제목/URL/확인일/본문 해시를 식별한다. 이 정보나 grounded만으로 답변 정확성을 보증하지 않는다.

AI 호출 중에는 DB 잠금을 유지하지 않는다. 저장 직전에 짧은 transaction에서 문서·해석·
출처를 잠그고 소유권/상태/저신뢰 여부 및 최초 데이터와의 일치를 다시 확인한다. 변경되면
재시도 오류를 반환하고 저장하지 않는다. SQLite에서는 DB 자체의 쓰기 잠금/충돌 오류 정책을 따른다.

## 개인정보 및 운영

파일 객체·파일 경로·마스킹 전 OCR을 AI에 전달하지 않는다. `prepare_ai_payload` 경계에서
기존 문서 마스킹 함수 및 기본 전화번호 가림을 질문/이력/해석에 적용한다. 이는 자유문 속
이름·주소까지 완전히 비식별화하는 정책이 아니다. **실제 외부 어댑터 연결 전에 개인정보 처리
정책 검토와 전송 제한을 구현해야 한다.** 현재는 로컬 더미만 실행하므로 외부 데이터 전송이 없다.
질문·답변·문서 본문이나 예외 원문을 서비스 로그에 남기지 않는다.

Admin은 기존 권한 체계를 사용하고 질문·답변 전문/검색/문서 링크를 제공하지 않는다.
목록/상세 모두 ID, 문서 ID, 언어, 근거 여부/상태, 시각만 보여준다. 직접 추가/변경/삭제도 금지한다.
기존 DocumentAdmin/UserAdmin의 삭제 확인에는 ChatMessage의 직접 삭제 권한을 요구하지 않도록
최소 예외를 적용하여 부모 삭제에 따른 CASCADE를 유지한다. 부모 및 그 밖의 관계 객체 삭제 권한은 유지한다.
Swagger `/api/docs/`, OpenAPI `/api/schema/`에 요청/응답/오류/빈 이력 예시가 있다.

## 완료 체크리스트

- [x] 최신 dev 구조 검토, 기존 모델 및 sources 수집기 정책 재사용
- [x] chatbot 등록, ChatMessage 및 신규 마이그레이션/제약/복합 인덱스
- [x] JWT, 소유권 404, DONE/저신뢰/결과 검사, 질문 검증
- [x] 다국어 안전 더미, AI 호출 경계와 응답 검증, 실패 시 미저장
- [x] 최근 문맥 10개, 이력 격리/안정적 정렬/공통 페이지네이션
- [x] 공식 근거 참조 검사 및 생성 당시 스냅샷, 더미 근거 승격 금지
- [x] Admin 개인정보 최소화 및 Swagger 문서/예시
- [x] 모델·서비스·API·AI·이력·스키마·Admin 테스트

검증 명령 (backend에서 프로젝트 환경으로 실행):

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test
python manage.py spectacular --validate --fail-on-warn --file /tmp/chungnamon-schema.yaml
```

테스트는 합성 문서·mock/로컬 더미를 사용하며 실제 LLM 호출이나 답변 정확도 평가는 하지 않는다.
