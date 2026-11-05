"""문서 챗봇 답변 생성 (명세서 4장 ⑥, R5 · F5-1, F5-2).

chatbot 앱(B 담당)의 질문 API가 호출하는 함수:

    from ai.chatbot import generate_answer
    result = generate_answer(document, message, history=history, language="vi")

DB 저장·권한 확인·대화 이력 조회는 호출하는 쪽(Django)이 하고, 이 함수는 계산만 한다.
"""
import logging
import re
from enum import Enum

from . import prompts
from .config import CHUNGNAM_REGIONS, get_settings
from .llm import get_llm
from .retriever import SearchResult, search

log = logging.getLogger(__name__)


class GroundingStatus(str, Enum):
    GROUNDED = "GROUNDED"      # 근거 기반: 공식 자료가 답을 뒷받침함
    UNVERIFIED = "UNVERIFIED"  # 미확인: 공식 근거를 못 찾음
    CONFLICT = "CONFLICT"      # 불일치: 문서 ↔ 공식자료, 또는 공식자료끼리 다름


# ---------- 입력 정리 ----------
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(d):
    if isinstance(d, dict):
        return {_CAMEL.sub("_", k).lower(): _snake(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_snake(x) for x in d]
    return d


def normalize_document(document: dict) -> dict:
    """해석 결과 dict를 snake_case로 통일. camelCase(API 응답 그대로)로 넘겨도 됨."""
    doc = _snake(dict(document or {}))
    if isinstance(doc.get("agency"), str):
        doc["agency"] = {"name": doc["agency"]}
    doc.setdefault("agency", {})
    return doc


def regions_for(doc: dict) -> list[str]:
    """검색 범위: [발신기관 시군, 충청남도, 충남, 전국]. 시군을 못 찾으면 시군만 빠짐.

    sources 앱은 도 단위 지역을 '충청남도'로 저장하고, 예전 샘플 자료는 '충남'을 써서 둘 다 넣는다.
    """
    region = doc.get("region")
    name = (doc.get("agency") or {}).get("name") or ""
    if not region and name:
        region = next((r for r in CHUNGNAM_REGIONS if r in name), None) or next(
            (r for r in CHUNGNAM_REGIONS if r[:-1] in name), None  # "아산 세무과"처럼 시·군이 빠진 경우
        )
    province = ["충청남도", "충남", "전국"]
    out = [region] if region and region not in province else []
    return out + province


def _history_messages(history: list[dict] | None) -> list[dict]:
    """[{role, content}] → LLM 규칙(user로 시작, 번갈아)에 맞게 정리. 최근 N턴만 씀."""
    turns = get_settings().history_turns
    msgs: list[dict] = []
    for h in (history or [])[-turns * 2 :]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        content = str(h.get("content") or h.get("text") or "").strip()
        if not content:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + content
        else:
            msgs.append({"role": role, "content": content})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":  # 답 없는 질문은 버림 (새 질문이 user로 붙음)
        msgs.pop()
    return msgs


# ---------- 검색 ----------
def _search_query(doc: dict, question: str, lang: str, history_msgs: list[dict]) -> str:
    base = f"{doc.get('title') or doc.get('doc_type') or ''} {question}".strip()
    if lang == "ko" and not history_msgs:
        return base  # 한국어 단독 질문은 LLM 없이 바로 검색
    try:
        ctx = (
            f"[문서] {doc.get('doc_type') or ''} / {doc.get('title') or ''} / "
            f"발신: {(doc.get('agency') or {}).get('name') or ''}\n[질문] {question}"
        )
        out = get_llm().complete_json(
            prompts.REWRITE_SYSTEM,
            [*history_msgs, {"role": "user", "content": ctx}],
            mock_hint={"task": "rewrite", "fallback_query": base},
        )
        return str(out.get("query", "")).strip() or base
    except Exception as e:  # 검색어 변환이 실패해도 원래 질문으로 계속
        log.warning("검색어 변환 실패, 원문으로 검색: %s", e)
        return base


# ---------- 상태 판정 ----------
def decide_status(raw: dict, results: list[SearchResult]) -> tuple[GroundingStatus, list[SearchResult]]:
    """LLM이 고른 상태를 그대로 믿지 않고 규칙으로 한 번 더 거름.

    - 검색 결과가 없으면 무조건 UNVERIFIED
    - GROUNDED·CONFLICT인데 실제로 쓴 출처가 없으면 UNVERIFIED로 내림
    - 출처는 LLM이 '썼다'고 한 것만 내보냄
    """
    try:
        status = GroundingStatus(str(raw.get("status", "")).upper())
    except ValueError:
        status = GroundingStatus.UNVERIFIED
    used: list[SearchResult] = []
    for sid in raw.get("used_sources") or []:
        m = re.fullmatch(r"S?(\d+)", str(sid).strip(), re.I)
        if m:
            i = int(m.group(1)) - 1
            if 0 <= i < len(results) and results[i] not in used:
                used.append(results[i])
    if not results or not used or status == GroundingStatus.UNVERIFIED:
        return GroundingStatus.UNVERIFIED, []
    return status, used


def _source_dict(r: SearchResult) -> dict:
    return {
        "title": r.title,
        "url": r.url,
        "agency": r.agency,
        "region": r.region,
        "checked_at": r.checked_at.isoformat(),
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "chunk_id": r.chunk_id,
    }


def _result(answer, status, sources, doc, suggested, lang) -> dict:
    agency = doc.get("agency") or {}
    contact = None
    if status != GroundingStatus.GROUNDED and (agency.get("name") or agency.get("phone")):
        contact = {"name": agency.get("name"), "phone": agency.get("phone")}
    return {
        "answer": answer,
        "grounding_status": status.value,
        "sources": sources,
        "agency_contact": contact,
        "suggested_questions": suggested[:3],
        "language": lang,
    }


# ---------- 진입점 ----------
def generate_answer(
    document: dict,
    message: str,
    *,
    history: list[dict] | None = None,
    language: str | None = None,
) -> dict:
    """문서 기반 질문에 답한다.

    document : 해석 결과 (명세서 4장 ③ 형식, snake_case/camelCase 둘 다 됨)
               쓰는 키: title, doc_type, language, region, original_text(마스킹됨), summary,
                        terms, deadlines, required_docs, agency{name, phone}
    message  : 사용자 질문 (모국어 그대로)
    history  : 이 문서의 이전 대화 [{"role": "user"|"assistant", "content": str}, ...] 오래된 순
    language : 답변 언어 코드 (vi, zh, en ...). 없으면 document.language, 그것도 없으면 ko

    반환 (snake_case, camelCase 전역 설정이 켜지면 응답에서 자동 변환됨):
    {
      "answer": str,
      "grounding_status": "GROUNDED" | "UNVERIFIED" | "CONFLICT",
      "sources": [{"title", "url", "agency", "region", "checked_at", "published_at", "chunk_id"}],
      "agency_contact": {"name", "phone"} | None,   # GROUNDED가 아닐 때만
      "suggested_questions": [str, ...],
      "language": str,
    }
    LLM 호출이 실패하면 RuntimeError를 던진다 (호출하는 쪽에서 503 등으로 처리).
    """
    doc = normalize_document(document)
    answer, status, used, suggested, lang = answer_with_results(doc, message, history=history, language=language)
    return _result(answer, status, [_source_dict(r) for r in used], doc, suggested, lang)


def answer_with_results(
    doc: dict,
    message: str,
    *,
    history: list[dict] | None = None,
    language: str | None = None,
) -> tuple[str, GroundingStatus, list[SearchResult], list[str], str]:
    """generate_answer의 본체. 출처를 SearchResult 그대로 돌려줘서 어댑터가 DB ID를 쓸 수 있게 함.

    doc은 normalize_document()를 거친 dict. 반환: (answer, status, used_results, suggested, lang)
    """
    message = (message or "").strip()
    if not message:
        raise ValueError("message가 비어 있어")
    lang = language or doc.get("language") or "ko"
    history_msgs = _history_messages(history)

    regions = regions_for(doc)
    query = _search_query(doc, message, lang, history_msgs)
    results = search(query, regions=regions)
    log.info("챗봇 검색어=%r 지역=%s 결과=%d", query, regions, len(results))

    system = prompts.ANSWER_SYSTEM.format(lang=prompts.lang_name(lang))
    user_msg = prompts.answer_user_message(doc, results, message)
    try:
        raw = get_llm().complete_json(
            system,
            [*history_msgs, {"role": "user", "content": user_msg}],
            mock_hint={
                "task": "answer",
                "source_ids": [f"S{i}" for i in range(1, len(results) + 1)],
                "first_source_text": results[0].text if results else "",
            },
        )
    except Exception as e:
        log.exception("챗봇 LLM 호출 실패")
        raise RuntimeError("답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요.") from e

    status, used = decide_status(raw, results)
    answer = str(raw.get("answer", "")).strip() or "답변을 만들지 못했어요. 담당기관에 문의해 주세요."
    suggested = [str(q) for q in (raw.get("suggested_questions") or [])]
    return answer, status, used, suggested, lang


def generate_answer_dummy(
    document: dict,
    message: str,
    *,
    history: list[dict] | None = None,
    language: str | None = None,
) -> dict:
    """더미. generate_answer와 입력·출력 형식이 같다. 검색·LLM 없이 바로 돌아감.

    질문에 '기한'·'deadline'이 들어가면 GROUNDED, '감면'이 들어가면 CONFLICT, 나머지는 UNVERIFIED.
    프론트가 세 가지 상태 화면을 다 만들어 볼 수 있게 일부러 나눠 둠.
    """
    doc = normalize_document(document)
    lang = language or doc.get("language") or "ko"
    q = (message or "").lower()
    sample = {
        "title": "[더미] 충청남도 지방세 납부 방법",
        "url": "https://example.com/dummy/chungnam-local-tax",
        "agency": "충청남도 세정과",
        "region": "충남",
        "checked_at": "2026-10-30",
        "published_at": None,
        "chunk_id": 0,
    }
    if any(k in q for k in ("기한", "마감", "deadline", "hạn")):
        return _result("[더미] 기한이 지나면 가산세가 붙어요.", GroundingStatus.GROUNDED, [sample], doc,
                       ["어디서 낼 수 있나요?"], lang)
    if any(k in q for k in ("감면", "miễn", "exempt")):
        other = {**sample, "title": "[더미] 아산시 시세 감면 조례", "url": "https://example.com/dummy/asan", "region": "아산시", "chunk_id": 1}
        return _result("[더미] 문서와 공식 자료의 감면 기준이 서로 달라요. 담당기관에 확인해 주세요.",
                       GroundingStatus.CONFLICT, [sample, other], doc, ["담당기관 연락처가 어디예요?"], lang)
    return _result("[더미] 공식 자료에서 확인되지 않았어요. 담당기관에 문의해 주세요.", GroundingStatus.UNVERIFIED, [],
                   doc, ["기한이 지나면 어떻게 되나요?"], lang)
