from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model

from analytics.models import KPIEvent
from analytics.services.event_service import record_event
from documents.models import Document, InterpretationResult

NOW = datetime(2026, 11, 5, 12, tzinfo=dt_timezone.utc)


class AnalyticsFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('kpi-owner@example.com')

    def document(self, **kwargs):
        return Document.objects.create(owner=self.user, file='documents/example.pdf', file_name='example.pdf',
                                       file_type='PDF', **kwargs)

    def event(self, kind, document, **kwargs):
        return record_event(kind, document=document, entity_id=kwargs.pop('entity_id', document.pk),
                            occurrence_key=kwargs.pop('occurrence_key', 'attempt-1'),
                            occurred_at=kwargs.pop('occurred_at', NOW),
                            data_kind=kwargs.pop('data_kind', KPIEvent.DataKind.REAL), **kwargs)

    def result(self, document):
        return InterpretationResult.objects.create(document=document, translated_text='번역', summary=['요약'], language='vi')
