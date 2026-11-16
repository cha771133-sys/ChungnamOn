"""외부 AI를 mock하여 실제 진입점의 실측 이벤트와 재시도 계약을 검증한다."""

from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserProfile
from analytics.models import KPIEvent
from analytics.services.event_service import record_event
from analytics.services.kpi_service import get_kpi_summary
from documents.models import Document
from documents.services.pipeline import process_document

from .helpers import AnalyticsFixtures


@override_settings(OCR_MODE='clova', MASK_MODE='rules', INTERPRET_MODE='llm',
                   DOCUMENTS_VERIFY_MODE='rag', DOCUMENTS_STEP_DELAY=0)
class RealDocumentEventTests(AnalyticsFixtures, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        UserProfile.objects.create(user=self.user, language='en')
        result = dict(doc_type='안내', title='안내', original_text='가림', translated_text='Translation',
                      language='en', summary=['Summary'], terms=[], deadlines=[], required_docs=[], agency={})
        for name, value in (
            ('run_ocr', {'text': '원문', 'confidence': 0.95}),
            ('mask_personal_info', {'masked_text': '가림', 'masked_count': 1}),
            ('interpret', result), ('verify', {'status': 'UNVERIFIED', 'sources': []}),
            ('_run_actions', {'checklist': [], 'call_script': ''}),
        ):
            mock = patch(f'documents.services.pipeline.{name}', return_value=value)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)

    def test_success_and_result_response_are_real_idempotent_and_in_kpi(self):
        document = self.document()
        self.assertTrue(process_document(document.pk))
        self.assertFalse(process_document(document.pk))
        start = document.kpi_events.get(event_type=KPIEvent.Type.ANALYSIS_STARTED)
        completed = document.kpi_events.get(event_type=KPIEvent.Type.INTERPRETATION_COMPLETED)
        self.assertEqual((start.data_kind, completed.data_kind), ('REAL', 'REAL'))
        self.assertEqual((start.language, completed.language), ('en', 'en'))
        # 같은 시도 완료 키를 재전달해도 기존 이벤트만 반환한다.
        self.assertEqual(record_event(
            completed.event_type, document=document, entity_id=document.pk, occurrence_key=str(start.pk),
            language=start.language, data_kind=start.data_kind, properties=completed.properties,
        ).pk, completed.pk)
        for _ in range(2):
            self.assertEqual(self.client.get(f'/api/documents/{document.pk}/result/').status_code, 200)
        supplied = document.kpi_events.get(event_type=KPIEvent.Type.RESULT_PROVIDED)
        self.assertEqual((supplied.data_kind, supplied.language), ('REAL', 'en'))
        summary = get_kpi_summary(now=timezone.now() + timedelta(seconds=1))
        self.assertEqual(summary['document_states'], {'total': 1, 'completed': 1, 'provided': 1, 'failed': 0})

    def test_failure_retry_and_language_snapshot_use_distinct_attempts(self):
        document = self.document()

        def fail_after_language_change(*args):
            UserProfile.objects.filter(user=self.user).update(language='vi')
            raise RuntimeError('가상 AI 오류')

        self.interpret.side_effect = fail_after_language_change
        with self.assertLogs('documents.services.pipeline', level='ERROR'):
            process_document(document.pk)
        first = document.kpi_events.get(event_type=KPIEvent.Type.ANALYSIS_STARTED)
        failure = document.kpi_events.get(event_type=KPIEvent.Type.INTERPRETATION_FAILED)
        self.assertEqual((first.data_kind, failure.data_kind, first.language, failure.language),
                         ('REAL', 'REAL', 'en', 'en'))
        self.assertFalse(document.kpi_events.filter(event_type=KPIEvent.Type.INTERPRETATION_COMPLETED).exists())
        document.refresh_from_db()
        first_attempt = document.analysis_attempt_id
        self.interpret.side_effect = None
        process_document(document.pk)
        document.refresh_from_db()
        self.assertNotEqual(document.analysis_attempt_id, first_attempt)
        starts = document.kpi_events.filter(event_type=KPIEvent.Type.ANALYSIS_STARTED)
        self.assertEqual(starts.count(), 2)
        second = starts.exclude(pk=first.pk).get()
        completed = document.kpi_events.get(event_type=KPIEvent.Type.INTERPRETATION_COMPLETED)
        self.assertEqual((second.language, completed.language), ('vi', 'vi'))
        self.assertEqual(self.interpret.call_args.args[1], 'vi')
        self.assertEqual(record_event(
            completed.event_type, document=document, entity_id=document.pk, occurrence_key=str(second.pk),
            language=second.language, data_kind=second.data_kind, properties=completed.properties,
        ).pk, completed.pk)
        self.client.get(f'/api/documents/{document.pk}/result/')
        self.assertEqual(document.kpi_events.get(event_type=KPIEvent.Type.RESULT_PROVIDED).language, 'vi')

    def test_signal_does_not_classify_real_saves_as_dummy(self):
        document = self.document()
        document.status, document.step = 'PROCESSING', 'OCR'
        document.save(update_fields=['status', 'step'])
        self.assertFalse(document.kpi_events.exists())

    def test_event_database_error_preserves_processing_and_never_reuses_old_completion(self):
        document = self.document()
        process_document(document.pk)
        old_count = document.kpi_events.count()
        Document.objects.filter(pk=document.pk).update(status='FAILED')
        with patch('analytics.services.event_service.record_event', side_effect=DatabaseError('가상 저장 오류')):
            with self.assertLogs('analytics.services.event_service', level='WARNING'):
                process_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.status, 'DONE')
        self.assertEqual(document.kpi_events.count(), old_count)
        self.assertEqual(self.client.get(f'/api/documents/{document.pk}/result/').status_code, 200)
        supplied = document.kpi_events.get(event_type=KPIEvent.Type.RESULT_PROVIDED)
        self.assertEqual(supplied.data_kind, 'UNKNOWN')

    def test_completion_save_error_does_not_turn_persisted_result_into_failure(self):
        document = self.document()

        def fail_completion(kind, **kwargs):
            if kind == KPIEvent.Type.INTERPRETATION_COMPLETED:
                raise DatabaseError('가상 저장 오류')
            return record_event(kind, **kwargs)

        with patch('analytics.services.event_service.record_event', side_effect=fail_completion):
            with self.assertLogs('analytics.services.event_service', level='WARNING'):
                process_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.status, 'DONE')
        self.assertTrue(hasattr(document, 'result'))
        self.assertEqual(list(document.kpi_events.values_list('event_type', flat=True)),
                         [KPIEvent.Type.ANALYSIS_STARTED])

    def test_rolled_back_result_records_failure_without_completion(self):
        document = self.document()
        with patch('documents.services.pipeline.replace_checklist', side_effect=RuntimeError('가상 저장 실패')):
            with self.assertLogs('documents.services.pipeline', level='ERROR'):
                process_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.status, 'FAILED')
        self.assertFalse(hasattr(document, 'result'))
        self.assertEqual(set(document.kpi_events.values_list('event_type', flat=True)), {
            KPIEvent.Type.ANALYSIS_STARTED, KPIEvent.Type.INTERPRETATION_FAILED,
        })

    def test_success_keeps_initial_language_when_profile_changes_during_ocr(self):
        document = self.document()

        def ocr_with_profile_change(*args):
            UserProfile.objects.filter(user=self.user).update(language='vi')
            return {'text': '원문', 'confidence': 0.95}

        self.run_ocr.side_effect = ocr_with_profile_change
        process_document(document.pk)
        self.assertEqual(self.interpret.call_args.args[1], 'en')
        self.client.get(f'/api/documents/{document.pk}/result/')
        self.assertEqual(set(document.kpi_events.values_list('language', flat=True)), {'en'})

    @override_settings(OCR_MODE='dummy')
    def test_mixed_configuration_is_unknown_and_excluded_from_real_kpi(self):
        document = self.document()
        process_document(document.pk)
        self.assertEqual(set(document.kpi_events.values_list('data_kind', flat=True)), {'UNKNOWN'})
        self.assertEqual(get_kpi_summary()['document_states']['total'], 0)

    def test_previous_start_cannot_be_used_for_new_retry_completion(self):
        from analytics.services.document_events import finish_analysis

        document = self.document()
        self.interpret.side_effect = RuntimeError('가상 실패')
        with self.assertLogs('documents.services.pipeline', level='ERROR'):
            process_document(document.pk)
        old_start = document.kpi_events.get(event_type=KPIEvent.Type.ANALYSIS_STARTED)
        self.interpret.side_effect = None
        process_document(document.pk)
        document.refresh_from_db()
        count = document.kpi_events.count()
        self.assertIsNone(finish_analysis(document, old_start))
        self.assertEqual(document.kpi_events.count(), count)
