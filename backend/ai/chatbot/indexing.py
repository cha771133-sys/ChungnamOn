"""공식 자료 승인 본문(SourceDocument.content_text)을 청크로 나누고 임베딩해서 SourceChunk에 저장.

수집기(sources 앱)는 건드리지 않는다. 수집·관리자 승인 후 manage.py shell에서 실행:

    python manage.py shell -c "from ai.chatbot import build_source_chunks; print(build_source_chunks())"

sources/REFRESH.md 'AI 경계'의 자동 ACTIVE 전환 정책(#60)을 따른다.
- 대상: 관리자 승인(APPROVED)·공식 링크 있음·사용 중지(INACTIVE) 아님·본문과 해시가 있는 자료
  (승인 후 재처리 대기라 PENDING인 자료 포함)
- 자료마다 처리를 시작할 때 본문·해시·승인 정보 버전(version)·상태를 다시 읽어 기억한다(시작 시점 값)
- 현재 승인 본문 해시로 만든 청크(source_content_hash 일치)가 이미 있고 임베딩 차원도 맞으면 청크 생성을 건너뜀
- 새 청크는 기존 청크 뒤 순서에 추가하고 source_content_hash에 읽은 본문 해시를 기록.
  이전 본문의 청크는 삭제하거나 ID를 재사용하지 않음 (검색은 해시가 맞는 청크만 씀)
- 저장·완료 직전에는 같은 트랜잭션에서 자료 행을 잠그고, 완료 처리가 version을 올리기 전에
  시작 시점의 해시·version과 비교하고 관리자 승인·INACTIVE 여부를 다시 확인한다.
  청크 생성을 건너뛰고 완료만 기록하는 경로에도 같은 검사를 한다.
  - 다 맞으면: 청크 저장 + (재처리 대기였다면) 재처리 완료·자동 ACTIVE 전환·version+1을 함께 반영
  - 하나라도 다르면: 청크를 저장하지 않고(그 작업의 청크는 최신으로 인정하지 않음) 자료도 건드리지 않음.
    최신 상태·재처리 플래그·완료 기록을 덮어쓰지 않는다
- 완료 처리는 status·ai_reprocessing_required·version·updated_at만 바꾼다 (제목·기관 등 관리자 입력은 그대로)
- 임베딩은 DB 트랜잭션 밖에서 계산. 임베딩이 실패하면 예외가 나고 자료는 기존 상태 그대로
- 임베딩 결과가 비정상(개수 불일치·빈 벡터·차원 불일치·숫자 아님)이어도 같은 실패로 보고 EmbeddingResultError를 낸다.
  청크를 저장하지 않고 완료·ACTIVE 전환도 하지 않는다 (#78). 분할 결과가 0개면 예외 없이 건너뛴다(완료 안 함)
"""
import logging
import math

from .chunker import prepare_chunks
from .config import get_settings
from .embedder import embed_passages

log = logging.getLogger(__name__)


class EmbeddingResultError(RuntimeError):
    """임베딩 결과가 비정상(개수 불일치·빈 벡터·차원 불일치·숫자가 아닌 값). 임베딩 예외와 같이 취급한다 (#78)."""


def _check_vectors(document_id: int, vectors, count: int, dim: int) -> None:
    """청크 count개에 대한 임베딩 결과를 저장 전에 검사. 이상하면 EmbeddingResultError.

    임베더(Gemini·OpenAI)가 자체 검사를 해도, Ollama처럼 검사가 없는 임베더나 mock이 빈 결과를 돌려주면
    zip()이 조용히 잘려 청크 없이 완료·ACTIVE로 기록되던 문제(#78)를 여기서 막는다.
    오류 메시지에는 문서 내용·벡터 값을 넣지 않는다.
    """
    if not isinstance(vectors, (list, tuple)) or len(vectors) != count:
        got = len(vectors) if isinstance(vectors, (list, tuple)) else type(vectors).__name__
        raise EmbeddingResultError(f"공식자료 {document_id}: 임베딩 결과 개수가 다름 (청크 {count}개, 결과 {got})")
    for i, vec in enumerate(vectors):
        if not isinstance(vec, (list, tuple)) or len(vec) != dim:
            size = len(vec) if isinstance(vec, (list, tuple)) else type(vec).__name__
            raise EmbeddingResultError(f"공식자료 {document_id}: {i}번째 임베딩 차원이 {size}인데 AI_EMBED_DIM={dim}")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vec):
            raise EmbeddingResultError(f"공식자료 {document_id}: {i}번째 임베딩에 숫자가 아닌 값이 있음")

