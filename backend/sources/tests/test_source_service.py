import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from sources.models import CrawlLog, SourceChunk, SourceDocument
from sources.services.errors import CollectionError
from sources.services.parser import parse_html
from sources.services.source_service import _store, collect_source

from .pipeline_helpers import HTML, URL, collector, spec


class SourceServiceTests(TestCase):
    def collect(self, **options):
        return collect_source(spec(), collector(), **options)

    def test_new_document_body_metadata_hash_and_success_history(self):
        before = timezone.now()
        result = self.collect()
        self.assertEqual(result.outcome, 'new')
        document = SourceDocument.objects.get()
        self.assertEqual(document.source_url, URL)
        self.assertEqual(document.content_hash, hashlib.sha256(document.content_text.encode()).hexdigest())
        self.assertIn('8월 31일', document.content_text)
        self.assertIsNone(document.published_at)
        self.assertEqual((document.agency, document.region, document.category), ('아산시청', '아산시', '지방세'))
        self.assertGreaterEqual(document.collected_at, before)
        self.assertEqual(document.status, SourceDocument.Status.ACTIVE)
        log = CrawlLog.objects.get()
        self.assertEqual(log.result, CrawlLog.Result.SUCCESS)
        self.assertGreaterEqual(log.finished_at, log.started_at)
        self.assertEqual(log.error_message, '')

    def test_running_log_exists_before_http_starts(self):
        client = collector()
        def fetch(url):
            log = CrawlLog.objects.get()
            self.assertEqual(log.result, CrawlLog.Result.RUNNING)
            self.assertIsNone(log.finished_at)
            return collector().fetch(url)
        client.fetch.side_effect = fetch
        self.assertEqual(collect_source(spec(), client).outcome, 'new')

    def test_same_hash_does_not_rewrite_body_and_refreshes_confirmation(self):
        self.collect()
        document = SourceDocument.objects.get()
        old_time = timezone.now() - timedelta(days=1)
        SourceDocument.objects.filter(pk=document.pk).update(collected_at=old_time)
        text = document.content_text
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.collect().outcome, 'unchanged')
        updates = [query['sql'] for query in queries if query['sql'].lstrip().upper().startswith('UPDATE "SOURCES_SOURCEDOCUMENT"')]
        self.assertTrue(updates)
        self.assertNotIn('"content_text" =', updates[0])
        self.assertNotIn('"content_hash" =', updates[0])
        document.refresh_from_db()
        self.assertEqual(document.content_text, text)
        self.assertGreater(document.collected_at, old_time)
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.assertEqual(CrawlLog.objects.count(), 2)

    def test_changed_body_updates_same_record_preserves_chunks_and_embedding(self):
        self.collect()
        document = SourceDocument.objects.get()
        original_hash = document.content_hash
        chunk = SourceChunk.objects.create(source_document=document, chunk_text='기존 근거', chunk_order=0, embedding=[0.1])
        changed = HTML.replace('8월 31일'.encode(), '9월 1일'.encode())
        self.assertEqual(collect_source(spec(), collector(changed)).outcome, 'updated')
        document.refresh_from_db()
        chunk.refresh_from_db()
        self.assertNotEqual(document.content_hash, original_hash)
        self.assertIn('9월 1일', document.content_text)
        self.assertEqual(chunk.embedding, [0.1])
        self.assertEqual(chunk.chunk_text, '기존 근거')
        self.assertEqual(SourceDocument.objects.count(), 1)

    def test_failed_network_or_empty_body_preserves_entire_document_and_chunks(self):
        self.collect()
        document = SourceDocument.objects.get()
        chunk = SourceChunk.objects.create(source_document=document, chunk_text='보존', chunk_order=0)
        original = SourceDocument.objects.values().get()
        failing = collector()
        failing.fetch.side_effect = CollectionError('NETWORK_FAILED', '네트워크 확인 실패')
        for client in (failing, collector(b'<main></main>')):
            self.assertEqual(collect_source(spec(), client).outcome, 'failed')
            self.assertEqual(SourceDocument.objects.values().get(), original)
        self.assertTrue(SourceChunk.objects.filter(pk=chunk.pk).exists())
        self.assertEqual(CrawlLog.objects.filter(result=CrawlLog.Result.FAILED).count(), 2)
        for log in CrawlLog.objects.filter(result=CrawlLog.Result.FAILED):
            self.assertTrue(log.error_message)
            self.assertIsNotNone(log.finished_at)

    def test_confirmed_date_saved_and_registry_metadata_has_priority(self):
        source = spec(title='목록에서 검토한 제목', published_at=date(2026, 8, 1))
        result = collect_source(source, collector())
        document = SourceDocument.objects.get()
        self.assertEqual(document.title, source.title)
        self.assertEqual(document.published_at, source.published_at)
        self.assertIn('HTML 제목 확인 필요', result.message)

    def test_inactive_document_is_not_reactivated_and_missing_body_is_backfilled(self):
        document = SourceDocument.objects.create(
            source_url=URL, title='기존', agency='아산시청', region='아산시', category='지방세',
            status=SourceDocument.Status.INACTIVE,
        )
        self.assertEqual(self.collect().outcome, 'updated')
        document.refresh_from_db()
        self.assertEqual(document.status, SourceDocument.Status.INACTIVE)
        self.assertTrue(document.content_text)

    def test_dry_run_new_and_changed_and_failure_leave_both_tables_unchanged(self):
        self.assertEqual(self.collect(dry_run=True).outcome, 'new')
        self.assertFalse(SourceDocument.objects.exists())
        self.assertFalse(CrawlLog.objects.exists())
        self.collect()
        documents, logs = list(SourceDocument.objects.values()), list(CrawlLog.objects.values())
        self.assertEqual(self.collect(dry_run=True).outcome, 'unchanged')
        self.assertEqual(collect_source(spec(), collector(HTML.replace('8월 31일'.encode(), '9월 1일'.encode())), dry_run=True).outcome, 'updated')
        self.assertEqual(collect_source(spec(), collector(b''), dry_run=True).outcome, 'failed')
        self.assertEqual(list(SourceDocument.objects.values()), documents)
        self.assertEqual(list(CrawlLog.objects.values()), logs)

    def test_disabled_does_not_request_or_log(self):
        client = collector()
        self.assertEqual(collect_source(spec(enabled=False), client).outcome, 'skipped')
        client.fetch.assert_not_called()
        self.assertFalse(CrawlLog.objects.exists())

    def test_database_failure_rolls_back_document_and_records_failed_log(self):
        self.collect()
        original = SourceDocument.objects.values().get()
        with patch('sources.services.source_service._store', side_effect=DatabaseError('secret database connection')):
            result = self.collect()
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('DB_FAILED', result.message)
        self.assertNotIn('secret', result.message)
        self.assertEqual(SourceDocument.objects.values().get(), original)
        self.assertEqual(CrawlLog.objects.first().result, CrawlLog.Result.FAILED)

    def test_success_log_failure_rolls_back_new_document(self):
        with patch('django.db.models.query.QuerySet.update', side_effect=DatabaseError('secret')):
            result = self.collect()
        self.assertEqual(result.outcome, 'failed')
        self.assertFalse(SourceDocument.objects.exists())
        self.assertEqual(CrawlLog.objects.get().result, CrawlLog.Result.RUNNING)
        self.assertIn('LOG_DB_FAILED', result.message)

    def test_log_creation_failure_prevents_untracked_http(self):
        client = collector()
        with patch.object(CrawlLog.objects, 'create', side_effect=DatabaseError('secret')):
            result = collect_source(spec(), client)
        self.assertEqual(result.outcome, 'failed')
        client.fetch.assert_not_called()

    def test_unexpected_exception_does_not_leak_secret(self):
        client = collector()
        client.fetch.side_effect = RuntimeError('https://user:secret@host/?token=secret')
        result = collect_source(spec(), client)
        self.assertNotIn('secret', result.message)
        self.assertEqual(CrawlLog.objects.get().error_message, result.message)

    def test_stale_confirmation_cannot_overwrite_newer_document(self):
        self.collect()
        original = SourceDocument.objects.values().get()
        result = _store(
            spec(), parse_html(HTML), 'b' * 64, timezone.now(), timezone.now() - timedelta(days=1),
        )
        self.assertEqual(result, 'skipped')
        self.assertEqual(SourceDocument.objects.values().get(), original)

    def test_earlier_request_finishing_last_cannot_overwrite_later_success(self):
        client = collector()
        changed = HTML.replace('8월 31일'.encode(), '9월 1일'.encode())
        saved = {}

        def fetch(url):
            self.assertEqual(collect_source(spec(), collector(changed)).outcome, 'new')
            saved.update(SourceDocument.objects.values().get())
            return collector().fetch(url)

        client.fetch.side_effect = fetch
        self.assertEqual(collect_source(spec(), client).outcome, 'skipped')
        self.assertEqual(SourceDocument.objects.values().get(), saved)
        self.assertEqual(CrawlLog.objects.filter(result=CrawlLog.Result.SUCCESS).count(), 2)

    def test_later_request_can_replace_earlier_request_that_finished_after_it_started(self):
        client = collector()
        changed = HTML.replace('8월 31일'.encode(), '9월 1일'.encode())

        def fetch(url):
            earlier = CrawlLog.objects.create(
                source_url=URL, started_at=timezone.now() - timedelta(days=1),
                result=CrawlLog.Result.SUCCESS, finished_at=timezone.now(),
            )
            _store(spec(), parse_html(HTML), 'b' * 64, timezone.now(), earlier.started_at)
            return collector(changed).fetch(url)

        client.fetch.side_effect = fetch
        self.assertEqual(collect_source(spec(), client).outcome, 'updated')
        self.assertIn('9월 1일', SourceDocument.objects.get().content_text)

    def test_later_failed_or_other_url_success_does_not_block_valid_response(self):
        self.collect()
        client = collector(HTML.replace('8월 31일'.encode(), '9월 1일'.encode()))

        def fetch(url):
            for source_url, result in (
                (URL, CrawlLog.Result.FAILED), (URL, CrawlLog.Result.RUNNING),
                (URL + '0', CrawlLog.Result.SUCCESS),
            ):
                now = timezone.now()
                CrawlLog.objects.create(source_url=source_url, result=result, started_at=now, finished_at=now)
            return client.fetch.return_value

        client.fetch.side_effect = fetch
        self.assertEqual(collect_source(spec(), client).outcome, 'updated')
        self.assertIn('9월 1일', SourceDocument.objects.get().content_text)


