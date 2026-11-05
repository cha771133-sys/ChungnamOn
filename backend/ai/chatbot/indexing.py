"""공식 자료 본문(SourceDocument.content_text)을 청크로 나누고 임베딩해서 SourceChunk에 저장.

수집기(sources 앱)는 건드리지 않는다. 수집 후 manage.py shell에서 실행:

    python manage.py shell -c "from ai.chatbot import build_source_chunks; print(build_source_chunks())"

- 대상: ACTIVE이고 본문이 있는 자료 (검색도 ACTIVE만 함)
- 건너뜀: 본문이 그대로라서 새로 자른 청크가 저장된 청크와 같고, 임베딩이 모두 현재 AI_EMBED_DIM인 자료
- 다시 만듦: 본문이 바뀐 자료, 임베딩이 없거나 차원이 다른 청크가 있는 자료 (임베딩 모델을 바꾼 경우)
  청크 분할 규칙(AI_CHUNK_SIZE 등)을 바꿔도 청크가 달라지므로 다시 만들어짐
- rebuild=True면 조건 없이 다시 만듦
- 임베딩은 DB 트랜잭션 밖에서 계산하고, 자료 하나 단위로 기존 청크 삭제·새 청크 저장을 묶음
"""
import logging

from .chunker import prepare_chunks
from .config import get_settings
from .embedder import embed_passages

log = logging.getLogger(__name__)


def _is_current(existing: list[tuple[str, object]], chunks: list[str], dim: int) -> bool:
    """저장된 [(chunk_text, embedding)]이 새 청크와 같고 임베딩 차원도 맞는지."""
    return (
        [text for text, _ in existing] == chunks
        and all(isinstance(emb, list) and len(emb) == dim for _, emb in existing)
    )


def build_source_chunks(source_ids=None, *, rebuild: bool = False) -> dict:
    """청크·임베딩 생성.

    반환: {"documents": 새로 만든 자료 수, "chunks": 만든 청크 수, "skipped": 그대로 둔 자료 수}
    """
    from django.db import transaction

    from sources.models import SourceChunk, SourceDocument

    dim = get_settings().embed_dim
    qs = SourceDocument.objects.filter(status=SourceDocument.Status.ACTIVE).exclude(content_text="")
    if source_ids is not None:
        qs = qs.filter(pk__in=list(source_ids))

    stats = {"documents": 0, "chunks": 0, "skipped": 0}
    for doc in qs.order_by("id").only("id", "title", "content_text"):
        chunks = prepare_chunks(doc.content_text, title=doc.title)
        existing = list(
            SourceChunk.objects.filter(source_document_id=doc.pk)
            .order_by("chunk_order").values_list("chunk_text", "embedding")
        )
        if not chunks or (not rebuild and _is_current(existing, chunks, dim)):
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
