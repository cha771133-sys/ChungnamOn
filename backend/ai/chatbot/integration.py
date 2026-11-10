"""Django 앱의 AI 교체 자리 두 곳과 이 모듈을 잇는 어댑터.

- chatbot.services.ai_client.generate_answer (CHATBOT_AI_MODE=rag) → chat_answer()
- documents.services.pipeline.verify      (DOCUMENTS_VERIFY_MODE=rag) → verify_interpretation()

두 함수 모두 호출하는 쪽의 입출력 계약(backend/chatbot/README.md, pipeline.verify docstring)을 그대로 따른다.
Django 모델은 함수 안에서 가져온다 (Django 없이 ai 모듈을 import할 수 있게).
"""
import logging

from . import prompts
from .llm import get_llm
from .retriever import SearchResult, search
from .service import GroundingStatus, answer_with_results, normalize_document, regions_for

log = logging.getLogger(__name__)

VERIFY_LIMIT = 10           # verify()가 돌려주는 관련 공식 자료 최대 개수
VERIFY_CHUNK_FACTOR = 4     # 자료 10개를 채우려고 청크는 넉넉히 검색
VERIFY_STATUSES = ("MATCHED", "MISMATCH", "UNVERIFIED")
SUGGESTION_LIMIT, SUGGESTION_MAX_CHARS = 5, 200


# ---------- 챗봇 (ai_client.generate_answer) ----------
def _history_to_messages(history) -> list[dict]:
    """chatbot 앱 이력 [{question, answer, ...}] (과거→현재) → [{role, content}]."""
    out = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        out.append({"role": "user", "content": str(item.get("question") or "")})
        out.append({"role": "assistant", "content": str(item.get("answer") or "")})
    return out


def _suggestions(items) -> list[str]:
    out = []
    for q in items:
        q = str(q).strip()
        if q and len(q) <= SUGGESTION_MAX_CHARS and q not in out:
            out.append(q)
    return out[:SUGGESTION_LIMIT]


def chat_answer(question, interpretation, history, language, official_sources) -> dict:
    """chatbot 앱 계약 형식으로 답한다.

    - 검색은 공식 자료 전체에서 하지만, 출처로 내는 건 official_sources 안의 sourceDocumentId뿐
    - 실제로 쓴 자료가 그 밖에만 있으면 UNVERIFIED (sources=[])
    - CONFLICT는 문서 검증 결과도 MISMATCH일 때만 MISMATCH, 아니면 UNVERIFIED
    """
    interpretation = interpretation or {}
    doc = normalize_document(interpretation)
    allowed = {s["sourceDocumentId"] for s in official_sources or []}
    # official_sources 밖의 검색 결과는 프롬프트에서 [참고 자료]로 분리 (출처 표시·'공식' 표현 금지)
    answer, status, used, suggested, _ = answer_with_results(
        doc, question, history=_history_to_messages(history), language=language, official_ids=allowed,
    )

    ids = list(dict.fromkeys(r.source_document_id for r in used if r.source_document_id in allowed))
    verification = interpretation.get("verification")
    verification_status = verification.get("status") if isinstance(verification, dict) else None

    if ids and status == GroundingStatus.GROUNDED:
        final = "GROUNDED"
    elif ids and status == GroundingStatus.CONFLICT and verification_status == "MISMATCH":
        final = "MISMATCH"
    else:
        final, ids = "UNVERIFIED", []
    if used and not ids:
        log.info("챗봇: 연결된 공식 근거 밖의 자료만 사용해서 UNVERIFIED로 저장")

    return {
        "answer": answer,
        "language": language,
        "sources": [{"sourceDocumentId": i} for i in ids],
        "grounded": final == "GROUNDED",
        "status": final,
        "suggestedQuestions": _suggestions(suggested),
    }


# ---------- 문서 검증 (pipeline.verify) ----------
def _identifiable(doc: dict) -> bool:
    agency = doc.get("agency") or {}
    return bool(str(agency.get("name") or "").strip() or str(doc.get("region") or "").strip())


def _verify_query(doc: dict) -> str:
    parts = [doc.get("title"), doc.get("doc_type"), (doc.get("agency") or {}).get("name")]
    parts += [s for s in doc.get("summary") or [] if isinstance(s, str)]
    parts += [str(x.get("label", "")) for x in doc.get("deadlines") or [] if isinstance(x, dict)]
    parts += [s for s in doc.get("required_docs") or [] if isinstance(s, str)]
    return " ".join(str(p) for p in parts if p)[:600]


