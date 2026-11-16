"""현재 처리 시도와 실제 저장 사실에 연결하는 문서 KPI 생산자."""

import logging
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction

from accounts.models import UserProfile
from documents.models import Document, InterpretationResult

from ..models import KPIEvent
from .event_service import event_idempotency_key, observe_event

logger = logging.getLogger(__name__)


def pipeline_data_kind():
    """고정 더미 입력과 완전한 실제 경로를 구분하고 혼합·미지원 설정은 미확인으로 둔다."""
    modes = tuple(getattr(settings, name, 'dummy') for name in (
        'OCR_MODE', 'MASK_MODE', 'INTERPRET_MODE', 'DOCUMENTS_VERIFY_MODE',
    ))
    if modes == ('clova', 'rules', 'llm', 'rag'):
        return KPIEvent.DataKind.REAL
    if modes[0] == modes[2] == modes[3] == 'dummy':
        return KPIEvent.DataKind.DUMMY
    return KPIEvent.DataKind.UNKNOWN


def start_analysis(document, language, data_kind):
    """선점된 시도 UUID로 시작을 기록한다. 저장 실패는 업무 처리를 중단하지 않는다."""
    return observe_event(
        KPIEvent.Type.ANALYSIS_STARTED, document=document, entity_id=document.pk,
        occurrence_key=str(document.analysis_attempt_id or uuid4()),
        language=language if language in UserProfile.Language.values else '', data_kind=data_kind,
    )


def current_start(document):
    """현재 UUID와 일치하는 시작만 찾는다. 누락된 새 시작을 과거 시작으로 대체하지 않는다."""
    events = document.kpi_events.filter(event_type=KPIEvent.Type.ANALYSIS_STARTED)
    if document.analysis_attempt_id:
        return events.filter(idempotency_key=event_idempotency_key(
            KPIEvent.Type.ANALYSIS_STARTED, document.pk, document.pk, str(document.analysis_attempt_id),
        )).first()
    # 파이프라인 밖의 기존 더미 save 관찰은 영속 시도 UUID가 없다.
    return events.filter(data_kind=KPIEvent.DataKind.DUMMY).first()


def attempt_completion(document, started):
    """같은 시작 이벤트에 연결된 완료만 선택한다."""
    return document.kpi_events.filter(
        event_type=KPIEvent.Type.INTERPRETATION_COMPLETED,
        idempotency_key=event_idempotency_key(
            KPIEvent.Type.INTERPRETATION_COMPLETED, document.pk, document.pk, str(started.pk),
        ), data_kind=started.data_kind, language=started.language,
    ).first()


def finish_analysis(document, started):
    """저장된 완료/실패만 시작 시점의 구분·언어로 기록한다."""
    if not started:
        return None
    try:
        with transaction.atomic():
            return _finish_analysis(document, started)
    except (DatabaseError, ValidationError):
        logger.warning('KPI_EVENT_NOT_RECORDED')
        return None


def _finish_analysis(document, started):
    """내부 조회 실패도 업무 트랜잭션을 오염시키지 않도록 savepoint에서 실행한다."""
    current = Document.objects.get(pk=document.pk)
    if current.analysis_attempt_id != document.analysis_attempt_id or started.document_id != document.pk:
        return None
    if started.event_type != KPIEvent.Type.ANALYSIS_STARTED:
        return None
    if current.analysis_attempt_id and started.idempotency_key != event_idempotency_key(
        KPIEvent.Type.ANALYSIS_STARTED, document.pk, document.pk, str(current.analysis_attempt_id),
    ):
        return None
    base = dict(document=document, entity_id=document.pk, occurrence_key=str(started.pk),
                data_kind=started.data_kind, language=started.language)
    if current.status == Document.Status.DONE:
        result = InterpretationResult.objects.filter(document=document).first()
        if result:
            return observe_event(
                KPIEvent.Type.INTERPRETATION_COMPLETED, **base,
                properties={'has_translation': bool(result.translated_text.strip()),
                            'has_summary': isinstance(result.summary, list) and bool(result.summary)},
            )
    elif current.status == Document.Status.FAILED:
        return observe_event(KPIEvent.Type.INTERPRETATION_FAILED, **base, properties={'stage': current.step})
    return None
