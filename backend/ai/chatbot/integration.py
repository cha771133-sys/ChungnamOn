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
    ).only("id", "title", "source_url", "collected_at", "status", "review_status", "ai_reprocessing_required")
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


def verify_interpretation(result: dict, limit: int = VERIFY_LIMIT) -> dict:
    """pipeline.verify 출력 형식 + 각 출처에 sourceDocumentId.

    {"status": "MATCHED"|"MISMATCH"|"UNVERIFIED",
     "sources": [{"title", "url", "checkedAt": "YYYY-MM-DD", "sourceDocumentId": int}]}  # 최대 limit개, 관련도 순
    발신기관·지역을 모르면 검색하지 않고 UNVERIFIED. 검색·LLM 오류는 그대로 던지고,
    pipeline.verify가 받아서 UNVERIFIED로 바꾼다 (문서 처리는 실패시키지 않음).
    """
    from django.utils import timezone

    doc = normalize_document(result)
    if not _identifiable(doc):
        return {"status": "UNVERIFIED", "sources": []}
    related = related_sources(doc, limit)
    if not related:
        return {"status": "UNVERIFIED", "sources": []}

    hits = [h for h, _ in related]
    raw = get_llm().complete_json(
        prompts.VERIFY_SYSTEM,
        [{"role": "user", "content": prompts.verify_user_message(doc, hits)}],
        mock_hint={"task": "verify", "source_ids": [f"S{i}" for i in range(1, len(hits) + 1)]},
    )
    status = str(raw.get("status", "")).upper()
    if status not in VERIFY_STATUSES:
        status = "UNVERIFIED"
    return {
        "status": status,
        "sources": [
            {
                "title": d.title,
                "url": d.source_url,
                "checkedAt": timezone.localdate(d.collected_at).isoformat(),
                "sourceDocumentId": d.pk,
            }
            for _, d in related
        ],
    }
