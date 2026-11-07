"""공식 자료 승인 본문(SourceDocument.content_text)을 청크로 나누고 임베딩해서 SourceChunk에 저장.

수집기(sources 앱)는 건드리지 않는다. 수집·관리자 승인 후 manage.py shell에서 실행:

    python manage.py shell -c "from ai.chatbot import build_source_chunks; print(build_source_chunks())"

sources/REFRESH.md 'AI 경계' 계약을 따른다.
- 대상: 관리자 승인(APPROVED)·공식 링크 있음·사용 중지(INACTIVE) 아님·본문과 해시가 있는 자료
  (승인 후 재처리 대기라 PENDING인 자료 포함)
- 현재 승인 본문 해시로 만든 청크(source_content_hash 일치)가 이미 있고 임베딩 차원도 맞으면 건너뜀
- 새 청크는 기존 청크 뒤 순서에 추가하고 source_content_hash에 읽은 본문 해시를 기록.
  이전 본문의 청크는 삭제하거나 ID를 재사용하지 않음 (검색은 해시가 맞는 청크만 씀)
- 저장 직전에 자료 행을 잠그고 본문 해시를 다시 확인. 그사이 새 본문이 승인됐으면 저장하지 않음
- 재처리 대기(ai_reprocessing_required)였던 자료는 같은 트랜잭션에서 플래그를 해제하고 version을 올림
  (화면을 먼저 연 관리자 저장이 덮어쓰지 않게). ACTIVE 전환은 하지 않음 — 관리자가 Admin에서 결정
- 임베딩은 DB 트랜잭션 밖에서 계산
"""
import logging

from .chunker import prepare_chunks
from .config import get_settings
from .embedder import embed_passages

log = logging.getLogger(__name__)


def _has_current_chunks(document_id: int, content_hash: str, dim: int) -> bool:
    """현재 본문 해시로 만든 청크가 있고 모두 현재 임베딩 차원인지."""
    from sources.models import SourceChunk

    embeddings = list(
        SourceChunk.objects.filter(source_document_id=document_id, source_content_hash=content_hash)
        .values_list("embedding", flat=True)
    )
    return bool(embeddings) and all(isinstance(e, list) and len(e) == dim for e in embeddings)


def _complete_reprocessing(document_id: int, content_hash: str) -> bool:
    """본문 해시를 다시 확인하고 재처리 플래그를 해제. 트랜잭션 안에서 호출."""
    from django.db.models import F
    from django.utils import timezone

    from sources.models import SourceDocument

    return bool(
        SourceDocument.objects.filter(
            pk=document_id, content_hash=content_hash, ai_reprocessing_required=True,
            review_status=SourceDocument.ReviewStatus.APPROVED,
        ).update(ai_reprocessing_required=False, version=F("version") + 1, updated_at=timezone.now())
    )


def build_source_chunks(source_ids=None) -> dict:
    """청크·임베딩 생성.

    반환: {"documents": 새로 청크를 만든 자료 수, "chunks": 만든 청크 수, "skipped": 이미 최신이라 그대로 둔 자료 수,
           "completed": 재처리 완료로 표시한 자료 수, "changed": 처리 중 본문이 바뀌어 저장하지 않은 자료 수}
    """
    from django.db import transaction
    from django.db.models import Max

    from sources.models import SourceChunk, SourceDocument

    dim = get_settings().embed_dim
    qs = (
        SourceDocument.objects.filter(review_status=SourceDocument.ReviewStatus.APPROVED, source_url__isnull=False)
        .exclude(status=SourceDocument.Status.INACTIVE).exclude(source_url="")
        .exclude(content_text="").exclude(content_hash="")
    )
    if source_ids is not None:
        qs = qs.filter(pk__in=list(source_ids))

    stats = {"documents": 0, "chunks": 0, "skipped": 0, "completed": 0, "changed": 0}
    for doc in qs.order_by("id").only("id", "title", "content_text", "content_hash", "ai_reprocessing_required"):
        content_hash = doc.content_hash
        if _has_current_chunks(doc.pk, content_hash, dim):
            stats["skipped"] += 1
            if doc.ai_reprocessing_required:
                with transaction.atomic():
                    stats["completed"] += _complete_reprocessing(doc.pk, content_hash)
            continue

        chunks = prepare_chunks(doc.content_text, title=doc.title)
        if not chunks:
            stats["skipped"] += 1
            continue
        vectors = embed_passages(chunks)  # 트랜잭션 밖 (외부 API)

        with transaction.atomic():
            locked = SourceDocument.objects.select_for_update().only("id", "content_hash").get(pk=doc.pk)
            if locked.content_hash != content_hash:
                # 임베딩하는 동안 새 본문이 승인됨. 이전 본문 청크를 새 해시로 저장하지 않음
                stats["changed"] += 1
                log.info("공식자료 %s: 처리 중 본문이 바뀌어 저장하지 않음", doc.pk)
                continue
            start = (SourceChunk.objects.filter(source_document_id=doc.pk)
                     .aggregate(m=Max("chunk_order"))["m"])
            start = 0 if start is None else start + 1
            SourceChunk.objects.bulk_create([
                SourceChunk(source_document_id=doc.pk, chunk_order=start + i, chunk_text=text,
                            embedding=vec, source_content_hash=content_hash)
                for i, (text, vec) in enumerate(zip(chunks, vectors))
            ])
            stats["completed"] += _complete_reprocessing(doc.pk, content_hash)
        stats["documents"] += 1
        stats["chunks"] += len(chunks)
        log.info("공식자료 %s: 청크 %d개 저장", doc.pk, len(chunks))
    return stats
