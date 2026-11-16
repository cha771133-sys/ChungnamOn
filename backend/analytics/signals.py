"""더미 파이프라인의 저장 전이만 관찰한다.

QuerySet.update/bulk_create에는 신호가 없다. 이 경로를 정상 수집으로 간주하지 않는다.
실제·혼합 경로는 pipeline의 명시적 생산자가 담당한다.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models.signals import pre_delete, pre_save, post_save
from django.dispatch import receiver

from accounts.models import UserProfile
from documents.models import Document

from .models import KPIEvent
from .services.document_events import current_start, finish_analysis, pipeline_data_kind, start_analysis

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Document)
def remember_document_state(sender, instance, raw=False, using='default', update_fields=None, **kwargs):
    """일반 저장의 상태 전이 비교를 위해 저장 전 DB 상태만 보관한다."""
    instance._kpi_previous = None
    if (raw or using != 'default' or pipeline_data_kind() != KPIEvent.DataKind.DUMMY
            or (update_fields is not None and not {'status', 'step'}.intersection(update_fields))):
        return
    instance._kpi_previous = (sender.objects.filter(pk=instance.pk).values('status', 'step').first()
                              if instance.pk else None)


@receiver(post_save, sender=Document)
def observe_document_state(sender, instance, raw=False, using='default', **kwargs):
    """저장 후 DB 상태를 다시 읽어 관찰하고 수집 실패는 savepoint로 격리한다."""
    previous = getattr(instance, '_kpi_previous', None)
    if raw or using != 'default' or not previous:
        return
    try:
        with transaction.atomic():
            # update_fields에 포함되지 않은 낡은 인스턴스 상태를 업무 사실로 기록하지 않는다.
            current = sender.objects.get(pk=instance.pk)
            _observe_transition(current, previous, language=getattr(instance, '_kpi_language', None))
    except (DatabaseError, ValidationError):
        logger.warning('KPI_EVENT_NOT_RECORDED')


def _observe_transition(document, previous, language=None):
    """OCR 진입과 대응 완료·실패를 DUMMY로 기록하며 누락된 시작은 추측하지 않는다."""
    # 기존 파이프라인은 OCR 진입 직전에 PROCESSING/QUEUED를 선점한다.
    # 업로드 생성만으로 분석 시작을 기록하지 않는다.
    if (document.status == Document.Status.PROCESSING and document.step == Document.Step.OCR
            and previous != {'status': Document.Status.PROCESSING, 'step': Document.Step.OCR}):
        if language is None:
            language = UserProfile.objects.filter(user_id=document.owner_id).values_list('language', flat=True).first() or 'vi'
        start_analysis(document, language, KPIEvent.DataKind.DUMMY)
        return
    if previous['status'] == document.status:
        return
    started = current_start(document)
    if started and started.data_kind == KPIEvent.DataKind.DUMMY:
        finish_analysis(document, started)


@receiver(pre_delete, sender=Document)
def erase_document_events(sender, instance, using='default', **kwargs):
    """문서 삭제 트랜잭션에서 연결된 KPI 이력도 제거한다."""
    # SET_NULL은 부모 Admin 삭제 권한을 바꾸지 않는다. 삭제 트랜잭션에서 이력도 제거한다.
    KPIEvent.objects.using(using).filter(document_id=instance.pk).delete()