@skipUnless(connection.vendor == 'postgresql', '동시 쓰기 검증은 PostgreSQL CI에서 실행합니다.')
class ConcurrentCollectionTests(TransactionTestCase):
    def test_older_request_finishing_last_preserves_later_committed_document(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                SourceDocument.objects.all().delete()
                CrawlLog.objects.all().delete()
                if existing:
                    self.assertEqual(collect_source(spec(), collector()).outcome, 'new')
                older_started, newer_saved = Event(), Event()

                def run_older():
                    close_old_connections()
                    client = collector()

                    def fetch(url):
                        older_started.set()
                        if not newer_saved.wait(timeout=10):
                            raise RuntimeError('동시 수집 테스트 대기 시간 초과')
                        return collector().fetch(url)

                    client.fetch.side_effect = fetch
                    try:
                        return collect_source(spec(), client)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=1) as pool:
                    older = pool.submit(run_older)
                    try:
                        self.assertTrue(older_started.wait(timeout=10))
                        changed = HTML.replace('8월 31일'.encode(), '9월 1일'.encode())
                        result = collect_source(spec(), collector(changed))
                        self.assertEqual(result.outcome, 'updated' if existing else 'new')
                        saved = SourceDocument.objects.values().get()
                    finally:
                        newer_saved.set()
                    self.assertEqual(older.result(timeout=10).outcome, 'skipped')
                self.assertEqual(SourceDocument.objects.values().get(), saved)
                self.assertFalse(CrawlLog.objects.exclude(result=CrawlLog.Result.SUCCESS).exists())

    def test_same_url_concurrently_creates_one_document_and_two_finished_logs(self):
        barrier = Barrier(2, timeout=10)

        def run_collection():
            close_old_connections()
            client = collector()
            def fetch(url):
                barrier.wait()
                return collector().fetch(url)
            client.fetch.side_effect = fetch
            try:
                return collect_source(spec(), client)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run_collection(), range(2)))
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.assertEqual(sum(result.outcome == 'new' for result in results), 1)
        self.assertFalse(any(result.outcome == 'failed' for result in results))
        self.assertEqual(CrawlLog.objects.count(), 2)
        self.assertFalse(CrawlLog.objects.filter(finished_at__isnull=True).exists())
