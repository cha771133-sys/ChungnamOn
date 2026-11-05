import logging

from django.db import DatabaseError, transaction

from documents.models import Document

from .models import KPIEvent
from .services.event_service import observe_event

logger = logging.getLogger(__name__)


class ResultProvidedMiddleware:
    """성공한 기존 결과 API 응답만 관찰한다. 브라우저 표시/수신을 증명하지 않는다."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = request.resolver_match
        if (request.method != 'GET' or response.status_code != 200 or not match
                or match.url_name != 'document-result' or not request.user.is_authenticated):
            return response
        try:
            with transaction.atomic():
                document = Document.objects.filter(pk=match.kwargs.get('pk'), owner=request.user, status='DONE').first()
                completed = (document.kpi_events.filter(event_type=KPIEvent.Type.INTERPRETATION_COMPLETED).first()
                             if document else None)
                if completed:
                    observe_event(KPIEvent.Type.RESULT_PROVIDED, document=document, entity_id=document.pk,
                                  occurrence_key=str(completed.pk), language=completed.language,
                                  data_kind=completed.data_kind)
        except DatabaseError:
            logger.warning('KPI_EVENT_NOT_RECORDED')
        return response
