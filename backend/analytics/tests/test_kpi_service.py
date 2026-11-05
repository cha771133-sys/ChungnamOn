from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from chatbot.models import ChatMessage
from analytics.models import KPIEvent
from analytics.services.kpi_service import get_kpi_summary

from .helpers import AnalyticsFixtures, NOW


class KPIServiceTests(AnalyticsFixtures, TestCase):
    def started(self, document, *, age=1, **kwargs):
        return self.event(KPIEvent.Type.ANALYSIS_STARTED, document,
                          occurred_at=NOW - timedelta(days=age), **kwargs)

    def completed(self, document, **kwargs):
        return self.event(KPIEvent.Type.INTERPRETATION_COMPLETED, document,
                          occurred_at=NOW - timedelta(hours=2),
                          properties={'has_translation': True, 'has_summary': True}, **kwargs)

    def metric(self, summary, key):
        return next(m for m in summary['metrics'] if m['metric'] == key)

    def test_no_denominator_and_missing_contracts_are_not_zero_percent(self):
        summary = get_kpi_summary(now=NOW)
        self.assertEqual(len(summary['metrics']), 6)
        for metric in summary['metrics']:
            self.assertIsNone(metric['rate'])
        self.assertEqual(summary['metrics'][0]['status'], 'NO_DATA')
        for index in (1, 2, 3, 5):
            self.assertEqual(summary['metrics'][index]['status'], 'NOT_MEASURABLE')
            self.assertIsNone(summary['metrics'][index]['denominator'])

    def test_started_only_with_failures_retries_and_result_provision_separated(self):
        completed, ocr_failed, ai_failed, pending = [self.document() for _ in range(4)]
        for document in (completed, ocr_failed, ai_failed):
            self.started(document)
        self.started(completed, occurrence_key='retry')
        self.result(completed)
        self.completed(completed)
        self.completed(completed, occurrence_key='retry')
        for document, stage in ((ocr_failed, 'OCR'), (ai_failed, 'INTERPRETING')):
            self.event(KPIEvent.Type.INTERPRETATION_FAILED, document,
                       occurred_at=NOW - timedelta(hours=1), properties={'stage': stage})
        summary = get_kpi_summary(now=NOW)
        metric = summary['metrics'][0]
        self.assertEqual((metric['numerator'], metric['denominator'], metric['rate']), (1, 3, 33.33))
        self.assertEqual(metric['status'], 'PARTIAL')
        self.assertEqual(summary['result_provided']['numerator'], 0)
        self.assertEqual(summary['document_states']['failed'], 2)
        self.event(KPIEvent.Type.RESULT_PROVIDED, completed, occurred_at=NOW - timedelta(minutes=1))
        self.assertEqual(get_kpi_summary(now=NOW)['result_provided']['numerator'], 1)

    def test_done_without_translation_or_summary_is_not_success(self):
        document = self.document(status='DONE')
        self.started(document)
        self.completed(document)
        self.assertEqual(get_kpi_summary(now=NOW)['metrics'][0]['numerator'], 0)
        result = self.result(document)
        result.summary = []
        result.save()
        self.assertEqual(get_kpi_summary(now=NOW)['metrics'][0]['numerator'], 0)
        result.summary = ['요약']
        result.translated_text = '  \n '
        result.save()
        self.assertEqual(get_kpi_summary(now=NOW)['metrics'][0]['numerator'], 0)

    def test_period_boundaries_first_start_and_retry_cohort(self):
        for age in (0, 7, 8, 30, 31):
            self.started(self.document(), age=age)
        old = self.document()
        self.started(old, age=40)
        self.started(old, age=1, occurrence_key='retry')
        self.assertEqual(get_kpi_summary(now=NOW, days=7)['metrics'][0]['denominator'], 1)
        self.assertEqual(get_kpi_summary(now=NOW, days=30)['metrics'][0]['denominator'], 3)

    def test_future_outcomes_do_not_count(self):
        document = self.document()
        self.started(document)
        self.result(document)
        self.event(KPIEvent.Type.INTERPRETATION_COMPLETED, document, occurred_at=NOW,
                   properties={'has_translation': True, 'has_summary': True})
        self.assertEqual(get_kpi_summary(now=NOW)['metrics'][0]['numerator'], 0)

    def test_dummy_test_unknown_and_untracked_history_are_not_real_metrics(self):
        for kind in (KPIEvent.DataKind.DUMMY, KPIEvent.DataKind.TEST, KPIEvent.DataKind.UNKNOWN):
            document = self.document()
            self.started(document, data_kind=kind)
            self.result(document)
            self.completed(document, data_kind=kind)
        historical = self.document(status='DONE')
        self.result(historical)
        summary = get_kpi_summary(now=NOW)
        self.assertEqual(summary['metrics'][0]['denominator'], 0)
        self.assertEqual(summary['data_kinds'], {'DUMMY': 2, 'TEST': 2, 'UNKNOWN': 2})

    def test_language_uses_first_event_snapshot_and_unknown_is_separate(self):
        for language in ('vi', 'en', ''):
            self.started(self.document(), language=language)
        self.assertEqual(get_kpi_summary(now=NOW, language='vi')['metrics'][0]['denominator'], 1)
        self.assertEqual(get_kpi_summary(now=NOW, language='')['metrics'][0]['denominator'], 1)
        self.assertEqual(get_kpi_summary(now=NOW)['metrics'][0]['denominator'], 3)

    def test_generated_chat_answers_count_with_statuses_and_no_double_count(self):
        document = self.document()
        source = self.official_source()
        for status in ('GROUNDED', 'UNVERIFIED', 'MISMATCH'):
            message = ChatMessage.objects.create(document=document, question='질문', answer='답변', language='ko',
                                                 status=status, grounded=status == 'GROUNDED',
                                                 sources=[] if status == 'UNVERIFIED' else [source])
            self.event(KPIEvent.Type.CHAT_ANSWER_CREATED, document, entity_id=message.pk,
                       occurred_at=NOW - timedelta(hours=1),
                       properties={'status': status, 'grounded': status == 'GROUNDED',
                                   'official_source_count': int(status != 'UNVERIFIED')})
        # 존재하지 않는 답변에 대한 성공 이력은 거부한다.
        with self.assertRaises(ValidationError):
            self.event(KPIEvent.Type.CHAT_ANSWER_CREATED, document, entity_id=999999,
                       occurred_at=NOW - timedelta(hours=1), properties={
                           'status': 'UNVERIFIED', 'grounded': False, 'official_source_count': 0})
        summary = get_kpi_summary(now=NOW)
        metric = self.metric(summary, 'chat_grounded_answer_rate')
        self.assertEqual((metric['numerator'], metric['denominator'], metric['rate']), (1, 3, 33.33))
        self.assertEqual(summary['chat_states'], {'grounded': 1, 'unverified': 1, 'mismatch': 1, 'total': 3})

    def test_query_count_stays_bounded_and_read_has_no_writes(self):
        self.started(self.document())
        with CaptureQueriesContext(connection) as small:
            get_kpi_summary(now=NOW)
        for _ in range(20):
            self.started(self.document())
        before = KPIEvent.objects.count()
        with CaptureQueriesContext(connection) as larger:
            get_kpi_summary(now=NOW)
        self.assertEqual(len(small), len(larger))
        self.assertLessEqual(len(larger), 7)
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in larger))
        self.assertEqual(KPIEvent.objects.count(), before)

    def test_unsupported_ranges_language_and_naive_time_are_rejected(self):
        for kwargs in ({'days': 0}, {'days': 365}, {'days': True}, {'days': '7'},
                       {'language': 'nationality'}, {'now': NOW.replace(tzinfo=None)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                get_kpi_summary(**kwargs)