START_FIELDS = ("id", "title", "content_text", "content_hash", "version", "status", "review_status",
                "ai_reprocessing_required")


def _targets():
    """청크 생성 대상 조건. 목록을 고를 때와 자료별 처리를 시작할 때 둘 다 쓴다."""
    from sources.models import SourceDocument

    return (
        SourceDocument.objects.filter(review_status=SourceDocument.ReviewStatus.APPROVED, source_url__isnull=False)
        .exclude(status=SourceDocument.Status.INACTIVE).exclude(source_url="")
        .exclude(content_text="").exclude(content_hash="")
    )


def _has_current_chunks(document_id: int, content_hash: str, dim: int) -> bool:
    """현재 본문 해시 + 현재 임베딩 차원으로 만든 청크가 있는지.

    청크 묶음은 자료 하나 단위로 한 트랜잭션에 저장되므로, 차원이 맞는 청크가 하나라도 있으면 묶음 전체가 있다.
    임베딩 모델을 바꾼 뒤 남은 옛 차원 청크(같은 해시)는 삭제하지 않으므로 '모두 맞는지'로 보면 매번 다시 만들게 된다.
    """
    from sources.models import SourceChunk

    embeddings = (
        SourceChunk.objects.filter(source_document_id=document_id, source_content_hash=content_hash)
        .values_list("embedding", flat=True)
    )
    return any(isinstance(e, list) and len(e) == dim for e in embeddings)


def _lock_if_unchanged(start):
    """자료 행을 잠그고 시작 시점과 그대로인지 확인. 트랜잭션 안에서 호출.

    본문 해시·version이 시작 시점과 같고, 지금도 관리자 승인 상태이며 INACTIVE가 아니어야 한다.
    맞으면 잠근 행을, 아니면 None을 돌려준다.
    """
    from sources.models import SourceDocument

    locked = (
        SourceDocument.objects.select_for_update()
        .only("id", "content_hash", "version", "status", "review_status", "ai_reprocessing_required")
        .filter(pk=start.pk).first()
    )
    if (
        locked is None
        or locked.content_hash != start.content_hash
        or locked.version != start.version
        or locked.review_status != SourceDocument.ReviewStatus.APPROVED
        or locked.status == SourceDocument.Status.INACTIVE
    ):
        return None
    return locked


def _complete_and_activate(start) -> bool:
    """재처리 완료 + 자동 ACTIVE 전환 + version+1을 한 번에. 잠근 행을 확인한 같은 트랜잭션에서 호출.

    조건(시작 시점 해시·version, 승인, INACTIVE 아님, 재처리 대기)을 update의 filter에도 다시 넣어
    한 번만 반영되게 한다. 반복 실행해도 플래그가 이미 False라 version이 두 번 오르지 않는다.
    """
    from django.db.models import F
    from django.utils import timezone

    from sources.models import SourceDocument

    return bool(
        SourceDocument.objects.filter(
            pk=start.pk, content_hash=start.content_hash, version=start.version,
            review_status=SourceDocument.ReviewStatus.APPROVED, ai_reprocessing_required=True,
        ).exclude(status=SourceDocument.Status.INACTIVE).update(
            status=SourceDocument.Status.ACTIVE, ai_reprocessing_required=False,
            version=F("version") + 1, updated_at=timezone.now(),
        )
    )


