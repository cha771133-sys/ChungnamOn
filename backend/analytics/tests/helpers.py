from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model

from analytics.models import KPIEvent
from analytics.services.event_service import record_event
from documents.models import Document, InterpretationResult
from sources.models import SourceDocument

NOW = datetime(2026, 11, 5, 12, tzinfo=dt_timezone.utc)


class AnalyticsFixtures:
    @classmethod
    def setUpTestData(cls):
        """실제 개인정보 없는 공통 문서 소유자를 생성한다."""
        cls.user = get_user_model().objects.create_user('kpi-owner@example.com')

    def document(self, **kwargs):
        """지정한 상태의 테스트 문서를 생성한다."""
        return Document.objects.create(owner=self.user, file='documents/example.pdf', file_name='example.pdf',
                                       file_type='PDF', **kwargs)

    def event(self, kind, document, **kwargs):
        """고정 시각과 서버 시도 키로 집계 검증용 REAL 이벤트를 저장한다."""
        return record_event(kind, document=document, entity_id=kwargs.pop('entity_id', document.pk),
                            occurrence_key=kwargs.pop('occurrence_key', 'attempt-1'),
                            occurred_at=kwargs.pop('occurred_at', NOW),
                            data_kind=kwargs.pop('data_kind', KPIEvent.DataKind.REAL), **kwargs)

    def result(self, document):
        """번역·요약이 존재하는 최소 해석 결과를 생성한다."""
        return InterpretationResult.objects.create(document=document, translated_text='번역', summary=['요약'], language='vi')

    def official_source(self):
        """승인된 공식자료와 동일한 출처 스냅샷을 생성한다."""
        source = SourceDocument.objects.create(
            source_url='https://www.asan.go.kr/', title='공식 안내', agency='테스트 기관', region='아산', category='안내',
            collected_at=NOW, content_hash='a' * 64, status='ACTIVE', review_status='APPROVED',
        )
        return {'sourceDocumentId': source.pk, 'url': source.source_url,
                'contentHash': source.content_hash, 'checkedAt': source.collected_at.isoformat()}
