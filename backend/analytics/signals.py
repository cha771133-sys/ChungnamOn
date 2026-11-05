"""A 담당 파일을 변경하지 않고 현재 더미 파이프라인의 저장 전이만 관찰한다.

QuerySet.update/bulk_create에는 신호가 없다. 이 경로를 정상 수집으로 간주하지 않는다.
실제 AI 전환 시 이 관찰자를 실제 데이터로 재분류하지 말고 시도 ID 계약을 먼저 합의한다.
"""

import logging
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models.signals import pre_delete, pre_save, post_save
from django.dispatch import receiver

from documents.models import Document, InterpretationResult

from .models import KPIEvent
from .services.event_service import observe_event

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Document)
def remember_document_state(sender, instance, raw=False, using='default', **kwargs):
    if raw or using != 'default':
        return
    instance._kpi_previous = (sender.objects.filter(pk=instance.pk).values('status', 'step').first()
                              if instance.pk else None)


@receiver(post_save, sender=Document)
def observe_document_state(sender, instance, raw=False, using='default', **kwargs):
    previous = getattr(instance, '_kpi_previous', None)
    if raw or using != 'default' or not previous:
        return
    try:
        with transaction.atomic():
            _observe_transition(instance, previous)
    except (DatabaseError, ValidationError):
        logger.warning('KPI_EVENT_NOT_RECORDED')


def _observe_transition(document, previous):
    # 기존 파이프라인은 OCR 진입 직전에 PROCESSING/QUEUED를 선점한다.
    # 업로드 생성만으로 분석 시작을 기록하지 않는다.
    if (document.status == Document.Status.PROCESSING and document.step == Document.Step.OCR
            and previous != {'status': Document.Status.PROCESSING, 'step': Document.Step.OCR}):
        observe_event(KPIEvent.Type.ANALYSIS_STARTED, document=document, entity_id=document.pk,
                      occurrence_key=uuid4().hex, data_kind=KPIEvent.DataKind.DUMMY)
        return
    if previous['status'] == document.status:
        return
    started = document.kpi_events.filter(event_type=KPIEvent.Type.ANALYSIS_STARTED).first()
    if not started:
        return  # 과거/누락 시도 이력을 추측해서 생성하지 않는다.
    base = dict(document=document, entity_id=document.pk, occurrence_key=str(started.pk), data_kind=started.data_kind)
    if document.status == Document.Status.DONE:
        result = InterpretationResult.objects.filter(document=document).first()
        if result:
            observe_event(KPIEvent.Type.INTERPRETATION_COMPLETED, **base,
                          language=result.language if result.language in dict(KPIEvent._meta.get_field('language').choices) else '',
                          properties={'has_translation': bool(result.translated_text.strip()),
                                      'has_summary': isinstance(result.summary, list) and bool(result.summary)})
    elif document.status == Document.Status.FAILED:
        observe_event(KPIEvent.Type.INTERPRETATION_FAILED, **base, properties={'stage': document.step})


@receiver(pre_delete, sender=Document)
def erase_document_events(sender, instance, using='default', **kwargs):
    # SET_NULL은 부모 Admin 삭제 권한을 바꾸지 않는다. 삭제 트랜잭션에서 이력도 제거한다.
    KPIEvent.objects.using(using).filter(document_id=instance.pk).delete()
