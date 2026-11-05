from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from analytics.models import KPIEvent
from analytics.services.chat_events import chat_properties, observe_chat_answer
from analytics.services.event_service import record_event
from analytics.services.kpi_service import get_kpi_summary
from chatbot.models import ChatMessage
from chatbot.services.chat_service import ChatServiceUnavailable, submit_question
from documents.models import Document
from documents.services.pipeline import process_document

from .helpers import AnalyticsFixtures


@override_settings(DOCUMENTS_STEP_DELAY=0, CHATBOT_AI_MODE='dummy')
class IntegrationTests(AnalyticsFixtures, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_real_pipeline_records_dummy_start_completion_and_successful_result_response(self):
        document = self.document()
        self.assertFalse(KPIEvent.objects.exists())
        self.assertTrue(process_document(document.pk))
        self.assertEqual(set(KPIEvent.objects.values_list('event_type', flat=True)), {
            KPIEvent.Type.ANALYSIS_STARTED, KPIEvent.Type.INTERPRETATION_COMPLETED})
        self.assertEqual(self.client.get(f'/api/documents/{document.pk}/result/').status_code, 200)
        self.client.get(f'/api/documents/{document.pk}/result/')
        self.assertEqual(document.kpi_events.filter(event_type=KPIEvent.Type.RESULT_PROVIDED).count(), 1)
        self.assertEqual(set(KPIEvent.objects.values_list('data_kind', flat=True)), {'DUMMY'})
        summary = get_kpi_summary(now=timezone.now() + timedelta(seconds=1))
        self.assertEqual(summary['metrics'][0]['denominator'], 0)
        self.assertNotIn('번역', str(list(KPIEvent.objects.values('properties'))))

    def test_ocr_failure_is_started_and_retry_has_distinct_attempt(self):
        document = self.document()
        with patch('documents.services.pipeline.run_ocr', side_effect=RuntimeError('테스트 실패')):
            with self.assertLogs('documents.services.pipeline', level='ERROR'):
                process_document(document.pk)
        failure = document.kpi_events.get(event_type=KPIEvent.Type.INTERPRETATION_FAILED)
        self.assertEqual(failure.properties, {'stage': 'OCR'})
        process_document(document.pk)
        self.assertEqual(document.kpi_events.filter(event_type=KPIEvent.Type.ANALYSIS_STARTED).count(), 2)
        self.assertEqual(document.kpi_events.filter(event_type=KPIEvent.Type.INTERPRETATION_COMPLETED).count(), 1)

    def test_unready_unauthorized_and_missing_results_do_not_record_provision(self):
        document = self.document()
        self.assertEqual(self.client.get(f'/api/documents/{document.pk}/result/').status_code, 409)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/documents/{document.pk}/result/').status_code, 401)
        self.assertFalse(KPIEvent.objects.filter(event_type=KPIEvent.Type.RESULT_PROVIDED).exists())

    def test_historical_result_response_is_observed_without_inventing_old_completion(self):
        document = self.document(status='DONE')
        self.result(document)
        self.assertEqual(self.client.get(f'/api/documents/{document.pk}/result/').status_code, 200)
        self.client.get(f'/api/documents/{document.pk}/result/')
        event = KPIEvent.objects.get()
        self.assertEqual(event.event_type, KPIEvent.Type.RESULT_PROVIDED)
        self.assertEqual(event.data_kind, 'UNKNOWN')
        self.assertEqual(get_kpi_summary()['metrics'][0]['denominator'], 0)

    def test_stale_instance_and_repeated_step_saves_do_not_invent_start(self):
        document = self.document()
        document.status, document.step = 'PROCESSING', 'OCR'
        document.save(update_fields=['title'])
        self.assertFalse(KPIEvent.objects.exists())
        Document.objects.filter(pk=document.pk).update(status='PROCESSING', step='QUEUED')
        document.save(update_fields=['step'])
        document.save(update_fields=['step'])
        self.assertEqual(KPIEvent.objects.filter(event_type=KPIEvent.Type.ANALYSIS_STARTED).count(), 1)

    def test_chat_success_is_logged_only_after_save_and_dummy_is_excluded(self):
        document = self.document(status='DONE')
        self.result(document)
        message = submit_question(self.user, document.pk, {'message': '필요한 안내는 무엇인가요?'})
        event = KPIEvent.objects.get(event_type=KPIEvent.Type.CHAT_ANSWER_CREATED)
        self.assertEqual(event.entity_id, message.pk)
        self.assertEqual(event.data_kind, 'DUMMY')
        self.assertEqual(event.properties, {'status': 'UNVERIFIED', 'grounded': False, 'official_source_count': 0})
        self.assertEqual(get_kpi_summary()['chat_states']['total'], 0)
        self.assertNotIn('안내', str(event.properties))

    def test_chat_call_failure_does_not_become_generated_answer(self):
        document = self.document(status='DONE')
        self.result(document)
        with patch('chatbot.services.ai_client.generate_answer', side_effect=RuntimeError('민감 오류')):
            with self.assertRaises(ChatServiceUnavailable):
                submit_question(self.user, document.pk, {'message': '질문'})
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(KPIEvent.objects.exists())

    def test_chat_event_failure_preserves_answer_and_exposes_no_raw_error(self):
        document = self.document(status='DONE')
        self.result(document)
        with patch('analytics.services.event_service.record_event', side_effect=DatabaseError('민감 오류')):
            with self.assertLogs('analytics.services.event_service', level='WARNING'):
                message = submit_question(self.user, document.pk, {'message': '질문'})
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertFalse(KPIEvent.objects.exists())
        self.assertEqual(get_kpi_summary()['answers_without_events'], 1)

    def test_chat_and_event_are_both_removed_by_business_rollback(self):
        document = self.document(status='DONE')
        self.result(document)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                submit_question(self.user, document.pk, {'message': '질문'})
                raise RuntimeError('롤백')
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(KPIEvent.objects.exists())

    def test_official_source_requires_real_id_approval_and_matching_snapshot(self):
        document = self.document(status='DONE')
        official = self.official_source()
        message = ChatMessage.objects.create(document=document, question='질문', answer='답변', language='vi',
                                             status='GROUNDED', grounded=True, sources=[official])
        self.assertEqual(chat_properties(message)['official_source_count'], 1)
        event = observe_chat_answer(message)
        self.assertEqual(event.properties['status'], 'GROUNDED')
        # 더미는 근거가 연결되어도 운영 KPI로 계산하지 않는다.
        self.assertEqual(get_kpi_summary()['chat_states']['total'], 0)
        for sources in ([{'url': 'https://www.asan.go.kr/'}], [{'sourceDocumentId': 999999}],
                        [{**official, 'contentHash': 'b' * 64}]):
            message.sources = sources
            message.save()
            self.assertEqual(chat_properties(message)['status'], 'UNVERIFIED')
            with self.assertRaises(ValidationError):
                record_event(KPIEvent.Type.CHAT_ANSWER_CREATED, document=document, entity_id=message.pk,
                             occurrence_key='forged', data_kind='REAL', properties={
                                 'status': 'GROUNDED', 'grounded': True, 'official_source_count': 1})

    def test_answer_creation_is_idempotent_even_if_retry_key_changes(self):
        document = self.document()
        message = ChatMessage.objects.create(document=document, question='질문', answer='답변', language='vi')
        kwargs = dict(document=document, entity_id=message.pk,
                      properties={'status': 'UNVERIFIED', 'grounded': False, 'official_source_count': 0})
        first = record_event(KPIEvent.Type.CHAT_ANSWER_CREATED, occurrence_key='first', **kwargs)
        second = record_event(KPIEvent.Type.CHAT_ANSWER_CREATED, occurrence_key='retry', **kwargs)
        self.assertEqual(first.pk, second.pk)
