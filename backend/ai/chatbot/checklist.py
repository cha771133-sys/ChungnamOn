"""S4 체크리스트·전화 대본 (DOCUMENTS_CHECKLIST_MODE=dummy|rag, 기본 dummy).

pipeline에서 verify 다음에 한 줄로 부른다:

    result.update(build_actions(result, verification, result['language']))

반환: {"checklist": [항목], "call_script": str}
- checklist 항목은 documents.services.checklist.clean_checklist()가 받는 형식
  {text, detail, docs, method, due, state, note, sourceIds}
- call_script는 기관 담당자에게 보여줄 한국어 문장 (저장할 칸은 성진님께 부탁 중, 그 전까지는 반환만)

dummy: interpret가 준 checklist를 그대로 돌려줌 (기존 동작 그대로). 대본은 규칙으로 만듦
rag:   interpret의 checklist는 무시하고 해석 결과 + verify(C1) 결과로 만든다.
  1. 규칙으로 한국어 항목 초안을 만든다 (납부 / 그 밖의 기한 / 준비서류 / 연락처 확인).
     문서에 값이 없으면 그 항목은 만들지 않는다.
  2. state는 코드가 정한다. 연결된 verify 항목이 모두 MATCHED면 CONFIRMED, 그 외는 CHECK_NEEDED.
     sourceIds는 연결된 verify 항목 중 MATCHED·MISMATCH의 id만, verification.sources에 실제 있는 것만.
  3. LLM 한 번으로 사용자 언어 번역 + CONDITIONAL 판정. 코드가 다시 검사한다.
     - CONFIRMED → CONDITIONAL로 낮추는 것만 허용 (조건 설명 note + 실제 근거 id 필요)
     - 준비서류는 verify에 항목이 없어 LLM이 공식 자료로 판정. 실제 근거 id가 있을 때만 CONFIRMED/CONDITIONAL
     - 번역문의 금액·날짜 숫자가 원문과 개수까지 같지 않으면(추가·중복·누락) 그 항목은 한국어 초안을 씀
  4. LLM이 실패하면 한국어 초안 그대로 (문서 처리는 실패시키지 않음)
개인 자격(비자·국적 등)은 판단하지 않는다. 로그에는 문서 내용을 남기지 않는다.
"""
import json
import logging
from collections import Counter
import os
import re

from .llm import get_llm
from .prompts import lang_name
from .service import normalize_document

log = logging.getLogger(__name__)

CONFIRMED, CONDITIONAL, CHECK_NEEDED = "CONFIRMED", "CONDITIONAL", "CHECK_NEEDED"
STATES = (CONFIRMED, CONDITIONAL, CHECK_NEEDED)
VERIFY_STATUSES = ("MATCHED", "MISMATCH", "UNVERIFIED")
# 납부 기한으로 보는 deadlines label (그 밖의 기한은 따로 항목을 만든다)
PAYMENT_WORDS = ("납부", "납기", "납입", "수납", "내는", "낼 ")
MAX_SOURCE_CHARS = 1500
MAX_FIELD_CHARS = 1000
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER_RE = re.compile(r"\d+(?:[.,\s]\d{3})*")

# 대본에 들어가면 안 되는 것: 마스킹 표시·전화번호·주민/외국인등록번호·계좌번호·긴 숫자·이메일
PRIVATE_PATTERNS = [re.compile(p) for p in (
    r"\*{2,}", r"\[[^\]]*가림\]", r"\d{2,4}-\d{3,4}-\d{4}", r"01[016789]\d{7,8}",
    r"(?<!\d)0\d{1,2}[\s-]+\d{3,4}[\s-]+\d{4}(?!\d)",  # 010 1234 5678, 02 2110 4092, 041 537-3001
    r"\d{6}\s*-?\s*[1-8]\d{6}", r"\d{2,6}-\d{2,6}-\d{2,8}", r"\d{9,}", r"[\w.+-]+@[\w-]+\.[\w.]+",
)]


def _mode() -> str:
    """Django settings에 있으면 그 값, 없으면 환경변수(.env). 기본 dummy."""
    try:
        from django.conf import settings

        value = getattr(settings, "DOCUMENTS_CHECKLIST_MODE", None)
    except Exception:  # Django 밖에서 import할 때
        value = None
    value = value or os.environ.get("DOCUMENTS_CHECKLIST_MODE") or "dummy"
    return value.strip().lower()


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(_text(v) for v in value if _text(v)))