def _finish(start, locked, stats) -> None:
    """검사를 통과한 자료의 재처리 완료 기록. 재처리 대기가 아니면 아무것도 바꾸지 않는다."""
    from sources.models import SourceDocument

    if not locked.ai_reprocessing_required:
        return
    was_active = locked.status == SourceDocument.Status.ACTIVE
    if _complete_and_activate(start):
        stats["completed"] += 1
        stats["activated"] += not was_active


def build_source_chunks(source_ids=None) -> dict:
    """청크·임베딩 생성과 재처리 완료·자동 ACTIVE 전환.

    반환: {"documents": 새로 청크를 만든 자료 수, "chunks": 만든 청크 수, "skipped": 이미 최신이라 청크 생성을 건너뛴 자료 수,
           "completed": 재처리 완료로 표시한 자료 수, "activated": 그중 ACTIVE로 바꾼 자료 수,
           "changed": 처리 중 본문·승인 정보 버전·승인·사용 중지가 바뀌어 저장·완료하지 않은 자료 수}
    """
    from django.db import transaction
    from django.db.models import Max

    from sources.models import SourceChunk

    dim = get_settings().embed_dim
    ids = _targets()
    if source_ids is not None:
        ids = ids.filter(pk__in=list(source_ids))
    ids = list(ids.order_by("id").values_list("id", flat=True))

    stats = {"documents": 0, "chunks": 0, "skipped": 0, "completed": 0, "activated": 0, "changed": 0}
    for document_id in ids:
        # 처리 시작 시점 값. 목록을 고른 뒤 대상에서 빠졌으면(사용 중지 등) 건너뜀
        start = _targets().only(*START_FIELDS).filter(pk=document_id).first()
        if start is None:
            continue

        if _has_current_chunks(start.pk, start.content_hash, dim):
            stats["skipped"] += 1
            if start.ai_reprocessing_required:
                with transaction.atomic():
                    locked = _lock_if_unchanged(start)
                    if locked is None:
                        stats["changed"] += 1
                        log.info("공식자료 %s: 처리 중 자료가 바뀌어 재처리 완료로 표시하지 않음", start.pk)
                    else:
                        _finish(start, locked, stats)
            continue

        chunks = prepare_chunks(start.content_text, title=start.title)
        if not chunks:
            stats["skipped"] += 1
            continue
        vectors = embed_passages(chunks)  # 트랜잭션 밖 (외부 API)
        # 비정상 결과는 임베딩 예외와 같이: 이 자료는 저장·완료하지 않고 예외를 낸다 (기존 상태·청크 유지)
        _check_vectors(start.pk, vectors, len(chunks), dim)

        with transaction.atomic():
            locked = _lock_if_unchanged(start)
            if locked is None:
                # 임베딩하는 동안 새 본문 승인·관리자 수정·승인 취소·사용 중지가 있었음.
                # 이 작업의 청크는 최신으로 인정하지 않으므로 저장하지 않고, 자료도 건드리지 않음
                stats["changed"] += 1
                log.info("공식자료 %s: 처리 중 자료가 바뀌어 청크를 저장하지 않음", start.pk)
                continue
            offset = (SourceChunk.objects.filter(source_document_id=start.pk)
                      .aggregate(m=Max("chunk_order"))["m"])
            offset = 0 if offset is None else offset + 1
            created = SourceChunk.objects.bulk_create([
                SourceChunk(source_document_id=start.pk, chunk_order=offset + i, chunk_text=text,
                            embedding=list(vec), source_content_hash=start.content_hash)
                for i, (text, vec) in enumerate(zip(chunks, vectors, strict=True))
            ])
            _finish(start, locked, stats)
        # 반환 통계는 실제 저장한 개수 (#78)
        stats["documents"] += 1
        stats["chunks"] += len(created)
        log.info("공식자료 %s: 청크 %d개 저장", start.pk, len(created))
    return stats