def _official_documents(ids):
    """검색된 ID 중 AI 근거로 쓸 수 있는 자료만 {id: SourceDocument} 로.

    SourceDocument.ai_eligible(공식 링크·ACTIVE·관리자 승인·재처리 완료) + 확인일 있음 + 공식 HTTPS URL.
    """
    from sources.models import SourceDocument
    from sources.services.errors import CollectionError
    from sources.services.source_registry import validate_source_url

    docs = SourceDocument.objects.filter(
        pk__in=ids, status=SourceDocument.Status.ACTIVE, collected_at__isnull=False,
        review_status=SourceDocument.ReviewStatus.APPROVED, ai_reprocessing_required=False,
        source_url__isnull=False,
    ).only("id", "title", "source_url", "collected_at", "published_at", "status", "review_status",
           "ai_reprocessing_required")
    out = {}
    for d in docs:
        if not d.ai_eligible:
            continue
        try:
            validate_source_url(d.source_url)
        except CollectionError:
            continue
        out[d.pk] = d
    return out


def related_sources(doc: dict, limit: int = VERIFY_LIMIT) -> list[tuple[SearchResult, object]]:
    """해석 결과와 관련된 공식 자료. 자료(SourceDocument)당 점수가 가장 높은 청크 하나씩, 관련도 순 최대 limit개.

    doc은 normalize_document()를 거친 dict. 반환: [(청크 검색 결과, SourceDocument), ...]
    """
    hits = search(_verify_query(doc), regions=regions_for(doc), top_k=limit * VERIFY_CHUNK_FACTOR)
    best: dict[int, SearchResult] = {}
    for h in hits:  # 점수 내림차순
        if h.source_document_id is not None and h.source_document_id not in best:
            best[h.source_document_id] = h
    official = _official_documents(list(best))
    return [(h, official[i]) for i, h in best.items() if i in official][:limit]


# C1 항목 (key, label). 문서에 값이 없는 항목은 결과에서 뺀다
VERIFY_ITEMS = (
    ("AMOUNT", "납부 금액"),
    ("DEADLINE", "납부 기한"),
    ("PAYMENT_METHOD", "납부 방법"),
    ("AGENCY_CONTACT", "문의 연락처"),
)
VALUE_MAX_CHARS = 500
REASON_NO_IDENTITY = "발신기관·지역을 확인할 수 없어 공식 자료를 찾지 않았어요."
REASON_NO_SOURCES = "이 문서와 관련된 공식 자료를 찾지 못했어요."
REASON_NO_EVIDENCE = "공식 자료에서 이 내용을 확인하지 못했어요."


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _document_values(doc: dict) -> list[tuple[str, str, str]]:
    """해석 결과에서 대조할 문서 값. [(key, label, doc_value)] — 값이 없는 항목은 빠짐.

    - AMOUNT: amount (문자열 또는 null)
    - DEADLINE: deadlines [{label, date}] → "납부 마감 2026-08-31"
    - PAYMENT_METHOD: payment_methods (문자열 목록, 문자열 하나도 허용)
    - AGENCY_CONTACT: agency.phone
    """
    deadlines = [
        " ".join(p for p in (_text(d.get("label")), _text(d.get("date"))) if p)
        for d in doc.get("deadlines") or [] if isinstance(d, dict) and _text(d.get("date"))
    ]
    raw_methods = doc.get("payment_methods")
    if isinstance(raw_methods, str):
        raw_methods = [raw_methods]  # 문자열 하나가 글자 단위로 쪼개지지 않게
    elif not isinstance(raw_methods, (list, tuple)):
        raw_methods = []  # null·숫자 등 이상한 타입이면 PAYMENT_METHOD 항목 제외
    methods = [_text(m) for m in raw_methods if _text(m)]
    values = {
        "AMOUNT": _text(doc.get("amount")),
        "DEADLINE": ", ".join(deadlines),
        "PAYMENT_METHOD": ", ".join(dict.fromkeys(methods)),
        "AGENCY_CONTACT": _text((doc.get("agency") or {}).get("phone")),
    }
    return [(key, label, values[key]) for key, label in VERIFY_ITEMS if values[key]]


def _overall(items: list[dict]) -> str:
    """하나라도 MISMATCH면 MISMATCH, 모두 MATCHED면 MATCHED, 그 외(항목 0개 포함) UNVERIFIED."""
    statuses = [i["status"] for i in items]
    if "MISMATCH" in statuses:
        return "MISMATCH"
    if statuses and all(s == "MATCHED" for s in statuses):
        return "MATCHED"
    return "UNVERIFIED"


def _item(key, label, doc_value, status="UNVERIFIED", official_value=None, reason=REASON_NO_EVIDENCE, source_ids=()):
    return {
        "key": key, "label": label, "docValue": doc_value, "officialValue": official_value,
        "status": status, "reason": reason, "sourceIds": list(source_ids),
    }


