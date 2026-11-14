# 문서 챗봇 AI 모듈 (`backend/ai/chatbot`)

R5 · F5-1, F5-2 담당 AI 코드. **Django 앱이 아니라 순수 파이썬 모듈**이라 테이블을 만들지 않고 `INSTALLED_APPS`에도 안 넣는다.
DB 저장, 권한, 대화 이력은 `chatbot` 앱이, 공식 자료 테이블은 `sources` 앱이 맡는다 (별도 PR).

```
FE Chat.jsx ──POST /api/chat/──▶ chatbot 앱
                            ├─ 권한 확인, 문서 해석 결과·이전 대화 조회
                            ├─ generate_answer(document, message, history, language)  ← 이 모듈
                            │     ├─ 검색어 만들기 (외국어 → 한국어)
                            │     ├─ 공식 자료 검색 [발신기관 시군, 충남, 전국]
                            │     ├─ LLM 답변 생성
                            │     └─ 근거 판정 → GROUNDED / UNVERIFIED / CONFLICT
                            └─ ChatMessage 저장 → 응답
```

## 0. Django 교체 자리 연결 (현재 쓰는 방식)

팀원 코드는 그대로 두고, 팀원이 비워 둔 AI 교체 자리 두 곳에서만 이 모듈을 부른다. 둘 다 기본값은 `dummy`.

| 교체 자리 | 설정 | 호출 | 하는 일 |
|---|---|---|---|
| `documents.services.pipeline.verify` | `DOCUMENTS_VERIFY_MODE=rag` | `integration.verify_interpretation` | 해석 결과로 관련 공식 자료 검색 (자료당 1개, 관련도 순 최대 10개) → LLM이 **항목별로** 대조 (C1 형식, 아래 참고) |
| `chatbot.services.ai_client.generate_answer` | `CHATBOT_AI_MODE=rag` | `integration.chat_answer` | RAG 답변을 `backend/chatbot/README.md` 계약 형식으로 변환 |

챗봇 출처 규칙 (chatbot 앱 계약):
- 출처로 내는 건 `official_sources`(= verify가 연결한 자료) 안의 `sourceDocumentId`뿐
- LLM이 실제로 쓴 자료가 그 밖에만 있으면 `UNVERIFIED`, `sources=[]`
- `CONFLICT`는 문서 검증도 `MISMATCH`일 때만 `MISMATCH`, 아니면 `UNVERIFIED`
- 그래서 **챗봇이 GROUNDED를 내려면 verify도 rag여야 함** (dummy verify에는 ID가 없음)
- 오류(검색·LLM·timeout)는 `AIClientError` → 503. 오류 원문은 버림

