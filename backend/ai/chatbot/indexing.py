"""공식 자료 본문(SourceDocument.content_text)을 청크로 나누고 임베딩해서 SourceChunk에 저장.

수집기(sources 앱)는 건드리지 않는다. 수집 후 manage.py shell에서 실행:

    python manage.py shell -c "from ai.chatbot.indexing import build_source_chunks; print(build_source_chunks())"

- 이미 청크가 있는 자료는 건너뜀. 본문이 바뀌었거나 임베딩 모델을 바꿨으면 rebuild=True
- INACTIVE 자료와 본문이 빈 자료는 대상에서 뺌 (검색은 ACTIVE만 함)
- 임베딩은 DB 트랜잭션 밖에서 계산하고, 자료 하나 단위로 기존 청크 삭제·새 청크 저장을 묶음
"""
import logging

from .chunker import prepare_chunks
from .embedder import embed_passages

log = logging.getLogger(__name__)


def build_source_chunks(source_ids=None, *, rebuild: bool = False) -> dict:
    """청크·임베딩 생성. 반환: {"documents": 처리한 자료 수, "chunks": 만든 청크 수, "skipped": 건너뛴 자료 수}"""
    from django.db import transaction

    from sources.models import SourceChunk, SourceDocument

    qs = SourceDocument.objects.exclude(status=SourceDocument.Status.INACTIVE).exclude(content_text="")
    if source_ids is not None:
        qs = qs.filter(pk__in=list(source_ids))

    stats = {"documents": 0, "chunks": 0, "skipped": 0}
    for doc in qs.order_by("id").only("id", "title", "content_text"):
        if not rebuild and SourceChunk.objects.filter(source_document_id=doc.pk).exists():
            stats["skipped"] += 1
            continue
        chunks = prepare_chunks(doc.content_text, title=doc.title)
        if not chunks:
            stats["skipped"] += 1
            continue
        vectors = embed_passages(chunks)
        with transaction.atomic():
            SourceChunk.objects.filter(source_document_id=doc.pk).delete()
            SourceChunk.objects.bulk_create([
                SourceChunk(source_document_id=doc.pk, chunk_order=i, chunk_text=text, embedding=vec)
                for i, (text, vec) in enumerate(zip(chunks, vectors))
            ])
        stats["documents"] += 1
        stats["chunks"] += len(chunks)
        log.info("공식자료 %s: 청크 %d개 저장", doc.pk, len(chunks))
    return stats