def _judge_item(key, label, doc_value, raw: dict | None, valid_ids: set[str]) -> dict:
    """LLM이 낸 항목 판정을 검사. 이상하면 그 항목만 UNVERIFIED (챗봇 decide_status와 같은 원칙).

    - status가 MATCHED/MISMATCH/UNVERIFIED가 아니면 UNVERIFIED
    - sourceIds 중 실제 sources에 없는 id가 하나라도 있으면 UNVERIFIED
    - MATCHED/MISMATCH인데 근거(sourceIds)나 공식 값이 없으면 UNVERIFIED
    """
    if not isinstance(raw, dict):
        return _item(key, label, doc_value)
    status = _text(raw.get("status")).upper()
    ids = raw.get("sourceIds", raw.get("source_ids"))
    ids = ids if isinstance(ids, list) else []
    ids = list(dict.fromkeys(i.strip().lower() for i in ids if isinstance(i, str) and i.strip()))
    official_value = _text(raw.get("officialValue", raw.get("official_value")))[:VALUE_MAX_CHARS]
    reason = _text(raw.get("reason"))[:VALUE_MAX_CHARS]
    if status not in VERIFY_STATUSES or any(i not in valid_ids for i in ids):
        return _item(key, label, doc_value)
    if status == "UNVERIFIED":
        return _item(key, label, doc_value, reason=reason or REASON_NO_EVIDENCE)
    if not ids or not official_value:
        return _item(key, label, doc_value)
    return _item(key, label, doc_value, status, official_value, reason, ids)


def _unverified_result(values, reason: str) -> dict:
    items = [_item(key, label, value, reason=reason) for key, label, value in values]
    return {"overall": "UNVERIFIED", "status": "UNVERIFIED", "items": items, "sources": []}


def verify_interpretation(result: dict, limit: int = VERIFY_LIMIT) -> dict:
    """해석 결과를 공식 자료와 항목별로 대조한다 (C1 형식, DOCUMENTS_VERIFY_MODE=rag).

    {
      "overall": "MATCHED"|"MISMATCH"|"UNVERIFIED",   # items로 코드에서 계산
      "status": overall과 같은 값 (기존 코드·챗봇 호환),
      "items": [{"key": "AMOUNT"|"DEADLINE"|"PAYMENT_METHOD"|"AGENCY_CONTACT", "label", "docValue",
                 "officialValue": str|None, "status", "reason", "sourceIds": ["s1", ...]}],
      "sources": [{"id": "s1", "title", "url", "checkedAt": "YYYY-MM-DD", "publishedAt"(있으면),
                   "sourceDocumentId": int}]   # 관련도 순 최대 limit개, 결과에 한 번만
    }
    문서에 값이 없는 항목은 items에서 뺀다. 발신기관·지역을 모르거나 관련 자료가 없으면 항목은 모두 UNVERIFIED.
    검색·임베딩·LLM 등 어떤 오류도 여기서 잡아 overall=UNVERIFIED, items=[], sources=[]로 돌려준다
    (문서 처리는 계속됨).
    """
    try:
        return _verify(result, limit)
    except Exception as exc:
        # 오류 원문에는 문서 내용이 섞일 수 있어서 종류만 남긴다
        log.warning("공식 자료 항목별 대조 실패, UNVERIFIED로 처리: %s", type(exc).__name__)
        return {"overall": "UNVERIFIED", "status": "UNVERIFIED", "items": [], "sources": []}


def _verify(result: dict, limit: int) -> dict:
    from django.utils import timezone

    doc = normalize_document(result)
    values = _document_values(doc)
    if not values:
        return _unverified_result([], REASON_NO_EVIDENCE)
    if not _identifiable(doc):
        return _unverified_result(values, REASON_NO_IDENTITY)
    related = related_sources(doc, limit)
    if not related:
        return _unverified_result(values, REASON_NO_SOURCES)

    sources = []
    for i, (_, d) in enumerate(related, 1):
        source = {
            "id": f"s{i}",
            "title": d.title,
            "url": d.source_url,
            "checkedAt": timezone.localdate(d.collected_at).isoformat(),
        }
        if d.published_at:
            source["publishedAt"] = d.published_at.isoformat()
        source["sourceDocumentId"] = d.pk
        sources.append(source)
    source_ids = [s["id"] for s in sources]

    raw = get_llm().complete_json(
        prompts.VERIFY_SYSTEM,
        [{"role": "user", "content": prompts.verify_user_message(doc, values, [h for h, _ in related])}],
        mock_hint={"task": "verify", "item_keys": [key for key, _, _ in values], "source_ids": source_ids},
    )
    entries = raw.get("items") if isinstance(raw, dict) else None
    by_key = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            by_key.setdefault(_text(entry.get("key")).upper(), entry)
    items = [_judge_item(key, label, value, by_key.get(key), set(source_ids)) for key, label, value in values]
    overall = _overall(items)
    return {"overall": overall, "status": overall, "items": items, "sources": sources}
