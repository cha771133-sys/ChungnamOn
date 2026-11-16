"""B 담당 챗봇 저장 트랜잭션에서 호출하는 개인정보 없는 연동 지점."""

from django.conf import settings
from django.db import DatabaseError, transaction

from sources.models import SourceDocument
from sources.services.errors import CollectionError
from sources.services.source_registry import validate_source_url

from ..models import KPIEvent
from .event_service import logger, observe_event


def _official_source_count(message):
    """승인된 공식자료와 ID·URL·해시·확인일이 일치하는 출처 스냅샷만 센다."""
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


def chat_properties(message):
    """실제 업무 모델과 유효한 공식 출처에서만 저장 속성을 구성한다."""
    count = _official_source_count(message)
    status = message.status if count or message.status == 'UNVERIFIED' else 'UNVERIFIED'
    return {'status': status, 'grounded': status == 'GROUNDED', 'official_source_count': count}


def observe_chat_answer(message):
    """현재 dummy 외 모드는 실제 어댑터 계약이 없어 UNKNOWN으로 제외한다."""
    try:
        with transaction.atomic():
            return observe_event(
                KPIEvent.Type.CHAT_ANSWER_CREATED, document=message.document, entity_id=message.pk,
                occurrence_key=str(message.pk), language=message.language,
                data_kind=KPIEvent.DataKind.DUMMY if settings.CHATBOT_AI_MODE == 'dummy' else KPIEvent.DataKind.UNKNOWN,
                properties=chat_properties(message),
            )
    except DatabaseError:
        logger.warning('KPI_EVENT_NOT_RECORDED')
        return None
