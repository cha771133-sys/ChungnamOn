from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from sources.models import CrawlLog, SourceDocument
from sources.services.errors import CollectionError

from .pipeline_helpers import URL, collector, spec


class CollectCommandTests(TestCase):
    def setUp(self):
        self.client = collector()
        self.registry = patch('sources.management.commands.collect_sources.load_registry', return_value=[spec()]).start()
        patch('sources.management.commands.collect_sources.Collector', return_value=self.client).start()
        self.addCleanup(patch.stopall)

    def run_command(self, *args):
        output = StringIO()
        call_command('collect_sources', *args, stdout=output)
        return output.getvalue()

    def test_default_processes_all_and_recollection_is_unchanged(self):
        self.registry.return_value = [spec(), spec(url=URL + '&other=2')]
        self.assertIn('전체=2 신규=2', self.run_command())
        self.assertIn('동일=2 실패=0', self.run_command())
        self.assertEqual(SourceDocument.objects.count(), 2)
        self.assertEqual(CrawlLog.objects.count(), 4)
        self.client.close.assert_called()

    def test_limit_and_disabled_skips_are_counted(self):
        self.registry.return_value = [spec(enabled=False), spec(url=URL + '&other=2'), spec(url=URL + '&other=3')]
        output = self.run_command('--limit', '1')
        self.assertIn('전체=3 신규=1', output)
        self.assertIn('건너뜀=2', output)
        self.assertEqual(self.client.fetch.call_count, 1)

    def test_limit_must_be_positive(self):
        for limit in ('0', '-1'):
            with self.subTest(limit=limit), self.assertRaises(CommandError):
                self.run_command('--limit', limit)
        self.client.fetch.assert_not_called()

    def test_url_must_be_registered_and_document_query_is_used(self):
        self.assertIn('전체=1 신규=1', self.run_command('--url', URL))
        for url in ('https://www.asan.go.kr/unregistered', 'https://127.0.0.1/'):
            self.client.fetch.reset_mock()
            with self.subTest(url=url), self.assertRaises(CommandError):
                self.run_command('--url', url)
            self.client.fetch.assert_not_called()

    def test_dry_run_collects_but_writes_neither_documents_nor_logs(self):
        self.assertIn('저장 없는 검증: 전체=1 신규=1', self.run_command('--dry-run'))
        self.assertEqual(self.client.fetch.call_count, 1)
        self.assertFalse(SourceDocument.objects.exists())
        self.assertFalse(CrawlLog.objects.exists())

    def test_partial_failure_continues_and_returns_nonzero_after_summary(self):
        self.registry.return_value = [spec(), spec(url=URL + '&other=2')]
        self.client.fetch.side_effect = [CollectionError('HTTP_STATUS', 'HTTP 404'), collector().fetch(URL)]
        output = StringIO()
        with self.assertRaisesMessage(CommandError, '일부 수집 실패'):
            call_command('collect_sources', stdout=output)
        self.assertIn('신규=1 갱신=0 동일=0 실패=1', output.getvalue())
        self.assertEqual(CrawlLog.objects.count(), 2)
        self.assertEqual(SourceDocument.objects.count(), 1)

    def test_total_failure_and_registry_failure_are_distinct(self):
        self.client.fetch.side_effect = CollectionError('NETWORK_FAILED', '요청 실패')
        with self.assertRaisesMessage(CommandError, '전체 수집 실패'):
            self.run_command()
        self.registry.side_effect = CollectionError('REGISTRY_INVALID', '목록 형식 오류')
        with self.assertRaisesMessage(CommandError, 'REGISTRY_INVALID'):
            self.run_command()

    def test_empty_registry_and_removed_url_never_delete_existing_document(self):
        self.run_command()
        self.registry.return_value = []
        self.assertIn('전체=0', self.run_command())
        self.assertEqual(SourceDocument.objects.count(), 1)
