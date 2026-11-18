import logging

from django.db import DatabaseError, transaction

from accounts.models import UserProfile
from documents.models import Document, InterpretationResult

from .models import KPIEvent
from .services.event_service import observe_event
from .services.document_events import attempt_completion, current_start

logger = logging.getLogger(__name__)


class ResultProvidedMiddleware:
    """성공한 기존 결과 API 응답만 관찰한다. 브라우저 표시/수신을 증명하지 않는다."""

    def __init__(self, get_response):
        """Django 응답 처리 함수를 보관한다."""
        self.get_response = get_response

    def __call__(self, request):
        """소유자의 성공한 결과 조회를 관찰하고 수집 DB 오류를 응답에서 격리한다."""
        response = self.get_response(request)
        match = request.resolver_match
        if (request.method != 'GET' or response.status_code != 200 or not match
                or match.url_name != 'document-result' or not request.user.is_authenticated):
            return response
        try:
            with transaction.atomic():
                document = Document.objects.filter(pk=match.kwargs.get('pk'), owner=request.user, status='DONE').first()
                # 직렬화 도중 새 시도가 시작되었다면 새 결과를 제공했다고 추측하지 않는다.
                if document and document.analysis_attempt_id != getattr(request, '_kpi_result_attempt_id', None):
                    return response
                started = current_start(document) if document else None
                if document and document.analysis_attempt_id:
                    completed = attempt_completion(document, started) if started else None
                else:
                    completed = (document.kpi_events.filter(event_type=KPIEvent.Type.INTERPRETATION_COMPLETED).first()
                                 if document else None)
                if completed:
                    observe_event(KPIEvent.Type.RESULT_PROVIDED, document=document, entity_id=document.pk,
                                  occurrence_key=f'completion:{completed.pk}', language=completed.language,
                                  data_kind=completed.data_kind)
                elif document:
                    # 현재의 성공한 조회 사실은 기록하되 과거 처리/데이터 출처를 추측하지 않는다.
                    result = InterpretationResult.objects.filter(document=document).only('id', 'language').first()
                    if result:
                        occurrence = f'untracked-result:{result.pk}'
                        if document.analysis_attempt_id:
                            occurrence += f':{document.analysis_attempt_id}'
                        observe_event(KPIEvent.Type.RESULT_PROVIDED, document=document, entity_id=document.pk,
                                      occurrence_key=occurrence,
                                      language=(started.language if started else
                                                result.language if result.language in UserProfile.Language.values else ''),
                                      data_kind=KPIEvent.DataKind.UNKNOWN)
        except DatabaseError:
            logger.warning('KPI_EVENT_NOT_RECORDED')
        return response
