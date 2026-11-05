"""B 담당 챗봇 저장 트랜잭션에서 호출하는 개인정보 없는 연동 지점."""

from django.conf import settings
from django.db import DatabaseError, transaction

from sources.models import SourceDocument
from sources.services.errors import CollectionError
from sources.services.source_registry import validate_source_url

from ..models import KPIEvent
from .event_service import logger, observe_event


def _official_source_count(message):
    snapshots = {entry['sourceDocumentId']: entry for entry in message.sources
                 if type(entry.get('sourceDocumentId')) is int}
    count = 0
    for source in SourceDocument.objects.filter(pk__in=snapshots):
        snapshot = snapshots[source.pk]
        if (not source.ai_eligible or not source.collected_at or not source.content_hash
                or snapshot.get('url') != source.source_url
                or snapshot.get('contentHash') != source.content_hash
                or snapshot.get('checkedAt') != source.collected_at.isoformat()):
            continue
        try:
            validate_source_url(source.source_url)
        except CollectionError:
            continue
        count += 1
    return count


def observe_chat_answer(message):
    """현재 dummy 외 모드는 실제 어댑터 계약이 없어 UNKNOWN으로 제외한다."""
    try:
        with transaction.atomic():
            count = _official_source_count(message)
            # 잘못 연결된 GROUNDED도 정상 근거 기반으로 수집하지 않는다.
            status = message.status if count or not message.grounded else 'UNVERIFIED'
            return observe_event(
                KPIEvent.Type.CHAT_ANSWER_CREATED, document=message.document, entity_id=message.pk,
                occurrence_key=str(message.pk), language=message.language,
                data_kind=KPIEvent.DataKind.DUMMY if settings.CHATBOT_AI_MODE == 'dummy' else KPIEvent.DataKind.UNKNOWN,
                properties={'status': status, 'grounded': status == 'GROUNDED', 'official_source_count': count},
            )
    except DatabaseError:
        logger.warning('KPI_EVENT_NOT_RECORDED')
        return None