청크·임베딩 생성 (수집기는 안 건드림, 관리자 승인 후 실행):
```bash
python manage.py shell -c "from ai.chatbot import build_source_chunks; print(build_source_chunks())"
python manage.py shell -c "from ai.chatbot import build_source_chunks; print(build_source_chunks([3, 5]))"  # 특정 자료만
```
`sources/REFRESH.md`의 'AI 경계' 계약과 자동 `ACTIVE` 전환 정책(#60)을 따름:
- 대상: 관리자 승인(`APPROVED`)·공식 링크 있음·`INACTIVE` 아님·본문 있음 (승인 후 재처리 대기라 `PENDING`인 자료 포함)
- 자료마다 처리를 시작할 때 본문 해시·`version`(승인 정보 버전)·상태를 다시 읽어 기억함
- 현재 승인 본문 해시로 만든 청크가 있고 임베딩 차원이 `AI_EMBED_DIM`과 같으면 청크 생성을 건너뜀(임베딩 API도 안 부름)
- 새 청크는 기존 청크 **뒤 순서에 추가**하고 `source_content_hash`에 읽은 본문 해시를 기록. 이전 청크는 삭제·ID 재사용 안 함
- **완료 직전 검사**: 같은 트랜잭션에서 자료 행을 잠그고, 완료 처리가 `version`을 올리기 전에
  시작 시점의 해시·`version`과 비교하고 관리자 승인·`INACTIVE` 여부를 다시 확인. 청크 생성을 건너뛰는 경로도 똑같이 검사
  - 다 맞으면: 청크 저장 + 재처리 대기 자료는 **재처리 완료(`ai_reprocessing_required=False`)·자동 `ACTIVE` 전환·`version+1`을 함께** 반영
  - 하나라도 다르면(`changed`): 청크를 저장하지 않고 자료도 건드리지 않음. 이 작업의 청크는 최신으로 인정하지 않고,
    최신 상태·재처리 플래그는 그대로 둠. 다음 실행이 새 시작 값으로 다시 처리
- 완료 처리는 `status`·`ai_reprocessing_required`·`version`·`updated_at`만 바꿈 (제목·기관 등 관리자 입력은 그대로)
- 이미 재처리가 끝난 자료(플래그 `False`)는 상태·`version`을 바꾸지 않음. 여러 번 실행해도 청크 중복·`version` 중복 증가 없음
- 임베딩이 실패하면 예외가 나고 자료는 기존 상태 그대로
- 반환값: `documents`·`chunks`·`skipped`·`completed`·`activated`(그중 `ACTIVE`로 바꾼 수)·`changed`

검색(`DbRetriever`)·verify 후보도 같은 계약: `ai_eligible`(ACTIVE·승인·재처리 완료·공식 링크) 자료의
`source_content_hash`가 현재 본문 해시와 같은 청크만 씀. 승인 전 자료와 이전 본문 청크는 검색되지 않음.

### verify 결과 형식 (C1, 관련 이슈 #62)

`InterpretationResult.verification`에 그대로 저장되고 결과 API에 그대로 나간다 (자동 camelCase 변환이 없어서 저장 키가 camelCase).

```json
{
  "overall": "MATCHED | MISMATCH | UNVERIFIED",
  "status": "overall과 같은 값 (기존 코드·챗봇 호환)",
  "items": [
    {"key": "DEADLINE", "label": "납부 기한", "docValue": "납부 마감 2026-08-31",
     "officialValue": "8월 16일 ~ 8월 31일", "status": "MATCHED", "reason": "…", "sourceIds": ["s1"]}
  ],
  "sources": [
    {"id": "s1", "title": "…", "url": "https://…", "checkedAt": "2026-11-05", "publishedAt": "2026-03-02",
     "sourceDocumentId": 12}
  ]
}
```
- **items**: `AMOUNT`·`DEADLINE`·`PAYMENT_METHOD`·`AGENCY_CONTACT` 순서. 문서에 값이 없는 항목은 뺌
  - 문서 값: `amount`(문자열 또는 null) / `deadlines` / `payment_methods`(문자열 목록) / `agency.phone`
  - 지금 더미 interpret에는 `amount`·`payment_methods`가 없어서 `DEADLINE`·`AGENCY_CONTACT`만 나옴
  - `officialValue`·`reason`은 한국어. `UNVERIFIED`면 `officialValue`는 null, `sourceIds`는 빈 목록
  - `AGENCY_CONTACT`도 검색된 공식 자료로만 판정 (담당기관 조회 #55 연동은 추후)
- **overall**: LLM이 아니라 코드가 items로 계산. 하나라도 `MISMATCH`면 `MISMATCH`, 모두 `MATCHED`면 `MATCHED`, 그 외(항목 0개 포함) `UNVERIFIED`
- **LLM 판정 검사**: status가 이상한 값이거나, `sourceIds`에 `sources`에 없는 id가 있거나, `MATCHED`·`MISMATCH`인데 근거 id·공식 값이 없으면 **그 항목만** `UNVERIFIED`. LLM이 빠뜨린 항목도 `UNVERIFIED`
- **sources**: 결과에 한 번만. `id`는 `s1`부터. `publishedAt`은 자료 기준일이 있을 때만. 챗봇은 지금처럼 `status`가 `MATCHED`/`MISMATCH`일 때 `sources[].sourceDocumentId`를 공식 출처로 씀
- 발신기관·지역을 모르면 검색하지 않고 항목을 모두 `UNVERIFIED`로. 관련 자료가 없어도 같음
- **오류**: 검색·임베딩·LLM 등 어떤 오류든 `verify_interpretation()`이 직접 잡아 `{"overall": "UNVERIFIED", "status": "UNVERIFIED", "items": [], "sources": []}`를 돌려줌. 문서 처리는 계속되고, 로그에는 오류 종류만 남김

실제 Ollama로 끝까지 확인 (샘플 자료를 테스트 DB에만 넣음, CPU면 몇 분 걸림):
```bash
AI_LIVE_OLLAMA=1 python manage.py test ai.tests.test_ollama_live -v 2
```

지역: sources 앱은 도 단위를 `충청남도`로 저장하므로 검색 범위는 `[발신 시군, 충청남도, 충남, 전국]`.

## 1. 모듈 직접 호출 (참고용)

```python
from ai.chatbot import generate_answer, generate_answer_dummy

result = generate_answer(
    document,                 # 해석 결과 dict (명세서 4장 ③ 형식, snake/camel 둘 다 됨)
    "외국인등록증이 없으면 어떻게 내나요?",
    history=[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],  # 오래된 순
    language="vi",            # 사용자 프로필 언어. 없으면 document.language → ko
)
```

AI가 준비되기 전엔 `generate_answer_dummy`로 바꿔 끼우면 돼. 입력·출력 형식이 똑같고, 질문에 따라 세 가지 상태를 다 돌려줘서 프론트가 화면을 다 만들어 볼 수 있어 ('기한' → GROUNDED, '감면' → CONFLICT, 나머지 → UNVERIFIED).

### document에서 쓰는 키
| 키 | 용도 | 없으면 |
|---|---|---|
| `title`, `doc_type` | 검색어·프롬프트 | 질문만으로 검색 |
| `agency.name`, `agency.phone` | 검색 지역 추출, 미확인 시 연락처 | 충남·전국만 검색, 연락처 없음 |
| `region` | 검색 지역 (agency보다 우선) | agency.name에서 시군 추출 |
| `original_text` | 마스킹된 원문 (앞 3000자) | 요약 정보만 사용 |
| `summary`, `deadlines`, `required_docs`, `terms` | 문서 내용 근거 | 생략 |
| `language` | 답변 언어 기본값 | ko |

### 반환값 (snake_case → camelCase 전역 설정이 켜지면 응답에서 자동 변환)
```json
{
  "answer": "고지서에 있는 가상계좌로 입금하면 …",
  "grounding_status": "GROUNDED",
  "sources": [{ "title": "아산시 지방세 납부 방법", "url": "…", "agency": "아산시청 세무과",
                "region": "아산시", "checked_at": "2026-10-30", "published_at": null, "chunk_id": 12 }],
  "agency_contact": null,
  "suggested_questions": ["기한이 지나면 어떻게 되나요?"],
  "language": "vi"
}
```

| grounding_status | 뜻 | sources | agency_contact |
|---|---|---|---|
| `GROUNDED` | 근거 기반: 공식 자료가 답을 뒷받침 | 실제로 쓴 자료만 | `null` |
| `UNVERIFIED` | 미확인: 공식 근거 없음 (문서 내용만으로 답한 경우 포함) | `[]` | 문서 발신기관 |
| `CONFLICT` | 불일치: 문서 ↔ 공식자료, 또는 공식자료끼리 다름 | 충돌한 자료들 | 문서 발신기관 |

LLM이 고른 상태를 그대로 쓰지 않고 한 번 더 거름: 검색 결과가 없거나 실제로 쓴 출처가 없으면 무조건 `UNVERIFIED`.

### 에러
- `ValueError`: 질문이 비어 있음 → 400
- `RuntimeError`: LLM·임베딩 호출 실패 → 503 추천

### 저장 추천 (ChatMessage)
`grounding_status`, `sources`(JSON), `language`는 assistant 메시지에 같이 저장해 두면 대화 이력 화면에서 배지·출처를 다시 그릴 수 있어.

## 2. sources 앱 수집기가 쓰는 법 (sources/collector.py)

청크 분할·임베딩은 AI 몫이라 함수로 제공해.
```python
from ai.chatbot import prepare_chunks, embed_passages

chunks = prepare_chunks(cleaned_body, title=doc.title)   # 제목·조문 단위로 자르고, 짧은 섹션은 합침
vectors = embed_passages(chunks)                          # list[list[float]], 길이 = AI_EMBED_DIM
for i, (text, vec) in enumerate(zip(chunks, vectors)):
    SourceChunk.objects.create(source_document=doc, chunk_order=i, chunk_text=text, embedding=vec)
```

### 검색 (`AI_RETRIEVER`)
- `db` (기본): sources 앱 테이블 검색. Postgres+pgvector면 SQL, 아니면 numpy
- `memory`: `AI_KB_DIR` 폴더(기본 `sources/data/pages`)의 .md를 메모리에서 검색. Django 없이 `python -m ai.chatbot`으로 개발할 때

## 3. 환경변수 (`backend/.env`, `.env.example`에는 값 없이)

```bash
# LLM: 개발 ollama / 배포 anthropic / mock은 테스트 전용 (비어 있으면 오류, mock으로 자동 전환 안 함)
AI_LLM_PROVIDER=ollama
AI_OLLAMA_URL=http://localhost:11434
AI_OLLAMA_MODEL=qwen2.5:7b
AI_OLLAMA_NUM_CTX=8192        # Ollama 기본 4096이면 긴 프롬프트가 잘림
AI_REQUEST_TIMEOUT=120        # LLM·임베딩 연결/읽기 timeout(초). CPU Ollama는 부족할 수 있음
ANTHROPIC_API_KEY=
AI_ANTHROPIC_MODEL=claude-haiku-5-5

# 임베딩: openai(text-embedding-3-large, 1536) / gemini(gemini-embedding-001, 768) / ollama(bge-m3, 1024) / fastembed / hash(테스트)
AI_EMBEDDER=openai
OPENAI_API_KEY=                          # OpenAI 키. Authorization 헤더로만 보내고 로그·오류에는 안 남김
AI_OPENAI_EMBED_MODEL=text-embedding-3-large
AI_EMBED_DIM=1536                        # openai는 dimensions로 요청 (기본 1536, 최대 3072)
# Gemini로 쓸 때: AI_EMBEDDER=gemini, GEMINI_API_KEY=..., AI_GEMINI_EMBED_MODEL=gemini-embedding-001, AI_EMBED_DIM=768
GEMINI_API_KEY=                          # Google AI Studio 키. 헤더(x-goog-api-key)로만 보냄
AI_GEMINI_EMBED_MODEL=gemini-embedding-001
# Ollama로 개발할 때: AI_EMBEDDER=ollama, AI_EMBED_DIM=1024
AI_OLLAMA_EMBED_MODEL=bge-m3
# 임베딩 모델·차원을 바꾸면 build_source_chunks()를 다시 실행. 차원이 다른 옛 청크는 검색에서 빠지고(삭제는 안 함)
# 새 차원 청크가 만들어짐. 같은 차원끼리 모델만 바꾸면 구분이 안 되니 차원도 함께 바꿀 것 (예: Gemini 768 → OpenAI 1536)

# 검색: db(공식 자료 테이블) / memory(.md 폴더, Django 없이)
AI_RETRIEVER=db
AI_KB_DIR=
AI_TOP_K=5
AI_MIN_SCORE=0.65
```
- 배포(Railway)엔 Ollama가 없어서 `AI_LLM_PROVIDER=anthropic`, 임베딩은 `openai`(1536차원). `gemini`도 선택지로 남겨 둠
- openai: 429·5xx는 서버가 준 대기 시간(retry-after-ms·Retry-After)만큼 기다렸다가 재시도 (청크 생성 최대 5회·60초, 질문 검색 최대 1회·2초), 문장은 100개씩 묶어서 전송
- gemini 무료 등급은 429(한도 초과)가 날 수 있음: 서버가 준 대기 시간(최대 60초)만큼 기다렸다가 최대 5번 재시도, 문장은 100개씩 묶어서 전송
- `AI_MIN_SCORE=0.65`: Gemini 기준 (관련 없는 자료도 0.5~0.6이 나옴). 임베딩 모델을 바꾸면 `--search --db`로 다시 조정 (OpenAI 값은 데모 자료가 들어온 뒤 조정 예정)
- 임베딩 모델·차원을 바꾸면 `build_source_chunks()`를 다시 실행 (차원이 다른 청크는 자동으로 다시 만듦)

## 4. 개발할 때

```bash
cd backend
pip install -r ai/requirements.txt
ollama pull bge-m3 && ollama pull qwen2.5:7b

python -m ai.chatbot --kb ai/tests/fixtures "주민세 안 내면 어떻게 돼요?"   # 답변 JSON (테스트 샘플로)
python -m ai.chatbot --lang vi "Tôi trả ở đâu?"              # 베트남어
python -m ai.chatbot --search "주민세 납부 기간"              # 검색 점수만 (AI_MIN_SCORE 튜닝용)
python -m unittest discover -s ai/tests -t .                 # 테스트 (Ollama·키 없이 돌아감)
```

## 5. 남은 것
- 챗봇 API 응답은 chatbot 앱 계약(`grounded` + `status`)을 그대로 따름. `agency_contact`는 계약에 없어서 어댑터에서 버림
- **프론트 Chat.jsx**: 챗봇 요청만 timeout 60초 이상
- **개인정보**: Ollama(로컬)는 외부 전송이 없음. `AI_LLM_PROVIDER=anthropic`으로 배포하기 전에 chatbot README의 개인정보 처리 정책 검토 필요
- `AI_MIN_SCORE`는 실제 수집 자료로 다시 조정

## 파일
```
ai/chatbot/
  __init__.py     공개 함수 모음
  service.py      generate_answer, 더미, 근거 판정
  prompts.py      프롬프트 (근거 규칙)
  retriever.py    검색: db / memory
  embedder.py     임베딩: openai / gemini / ollama / fastembed(선택 설치) / hash
  llm.py          LLM: anthropic / ollama / mock
  chunker.py      청크 분할
  config.py       환경변수
  __main__.py     터미널 테스트 도구
ai/tests/         unittest
  fixtures/       테스트용 [샘플] 공식 자료 .md (지어낸 내용, 실제 DB에 넣지 말 것)
```
