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

## 1. chatbot 앱이 쓰는 법 (chatbot/services.py)

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
# LLM: 개발 ollama / 배포 anthropic / 키 없이 흐름만 mock
AI_LLM_PROVIDER=ollama
AI_OLLAMA_URL=http://localhost:11434
AI_OLLAMA_MODEL=qwen2.5:7b
ANTHROPIC_API_KEY=
AI_ANTHROPIC_MODEL=claude-haiku-5-5

# 임베딩: ollama(bge-m3, 1024) / fastembed / hash(테스트)
AI_EMBEDDER=ollama
AI_OLLAMA_EMBED_MODEL=bge-m3
AI_EMBED_DIM=1024

# 검색: db(공식 자료 테이블) / memory(.md 폴더, Django 없이)
AI_RETRIEVER=db
AI_KB_DIR=
AI_TOP_K=5
AI_MIN_SCORE=0.35
```
- 배포(Railway)엔 Ollama가 없어서 `AI_LLM_PROVIDER=anthropic`. 임베딩도 `fastembed`나 임베딩 API로 바꿔야 함 (Railway 메모리 보고 결정)
- `AI_MIN_SCORE=0.35`는 임시값. 실제 모델로 `--search`를 돌려 보고 조정
- 임베딩 모델을 바꾸면 차원이 바뀌니 저장된 임베딩을 다시 만들어야 함

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

## 5. 다른 파트에 맞춰야 할 것
- **명세서 ⑥**: `grounded: true/false` → `groundingStatus` (불일치를 표현해야 해서)
- **프론트 Chat.jsx**: `m.grounded` → `m.groundingStatus` 배지 3종, 출처는 받은 객체의 `title`·`checkedAt`을 바로 사용, 담당기관은 `agency_contact`, 챗봇 요청만 timeout 60초 이상

## 파일
```
ai/chatbot/
  __init__.py     공개 함수 모음
  service.py      generate_answer, 더미, 근거 판정
  prompts.py      프롬프트 (근거 규칙)
  retriever.py    검색: db / memory
  embedder.py     임베딩: ollama / fastembed / hash
  llm.py          LLM: anthropic / ollama / mock
  chunker.py      청크 분할
  config.py       환경변수
  __main__.py     터미널 테스트 도구
ai/tests/         unittest
  fixtures/       테스트용 [샘플] 공식 자료 .md (지어낸 내용, 실제 DB에 넣지 말 것)
```
