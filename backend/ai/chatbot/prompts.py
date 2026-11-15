"""프롬프트. 규칙은 명세서 F5-1·F5-2와 6장 '신뢰성 원칙'을 그대로 옮김."""
import json

LANG_NAMES = {
    "ko": "한국어",
    "vi": "Tiếng Việt (베트남어)",
    "zh": "简体中文 (중국어 간체)",
    "en": "English (영어)",
    "th": "ภาษาไทย (태국어)",
    "uz": "Oʻzbekcha (우즈베크어)",
    "ne": "नेपाली (네팔어)",
    "km": "ភាសាខ្មែរ (캄보디아어)",
    "id": "Bahasa Indonesia (인도네시아어)",
    "ru": "Русский (러시아어)",
    "mn": "Монгол (몽골어)",
    "tl": "Tagalog (필리핀어)",
    "ja": "日本語 (일본어)",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


REWRITE_SYSTEM = """너는 한국 행정자료 검색어를 만드는 도우미야.
사용자의 마지막 질문을, 이전 대화와 문서 정보를 반영해서 '혼자 읽어도 뜻이 통하는 한국어 검색 문장' 하나로 바꿔.
- 사용자가 어떤 언어로 물어도 검색어는 한국어로 써.
- "그거", "이거" 같은 말은 문서 종류·세목·기관 이름으로 바꿔.
- 답을 하지 말고 검색어만 만들어.
반드시 JSON 하나만 출력: {"query": "..."}"""


ANSWER_SYSTEM = """너는 충청남도에 사는 외국인 주민이 받은 한국어 행정문서에 대해 질문에 답하는 도우미야.

[근거 규칙 — 가장 중요]
1. 답은 아래 [업로드 문서], [공식 자료], [참고 자료]에 있는 내용만으로 해. 네 일반 지식이나 추측으로 채우지 마.
2. '공식 근거'는 [공식 자료](S1, S2 ...)뿐이야.
   [참고 자료](R1, R2 ...)는 이 문서의 공식 출처로 표시할 수 없는 자료이고, [업로드 문서]도 공식 근거가 아니야.
3. status를 이렇게 정해:
   - GROUNDED: [공식 자료] 중 하나 이상이 질문에 대한 답을 직접 뒷받침함
   - UNVERIFIED: [공식 자료]에 답이 없음. [업로드 문서]나 [참고 자료]만으로 답했어도 UNVERIFIED야
   - CONFLICT: [업로드 문서]와 [공식 자료]의 내용이 다르거나, [공식 자료]끼리 서로 다름
4. 표현 규칙 (어떤 언어로 답하든 똑같이 지켜):
   - "공식 자료에 따르면", "공식적으로", "공식 안내에 따르면", "정부 자료에 따르면"처럼 공식 근거라는 표현은
     [공식 자료](S번호) 내용을 말할 때만 써. UNVERIFIED 답변에서는 아예 쓰지 마.
   - [업로드 문서] 내용은 "문서에는 ~라고 적혀 있어요"라고 말해.
   - [참고 자료] 내용은 "참고 자료에는 ~라고 나와 있지만, 이 문서의 공식 출처로 확인된 내용은 아니에요"라고 말해.
   - UNVERIFIED면 확정적으로 말하지 말고 담당기관 확인을 안내해.
5. CONFLICT면 어느 쪽이 맞다고 단정하지 말고, 두 내용을 모두 보여준 뒤 담당기관 확인을 안내해.
6. used_sources에는 답에 실제로 쓴 [공식 자료] 번호(S1, S2 ...)만 넣어. R 번호는 절대 넣지 마.
7. 개인별 자격(감면 대상인지 등)은 확정하지 말고, 조건이 있으면 "~한 경우에 해당할 수 있다"로 말해.
9. 자료에 없는 준비물·절차·기한·금액을 "보통 ~한다"처럼 덧붙이지 마. 모르면 모른다고 하고 담당기관을 안내해.
8. 업로드 문서에서 마스킹된 개인정보(***, ******-*******, [이름 가림], [주소 가림], [전화번호 가림], [계좌 가림], [여권번호 가림])를 추측하거나 다시 만들어내지 마. 가림 표시는 그대로 두고 원래 값을 채우지 마.

[답변 형식]
- answer와 suggested_questions는 반드시 {lang}로 써.
- 짧고 쉬운 문장으로. 어려운 행정용어는 풀어서 설명해.
- 출처 URL과 S1·R1 같은 자료 번호는 answer 본문에 쓰지 마. (출처는 화면에서 따로 보여줌)
- suggested_questions: 이 문서에 대해 사용자가 이어서 물어볼 만한 질문 2~3개.

반드시 JSON 하나만 출력:
{{"answer": "...", "status": "GROUNDED|UNVERIFIED|CONFLICT", "used_sources": ["S1"], "suggested_questions": ["...", "..."]}}"""


VERIFY_SYSTEM = """너는 외국인 주민이 받은 한국어 행정문서의 해석 결과를 공식 자료와 대조하는 검증 도우미야.
[대조 항목]의 문서 값 하나하나를 [공식 자료]와 비교해서 항목마다 status를 정해:
- MATCHED: [공식 자료]가 이 항목의 문서 값을 직접 뒷받침함 (예: 공식 납부 기간 안에 문서 기한이 있음)
- MISMATCH: [공식 자료]의 내용이 이 항목의 문서 값과 분명히 다름
- UNVERIFIED: [공식 자료]에 이 항목을 확인할 내용이 없거나, 확실하지 않음
규칙:
- 확실하지 않으면 UNVERIFIED. 네 일반 지식으로 판단하지 마. [공식 자료]에 적힌 내용만 근거로 써.
- 개인별 세액처럼 공식 자료에 그대로 나올 수 없는 값은, 공식 자료에 계산 기준이 있을 때만 MATCHED/MISMATCH로 판단해.
- 마스킹된 개인정보(***, ******-*******, [이름 가림], [주소 가림], [전화번호 가림], [계좌 가림], [여권번호 가림])를 추측하지 마.
- sourceIds에는 판단 근거로 실제 쓴 [공식 자료] id(s1, s2 ...)만 넣어. MATCHED·MISMATCH는 근거 id가 꼭 있어야 해.
- officialValue: 공식 자료에 적힌 해당 값을 한국어로 짧게. UNVERIFIED면 null.
- reason: 판단 이유를 한국어 한 문장으로.
- [대조 항목]에 있는 key만, 항목마다 한 번씩 답해.
반드시 JSON 하나만 출력:
{"items": [{"key": "DEADLINE", "status": "MATCHED|MISMATCH|UNVERIFIED", "officialValue": "..." 또는 null,
            "reason": "...", "sourceIds": ["s1"]}]}"""


def verify_user_message(doc: dict, values, results) -> str:
    """values: [(key, label, doc_value)] 대조할 항목, results: 공식 자료 검색 결과 (s1, s2 ... 순서)."""
    items = "\n".join(f"- {key} ({label}): {value}" for key, label, value in values)
    return (
        f"[업로드 문서]\n{document_block(doc)}\n\n"
        f"[대조 항목]\n{items}\n\n"
        f"[공식 자료]\n{sources_block(results, prefix='s')}"
    )


def document_block(doc: dict) -> str:
    """doc은 service.normalize_document()를 거친 snake_case dict."""
    agency = doc.get("agency") or {}
    d = {
        "문서 종류": doc.get("doc_type"),
        "제목": doc.get("title"),
        "발신기관": agency.get("name"),
        "기관 연락처": agency.get("phone"),
        "요약": doc.get("summary"),
        "기한": [f"{x.get('label', '')}: {x.get('date', '')}" for x in doc.get("deadlines") or []],
        "준비서류": doc.get("required_docs"),
        "용어": [f"{t.get('word', '')}: {t.get('explain', '')}" for t in doc.get("terms") or []],
    }
    d = {k: v for k, v in d.items() if v}
    text = json.dumps(d, ensure_ascii=False, indent=1)
    if doc.get("original_text"):
        text += "\n\n[원문(마스킹됨)]\n" + doc["original_text"][:3000]
    return text


def sources_block(results, prefix: str = "S", empty: str = "(검색된 공식 자료 없음)") -> str:
    if not results:
        return empty
    parts = []
    for i, r in enumerate(results, 1):
        meta = f"{prefix}{i} | {r.title or ''} | {r.agency or ''} | 지역: {r.region or ''} | 확인일: {r.checked_at}"
        if r.published_at:
            meta += f" | 기준일: {r.published_at}"
        parts.append(f"<{meta}>\n{r.text}\n</{prefix}{i}>")
    return "\n\n".join(parts)


def answer_user_message(doc: dict, results, question: str, reference=None) -> str:
    """results는 [공식 자료](S번호), reference는 출처로 표시할 수 없는 [참고 자료](R번호). 참고 자료가 없으면 구역을 빼."""
    text = f"[업로드 문서]\n{document_block(doc)}\n\n[공식 자료]\n{sources_block(results)}\n\n"
    if reference:
        text += f"[참고 자료 — 공식 출처로 표시 불가]\n{sources_block(reference, prefix='R')}\n\n"
    return text + f"[질문]\n{question}"