def _date_ko(value: str) -> str:
    m = DATE_RE.match(value)
    return f"{int(m[1])}년 {int(m[2])}월 {int(m[3])}일" if m else value


def _due(value: str):
    return value if DATE_RE.match(value) else None


def _deadlines(doc: dict) -> list[tuple[str, str]]:
    """[(label, date)]. deadlines가 목록이 아니거나 항목 형식이 이상하면 그 기한만 뺀다."""
    raw = doc.get("deadlines")
    if not isinstance(raw, (list, tuple)):
        return []
    return [(_text(d.get("label")) or "기한", _text(d.get("date")))
            for d in raw if isinstance(d, dict) and _text(d.get("date"))]


def _is_payment(label: str) -> bool:
    return any(w in label for w in PAYMENT_WORDS)


# ── 1·2. 규칙: 초안과 state ──

def _verification(verification) -> tuple[dict, list[str]]:
    """C1 verification → ({key: (status, 근거 id 목록, docValue의 날짜 집합)}, 실제 있는 출처 id 목록).

    근거 id는 MATCHED·MISMATCH 항목의 것만, sources에 실제 있는 것만 남긴다 (#65 챗봇 규칙과 같음).
    날짜 집합은 DEADLINE 결과를 같은 날짜의 기한에만 적용하려고 docValue에서 YYYY-MM-DD를 뽑은 것.
    """
    v = verification if isinstance(verification, dict) else {}
    sources = v.get("sources") if isinstance(v.get("sources"), list) else []
    source_ids = list(dict.fromkeys(s["id"] for s in sources if isinstance(s, dict) and _text(s.get("id"))))
    items = {}
    for item in v.get("items") if isinstance(v.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        key, status = _text(item.get("key")).upper(), _text(item.get("status")).upper()
        if not key or key in items or status not in VERIFY_STATUSES:
            continue
        ids = item.get("sourceIds") if isinstance(item.get("sourceIds"), list) else []
        ids = [i for i in dict.fromkeys(_text(i) for i in ids) if i in source_ids] if status != "UNVERIFIED" else []
        items[key] = (status, ids, set(ISO_DATE_RE.findall(_text(item.get("docValue")))))
    return items, source_ids


def _linked(keys, items, date=None) -> tuple[str, list[str]]:
    """문서 값이 있는 verify 항목 keys가 모두 MATCHED면 CONFIRMED. 하나라도 없거나 아니면 CHECK_NEEDED.

    DEADLINE 결과는 그 검증 항목 docValue의 날짜와 같은 기한(date)에만 적용한다. 날짜가 다르면 없는 것으로 본다.
    """
    linked = [items.get(k) for k in keys]
    linked = [None if k == "DEADLINE" and x and date not in x[2] else x for k, x in zip(keys, linked)]
    state = CONFIRMED if linked and all(x and x[0] == "MATCHED" for x in linked) else CHECK_NEEDED
    ids = list(dict.fromkeys(i for x in linked if x for i in x[1]))
    return state, ids


def _draft(kind, text, *, keys=(), items=None, date=None, detail="", docs=(), method="", due=None, state=None,
           ids=()):
    if keys:
        state, ids = _linked(keys, items, date)
    return {"kind": kind, "text": text, "detail": detail, "docs": list(docs), "method": method, "due": due,
            "state": state or CHECK_NEEDED, "note": "", "sourceIds": list(ids)}


def _drafts(doc: dict, items: dict) -> list[dict]:
    amount = _text(doc.get("amount"))
    methods = _texts(doc.get("payment_methods"))
    deadlines = _deadlines(doc)
    pay_deadline = next((d for d in deadlines if _is_payment(d[0])), None)
    out = []

    # ① 납부: 납부 기한 + 금액 + 납부 방법
    if amount or methods or pay_deadline:
        when = f"{_date_ko(pay_deadline[1])}까지 " if pay_deadline else ""
        text = f"{when}{amount + ' ' if amount else ''}내기" if (when or amount) else "납부하기"
        keys = [k for k, has in (("AMOUNT", amount), ("DEADLINE", pay_deadline), ("PAYMENT_METHOD", methods)) if has]
        out.append(_draft("payment", text, keys=keys, items=items, date=pay_deadline[1] if pay_deadline else None,
                          detail=f"{pay_deadline[0]}: {_date_ko(pay_deadline[1])}" if pay_deadline else "",
                          method=", ".join(methods), due=_due(pay_deadline[1]) if pay_deadline else None))

    # ② 그 밖의 기한
    for label, date in deadlines:
        if (label, date) == pay_deadline:
            continue
        out.append(_draft("deadline", f"{_date_ko(date)}까지 {label} 확인하기", keys=["DEADLINE"], items=items,
                          date=date, due=_due(date)))

    # ③ 준비서류 (verify에 항목이 없어 기본 CHECK_NEEDED, LLM 단계에서 판정)
    for name in _texts(doc.get("required_docs")):
        out.append(_draft("docs", f"{name} 준비하기", docs=[name]))

    # ④ 연락처 확인: 문서에 연락처가 있고 공식 자료와 맞다고 확인되지 않았을 때만
    agency = doc.get("agency") if isinstance(doc.get("agency"), dict) else {}
    contact = items.get("AGENCY_CONTACT")
    if _text(agency.get("phone")) and not (contact and contact[0] == "MATCHED"):
        mismatch = bool(contact and contact[0] == "MISMATCH")
        out.append(_draft(
            "agency", f"{_text(agency.get('name')) or '담당기관'} 연락처 확인하기", keys=["AGENCY_CONTACT"], items=items,
            detail="문서의 연락처가 공식 자료와 달라요." if mismatch else "공식 자료에서 연락처를 확인하지 못했어요.",
            method="전화하기 전에 기관 누리집에서 공식 번호를 확인해요.",
        ))
    return out


# ── 3. LLM: 번역 + CONDITIONAL ──

SYSTEM = """너는 외국인 주민이 받은 한국어 행정문서의 할 일 목록(체크리스트)을 다듬는 도우미야.
[할 일]의 각 항목을 [공식 자료]와 비교하고, 글자는 {lang}로 옮겨.
번역 규칙:
- text·detail·method·note를 {lang}로 쉽게 써. 내용을 더하거나 빼지 마.
- 금액·날짜의 숫자는 그대로 써 (예: 6,250원 → 6,250 won, 8월 31일 → 31/8). 숫자를 바꾸거나 빼지 마.
state 규칙 (확실하지 않으면 원래 state 그대로):
- state가 CONFIRMED인 항목은, [공식 자료]에 사람·상황에 따라 달라지는 조건(예: 납부 방법에 따라 필요 없음)이 있을 때만 CONDITIONAL로 바꿔.
- kind가 docs인 항목(준비서류)은 [공식 자료]가 그 서류가 필요하다고 직접 말하면 CONFIRMED,
  경우에 따라 필요하면 CONDITIONAL. 그 외에는 CHECK_NEEDED 그대로.
- 그 밖의 CHECK_NEEDED 항목은 바꾸지 마.
- CONDITIONAL이면 note에 조건을 {lang}로 한 문장으로 써. 개인 자격(비자·국적 등)은 단정하지 마.
- CONFIRMED·CONDITIONAL로 판단했으면 sourceIds에 근거로 쓴 [공식 자료] id(s1, s2 ...)를 꼭 넣어.
- 마스킹된 개인정보(***, ******-*******, [이름 가림], [주소 가림], [전화번호 가림], [계좌 가림], [여권번호 가림])를 추측하지 마.
반드시 JSON 하나만 출력:
{"items": [{"index": 0, "text": "...", "detail": "...", "method": "...", "note": "...",
            "state": "CONFIRMED|CONDITIONAL|CHECK_NEEDED", "sourceIds": ["s1"]}]}"""


def _source_texts(verification) -> dict[str, str]:
    """verification.sources의 id(s1…) → 그 공식자료의 현재 청크 본문. AI가 쓸 수 있는 자료만."""
    from sources.models import SourceChunk, SourceDocument

    v = verification if isinstance(verification, dict) else {}
    by_doc = {}
    for s in v.get("sources") if isinstance(v.get("sources"), list) else []:
        if isinstance(s, dict) and _text(s.get("id")) and type(s.get("sourceDocumentId")) is int:
            by_doc.setdefault(s["sourceDocumentId"], s["id"])
    if not by_doc:
        return {}
    docs = {d.pk: d for d in SourceDocument.objects.filter(pk__in=list(by_doc)) if d.ai_eligible}
    texts = {}
    chunks = (SourceChunk.objects.filter(source_document_id__in=list(docs))
              .order_by("source_document_id", "chunk_order")
              .values_list("source_document_id", "source_content_hash", "chunk_text"))
    for doc_id, content_hash, text in chunks:
        if content_hash and content_hash == docs[doc_id].content_hash:
            sid = by_doc[doc_id]
            texts[sid] = (texts.get(sid, "") + "\n" + text).strip()[:MAX_SOURCE_CHARS]
    return texts


def _user_message(drafts, texts) -> str:
    todo = [{"index": i, "kind": d["kind"], "text": d["text"], "detail": d["detail"], "method": d["method"],
             "docs": d["docs"], "due": d["due"], "state": d["state"]} for i, d in enumerate(drafts)]
    sources = "\n\n".join(f"<{sid}>\n{text}\n</{sid}>" for sid, text in texts.items()) or "(공식 자료 없음)"
    return f"[할 일]\n{json.dumps(todo, ensure_ascii=False, indent=1)}\n\n[공식 자료]\n{sources}"


def _numbers(text: str) -> Counter:
    """금액·날짜 숫자를 개수까지 센다. 6,250 / 6.250 / 6 250은 모두 6250으로 본다."""
    return Counter(re.sub(r"\D", "", n) for n in NUMBER_RE.findall(text))


def _translated(draft: dict, raw: dict) -> dict | None:
    """번역된 text·detail·method. 빈 값이 있거나 숫자가 원문과 다르면 None (한국어 초안을 씀)."""
    out = {}
    for field in ("text", "detail", "method"):
        value = _text(raw.get(field))[:MAX_FIELD_CHARS]
        # 숫자가 추가·중복·누락되면 거부 (같은 숫자가 같은 개수만큼 있어야 함)
        if draft[field] and (not value or _numbers(draft[field]) != _numbers(value)):
            return None
        out[field] = value if draft[field] else ""
    return out


def _judge(draft: dict, raw: dict, valid_ids: set[str]) -> tuple[str, list[str], str]:
    """LLM이 낸 state를 검사해 (state, sourceIds, note). 규칙에 맞지 않으면 초안 그대로."""
    keep = (draft["state"], draft["sourceIds"], "")
    state = _text(raw.get("state")).upper()
    ids = raw.get("sourceIds") if isinstance(raw.get("sourceIds"), list) else []
    ids = list(dict.fromkeys(_text(i).lower() for i in ids if _text(i)))
    note = _text(raw.get("note"))[:MAX_FIELD_CHARS]
    if state not in STATES or state == draft["state"] or not ids or any(i not in valid_ids for i in ids):
        return keep
    merged = list(dict.fromkeys([*draft["sourceIds"], *ids]))
    if draft["kind"] == "docs" and draft["state"] == CHECK_NEEDED:
        if state == CONFIRMED:
            return CONFIRMED, merged, ""
        if state == CONDITIONAL and note:
            return CONDITIONAL, merged, note
        return keep
    if draft["state"] == CONFIRMED and state == CONDITIONAL and note:
        return CONDITIONAL, merged, note  # 낮추는 것만
    return keep


def _refine(drafts: list[dict], verification, language: str) -> list[dict]:
    """LLM 한 번으로 번역 + CONDITIONAL 판정. 실패하면 예외 (호출하는 쪽에서 한국어 초안으로)."""
    texts = _source_texts(verification)
    translate = language != "ko"
    if not drafts or (not translate and not texts):
        return drafts
    raw = get_llm().complete_json(
        SYSTEM.replace("{lang}", lang_name(language)),
        [{"role": "user", "content": _user_message(drafts, texts)}],
        mock_hint={"task": "checklist"},
    )
    entries = raw.get("items") if isinstance(raw, dict) else None
    by_index = {}
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and type(e.get("index")) is int and e["index"] not in by_index:
            by_index[e["index"]] = e
    out = []
    for i, draft in enumerate(drafts):
        raw_item = by_index.get(i)
        if raw_item is None:
            out.append(draft)
            continue
        item = dict(draft)
        if translate:
            item.update(_translated(draft, raw_item) or {})
        item["state"], item["sourceIds"], note = _judge(draft, raw_item, set(texts))
        item["note"] = note or draft["note"]
        out.append(item)
    return out


# ── 전화 대본 ──

def _private(line: str) -> bool:
    return any(p.search(line) for p in PRIVATE_PATTERNS)


def _subject(word: str) -> str:
    """받침이 있으면 '이', 없으면 '가'. 한글로 끝나지 않으면 '이(가)'."""
    last = word.rstrip()[-1:] or " "
    if not "가" <= last <= "힣":
        return f"{word}이(가)"
    return f"{word}{'이' if (ord(last) - 0xAC00) % 28 else '가'}"


def _question(item: dict) -> str:
    kind, docs = item["kind"], item["docs"]
    if kind == "payment":
        return "금액과 납부 기한, 낼 수 있는 방법이 맞는지"
    if kind == "deadline":
        return "기한이 맞는지"
    if kind == "docs":
        name = _subject(docs[0] if docs else "준비서류")
        return f"{name} 꼭 필요한지" if item["state"] == CONDITIONAL else f"{name} 필요한지"
    return "문의할 담당 부서와 연락처가 맞는지"


def call_script(doc: dict, items: list[dict]) -> str:
    """기관 담당자에게 보여줄 한국어 대본. 정해진 필드로만 만들고(원문·LLM 출력 안 씀), 개인정보 패턴이 있는 줄은 뺀다."""
    agency = doc.get("agency") if isinstance(doc.get("agency"), dict) else {}
    name = _text(agency.get("name"))
    if _private(name):
        name = ""
    title = _text(doc.get("title"))
    if not title or _private(title):
        title = _text(doc.get("doc_type"))
    if _private(title):
        title = ""
    about = f"「{title}」" if title else "받은 문서"
    lines = ["안녕하세요. 저는 외국인 주민이고 한국어가 서툽니다.",
             f"{name}에서 보낸 {about}에 대해 문의드립니다." if name else f"{about}에 대해 문의드립니다."]

    facts = []
    amount = _text(doc.get("amount"))
    if amount:
        facts.append(f"금액: {amount}")
    for label, date in _deadlines(doc):
        facts.append(f"{label}: {_date_ko(date)}")
    methods = _texts(doc.get("payment_methods"))
    if methods:
        facts.append(f"납부 방법: {', '.join(methods)}")
    facts = [f for f in facts if not _private(f)]
    if facts:
        lines.append("문서에 적힌 내용: " + " / ".join(facts))

    asks = list(dict.fromkeys(_question(i) for i in items if i["state"] in (CHECK_NEEDED, CONDITIONAL)))
    asks = [a for a in asks if not _private(a)]
    if asks:
        lines.append("확인하고 싶은 것: " + ", ".join(asks) + " 알려 주세요.")
    else:
        lines.append("처리 방법과 기한을 다시 한번 확인하고 싶습니다.")
    lines.append("천천히, 쉬운 말로 설명해 주시면 감사하겠습니다.")
    return "\n".join(line for line in lines if not _private(line))


# ── 진입점 ──

def _public(item: dict) -> dict:
    return {k: item[k] for k in ("text", "detail", "docs", "method", "due", "state", "note", "sourceIds")}


def build_actions(result: dict, verification: dict, language: str) -> dict:
    """해석 결과 + verify(C1) 결과 → {"checklist": [...], "call_script": str}. 예외를 내지 않는다."""
    try:
        doc = normalize_document(result if isinstance(result, dict) else {})
        items, _ = _verification(verification)
        drafts = _drafts(doc, items)
    except Exception as exc:
        log.warning("체크리스트 초안 생성 실패: %s", type(exc).__name__)
        doc, drafts = {}, []

    if _mode() != "rag":
        # dummy: 기존처럼 interpret의 checklist를 그대로 저장하게 둔다
        checklist = result.get("checklist") if isinstance(result, dict) else None
    else:
        try:
            drafts = _refine(drafts, verification, _text(language) or "ko")
        except Exception as exc:
            # 번역·판정 실패: 한국어 초안 그대로 (규칙 state)
            log.warning("체크리스트 번역·판정 실패, 한국어 초안 사용: %s", type(exc).__name__)
        checklist = [_public(d) for d in drafts]

    try:
        script = call_script(doc, drafts)
    except Exception as exc:
        log.warning("전화 대본 생성 실패: %s", type(exc).__name__)
        script = ""
    return {"checklist": checklist, "call_script": script}
