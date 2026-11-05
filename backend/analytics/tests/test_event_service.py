from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import TestCase

from analytics.models import KPIEvent
from analytics.services.event_service import observe_event, record_event

from .helpers import AnalyticsFixtures, NOW


class EventServiceTests(AnalyticsFixtures, TestCase):
    def test_idempotent_request_and_distinct_retry(self):
        document = self.document()
        first = self.event(KPIEvent.Type.ANALYSIS_STARTED, document, language='vi')
        repeated = self.event(KPIEvent.Type.ANALYSIS_STARTED, document, language='vi', occurred_at=NOW + timedelta(seconds=1))
        retry = self.event(KPIEvent.Type.ANALYSIS_STARTED, document, language='vi', occurrence_key='attempt-2')
        self.assertEqual(first.pk, repeated.pk)
        self.assertNotEqual(first.pk, retry.pk)
        self.assertEqual(first.occurred_at, NOW)
        self.assertEqual(first.language, 'vi')
        self.assertEqual(first.document_id, document.pk)
        self.assertEqual(first.entity_id, document.pk)
        self.assertNotIn('attempt-1', first.idempotency_key)
        self.assertNotEqual(first.created_at, first.occurred_at)

    def test_reused_key_cannot_replace_business_fact(self):
        document = self.document()
        self.event(KPIEvent.Type.ANALYSIS_STARTED, document, language='vi')
        with self.assertRaises(ValidationError):
            self.event(KPIEvent.Type.ANALYSIS_STARTED, document, language='en')
        self.assertEqual(KPIEvent.objects.get().language, 'vi')

    def test_rejects_unknown_type_language_kind_entity_and_naive_time(self):
        document = self.document()
        for overrides in ({'kind': 'CLIENT_RAW_EVENT'}, {'language': 'xx'}, {'data_kind': 'secret'},
                          {'entity_id': 'passport-secret'}, {'occurred_at': NOW.replace(tzinfo=None)}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.event(overrides.pop('kind', KPIEvent.Type.ANALYSIS_STARTED), document, **overrides)
        self.assertFalse(KPIEvent.objects.exists())

    def test_properties_block_raw_data_and_free_error_text(self):
        document = self.document()
        for properties in ({'request': {'token': 'sensitive'}}, {'question': '민원 원문'}, {'stage': '주소 원문'},
                           {'stage': True}, {'stage': 'OCR', 'error': '민감 오류'}):
            with self.subTest(properties=properties), self.assertRaises(ValidationError):
                self.event(KPIEvent.Type.INTERPRETATION_FAILED, document, properties=properties)
        self.event(KPIEvent.Type.INTERPRETATION_FAILED, document, properties={'stage': 'OCR'})
        self.assertEqual(KPIEvent.objects.get().properties, {'stage': 'OCR'})

    def test_direct_model_save_also_validates(self):
        document = self.document()
        with self.assertRaises(ValidationError):
            KPIEvent.objects.create(event_type='RAW', document=document, entity_type='DOCUMENT', entity_id=document.pk,
                                    idempotency_key='a' * 64, properties={'answer': '본문'})

    def test_business_rollback_also_rolls_back_event(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                document = self.document()
                self.event(KPIEvent.Type.ANALYSIS_STARTED, document)
                raise RuntimeError('테스트 롤백')
        self.assertFalse(KPIEvent.objects.exists())

    def test_observation_failure_keeps_outer_transaction_usable(self):
        document = self.document()
        with transaction.atomic(), patch('analytics.services.event_service.record_event', side_effect=DatabaseError('민감 오류')):
            with self.assertLogs('analytics.services.event_service', level='WARNING') as logs:
                result = observe_event(KPIEvent.Type.ANALYSIS_STARTED, document=document, entity_id=document.pk, occurrence_key='a')
            self.assertIsNone(result)
            self.assertEqual(logs.output, ['WARNING:analytics.services.event_service:KPI_EVENT_NOT_RECORDED'])
            self.assertTrue(type(document).objects.filter(pk=document.pk).exists())

    def test_document_and_user_deletion_erase_associated_events(self):
        first, second = self.document(), self.document()
        self.event(KPIEvent.Type.ANALYSIS_STARTED, first)
        self.event(KPIEvent.Type.ANALYSIS_STARTED, second)
        first.delete()
        self.assertEqual(KPIEvent.objects.count(), 1)
        self.user.delete()
        self.assertFalse(KPIEvent.objects.exists())

    def test_default_data_kind_is_excluded_until_provenance_is_known(self):
        document = self.document()
        event = record_event(KPIEvent.Type.ANALYSIS_STARTED, document=document, entity_id=document.pk, occurrence_key='a')
        self.assertEqual(event.data_kind, KPIEvent.DataKind.UNKNOWN)
