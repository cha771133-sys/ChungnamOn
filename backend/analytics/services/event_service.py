"""신뢰하는 서버 호출 전용 이벤트 저장. 공개 이벤트 수집 API는 제공하지 않는다."""

import hashlib
import logging

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from ..models import KPIEvent

logger = logging.getLogger(__name__)


def record_event(event_type, *, document, entity_id, occurrence_key, language='',
                 properties=None, data_kind=KPIEvent.DataKind.UNKNOWN, occurred_at=None):
    """업무 전이 식별자를 해시로 저장한다. 같은 키의 다른 사실은 덮어쓰지 않고 거부한다.

    예외를 호출자에게 전달하므로 업무 트랜잭션 안에서 원자적 저장할 수 있다.
    occurrence_key는 서버가 만든 요청/시도 ID이며 원문을 저장하지 않는다.
    """
    if not isinstance(occurrence_key, str) or not occurrence_key or len(occurrence_key) > 200:
        raise ValidationError('서버 업무 전이 식별자가 필요합니다.')
    if not document or not document.pk or type(entity_id) is not int or entity_id <= 0:
        raise ValidationError('저장된 문서와 양의 정수 업무 ID가 필요합니다.')
    entity_type = (KPIEvent.Entity.CHAT_MESSAGE if event_type == KPIEvent.Type.CHAT_ANSWER_CREATED
                   else KPIEvent.Entity.DOCUMENT)
    key = hashlib.sha256(f'{event_type}:{document.pk}:{entity_id}:{occurrence_key}'.encode()).hexdigest()
    values = dict(event_type=event_type, document=document, entity_type=entity_type,
                  entity_id=entity_id, language=language, properties=properties if properties is not None else {},
                  data_kind=data_kind)
    candidate = KPIEvent(**values, idempotency_key=key, occurred_at=occurred_at or timezone.now())
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    with transaction.atomic():
        event, _ = KPIEvent.objects.get_or_create(idempotency_key=key, defaults={
            **values, 'occurred_at': candidate.occurred_at,
        })
        if any(getattr(event, field) != value for field, value in values.items()):
            raise ValidationError('같은 업무 전이 키에 다른 사실을 기록할 수 없습니다.')
        return event


def observe_event(*args, **kwargs):
    """핵심 업무를 보존하는 관찰 경로. 저장 실패는 고정 경고로 남기며 가짜 이력을 만들지 않는다.

    중첩 savepoint가 외부 트랜잭션의 DB 오류 오염을 막는다. 원본 예외/입력은 로깅하지 않는다.
    """
    try:
        with transaction.atomic():
            return record_event(*args, **kwargs)
    except (DatabaseError, ValidationError):
        logger.warning('KPI_EVENT_NOT_RECORDED')
        return None
