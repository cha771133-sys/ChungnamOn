from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from sources.models import CrawlLog, SourceChunk, SourceDocument

from .helpers import create_document


class SourceDocumentTests(TestCase):
    def test_create_document_with_metadata_and_defaults(self):
        document = create_document()
        document.full_clean()
        document.refresh_from_db()

        self.assertIsInstance(document.pk, int)
        self.assertEqual(document.status, SourceDocument.Status.PENDING)
        self.assertIsNone(document.published_at)
        self.assertIsNone(document.collected_at)
        self.assertEqual(document.content_hash, '')
        self.assertEqual(
            SourceDocument.objects.get(agency='아산시청', region='아산시', category='지방세'),
            document,
        )
        self.assertEqual(str(document), '주민세 납부 안내 (아산시청)')

    def test_required_url_model_validation(self):
        for value in ('', '  ', None, '유효하지 않은 URL'):
            with self.subTest(value=value):
                document = SourceDocument(source_url=value)
                with self.assertRaises(ValidationError) as error:
                    document.full_clean()
                self.assertIn('source_url', error.exception.message_dict)

    def test_missing_url_rejected_by_database_without_full_clean(self):
        for value in ('', '  ', None):
            with self.subTest(value=value), self.assertRaises(IntegrityError), transaction.atomic():
                create_document(source_url=value)

    def test_duplicate_url_rejected_by_database(self):
        create_document()
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_document()

    def test_normalized_duplicate_rejected_by_validation_and_database(self):
        document = create_document()
        duplicate = SourceDocument(
            source_url='  HTTPS://WWW.ASAN.GO.KR:443/notices/1#details  ',
            title=document.title, agency=document.agency,
            region=document.region, category=document.category,
        )
        with self.assertRaises(ValidationError) as error:
            duplicate.full_clean()
        self.assertIn('source_url', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_url_normalization_on_save_and_refresh(self):
        document = create_document(source_url=' HTTPS://WWW.ASAN.GO.KR:443#intro ')
        self.assertEqual(document.source_url, 'https://www.asan.go.kr/')
        document.refresh_from_db()
        self.assertEqual(document.source_url, 'https://www.asan.go.kr/')

    def test_http_default_port_and_nondefault_port(self):
        document = create_document(source_url='HTTP://WWW.ASAN.GO.KR:80/notice')
        self.assertEqual(document.source_url, 'http://www.asan.go.kr/notice')
        other = create_document(source_url='https://www.asan.go.kr:8443/notice')
        self.assertEqual(other.source_url, 'https://www.asan.go.kr:8443/notice')

    def test_international_hostname_and_ipv6_normalization(self):
        document = create_document(source_url='https://예시.한국/안내#본문')
        self.assertEqual(document.source_url, 'https://xn--vv4b11d.xn--3e0b707e/안내')
        other = create_document(source_url='HTTPS://[2001:DB8::1]:443/notice')
        self.assertEqual(other.source_url, 'https://[2001:db8::1]/notice')

    def test_invalid_port_raises_field_validation_error(self):
        document = SourceDocument(source_url='https://www.asan.go.kr:invalid/notice')
        with self.assertRaises(ValidationError) as error:
            document.full_clean()
        self.assertIn('source_url', error.exception.message_dict)

    def test_distinct_path_query_and_scheme_are_preserved(self):
        urls = [
            'https://www.asan.go.kr/Notice?a=1&b=2',
            'https://www.asan.go.kr/notice?a=1&b=2',
            'https://www.asan.go.kr/Notice?b=2&a=1',
            'https://www.asan.go.kr/Notice/?a=1&b=2',
            'http://www.asan.go.kr/Notice?a=1&b=2',
        ]
        for url in urls:
            self.assertEqual(create_document(source_url=url).source_url, url)
        self.assertEqual(SourceDocument.objects.count(), len(urls))

    def test_bulk_create_and_update_cannot_bypass_normalized_uniqueness(self):
        first = create_document()
        second = create_document(source_url='https://www.asan.go.kr/notices/2')
        variant = 'HTTPS://WWW.ASAN.GO.KR:443/notices/1#intro'
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceDocument.objects.bulk_create([SourceDocument(source_url=variant)])
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceDocument.objects.filter(pk=second.pk).update(source_url=variant)
        second.source_url = variant
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceDocument.objects.bulk_update([second], ['source_url'])
        self.assertEqual(SourceDocument.objects.get(pk=first.pk).source_url, first.source_url)

    def test_metadata_dates_and_hash_round_trip(self):
        collected = timezone.now()
        document = create_document(
            published_at=date(2026, 7, 1), collected_at=collected,
            content_hash='a' * 64, status=SourceDocument.Status.ACTIVE,
        )
        document.full_clean()
        document.refresh_from_db()
        self.assertEqual(document.published_at, date(2026, 7, 1))
        self.assertEqual(document.collected_at, collected)
        self.assertTrue(timezone.is_aware(document.collected_at))
        self.assertEqual(document.content_hash, 'a' * 64)
        other = create_document(source_url='https://www.asan.go.kr/notices/2', content_hash='a' * 64)
        self.assertNotEqual(other.pk, document.pk)
        document.status = SourceDocument.Status.INACTIVE
        document.save(update_fields=['status'])
        document.refresh_from_db()
        self.assertEqual(document.collected_at, collected)

    def test_required_metadata_and_length_limits_use_full_clean(self):
        document = create_document()
        for field in ('title', 'agency', 'region', 'category'):
            original = getattr(document, field)
            for value in ('', '가' * (document._meta.get_field(field).max_length + 1)):
                with self.subTest(field=field, value_length=len(value)):
                    setattr(document, field, value)
                    with self.assertRaises(ValidationError) as error:
                        document.full_clean()
                    self.assertIn(field, error.exception.message_dict)
            setattr(document, field, original)
        document.source_url = 'https://www.asan.go.kr/' + 'a' * 2048
        with self.assertRaises(ValidationError) as error:
            document.full_clean()
        self.assertIn('source_url', error.exception.message_dict)

    def test_invalid_status_rejected_by_model_and_database(self):
        document = create_document()
        document.status = 'FAILED'
        with self.assertRaises(ValidationError) as error:
            document.full_clean()
        self.assertIn('status', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceDocument.objects.filter(pk=document.pk).update(status='FAILED')


class SourceChunkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.document = create_document()

    def test_multiple_chunks_have_source_and_stable_order_without_embeddings(self):
        later = SourceChunk.objects.create(
            source_document=self.document, chunk_text='두 번째 본문', chunk_order=1,
        )
        first = SourceChunk.objects.create(
            source_document=self.document, chunk_text='첫 번째 본문', chunk_order=0,
        )
        first.full_clean()
        self.assertEqual(list(self.document.chunks.all()), [first, later])
        self.assertEqual(first.source_document, self.document)
        self.assertIsNone(first.embedding)
        self.assertEqual(str(first), f'공식자료 {self.document.pk} / 청크 0')

    def test_duplicate_order_rejected_by_validation_and_database(self):
        SourceChunk.objects.create(source_document=self.document, chunk_text='처음', chunk_order=0)
        duplicate = SourceChunk(source_document=self.document, chunk_text='중복', chunk_order=0)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_same_order_in_different_documents_is_allowed(self):
        other = create_document(source_url='https://www.asan.go.kr/notices/2')
        for document in (self.document, other):
            SourceChunk.objects.create(source_document=document, chunk_text='본문', chunk_order=0)
        self.assertEqual(SourceChunk.objects.count(), 2)

    def test_embedding_round_trip_and_unbounded_text(self):
        vector = [0.1, -0.25, 0.0, 1.0]
        text = '공식자료 본문 ' * 10000
        chunk = SourceChunk.objects.create(
            source_document=self.document, chunk_text=text, chunk_order=0, embedding=vector,
        )
        chunk.full_clean()
        chunk.refresh_from_db()
        self.assertEqual(chunk.embedding, vector)
        self.assertEqual(chunk.chunk_text, text)

    def test_source_document_is_required_by_model_and_database(self):
        chunk = SourceChunk(chunk_text='본문', chunk_order=0)
        with self.assertRaises(ValidationError) as error:
            chunk.full_clean()
        self.assertIn('source_document', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            chunk.save()

    def test_nonexistent_document_rejected_by_foreign_key(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceChunk.objects.create(
                source_document_id=self.document.pk + 1000, chunk_text='본문', chunk_order=0,
            )
            connection.check_constraints()

    def test_negative_order_rejected_by_model_and_database(self):
        chunk = SourceChunk(source_document=self.document, chunk_text='본문', chunk_order=-1)
        with self.assertRaises(ValidationError) as error:
            chunk.full_clean()
        self.assertIn('chunk_order', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            chunk.save()

    def test_document_delete_is_protected_and_status_updates_keep_chunk_id(self):
        chunk = SourceChunk.objects.create(
            source_document=self.document, chunk_text='기존 근거', chunk_order=0,
        )
        with self.assertRaises(ProtectedError):
            self.document.delete()
        with self.assertRaises(ProtectedError):
            SourceDocument.objects.filter(pk=self.document.pk).delete()
        self.document.status = SourceDocument.Status.INACTIVE
        self.document.save(update_fields=['status'])
        self.assertEqual(self.document.chunks.get().pk, chunk.pk)


class CrawlLogTests(TestCase):
    url = 'https://www.asan.go.kr/notices/unregistered#original'

    def test_running_log_without_document_or_finish_time(self):
        before = timezone.now()
        log = CrawlLog.objects.create(source_url=self.url)
        log.full_clean()
        log.refresh_from_db()
        self.assertEqual(log.source_url, self.url)
        self.assertEqual(log.result, CrawlLog.Result.RUNNING)
        self.assertIsNone(log.finished_at)
        self.assertEqual(log.error_message, '')
        self.assertTrue(timezone.is_aware(log.started_at))
        self.assertGreaterEqual(log.started_at, before)
        self.assertLessEqual(log.started_at, timezone.now())
        self.assertFalse(SourceDocument.objects.exists())
        self.assertEqual(str(log), f'{self.url} (진행 중)')

    def test_success_and_failure_records(self):
        start = timezone.now()
        for result, message in (
            (CrawlLog.Result.SUCCESS, ''), (CrawlLog.Result.FAILED, '응답 시간 초과'),
        ):
            with self.subTest(result=result):
                log = CrawlLog.objects.create(
                    source_url=self.url, started_at=start, finished_at=start + timedelta(seconds=1),
                    result=result, error_message=message,
                )
                log.full_clean()
                log.refresh_from_db()
                self.assertEqual(log.result, result)
                self.assertEqual(log.error_message, message)

    def test_multiple_runs_preserve_history_and_latest_order(self):
        start = timezone.now()
        old = CrawlLog.objects.create(source_url=self.url, started_at=start)
        recent = CrawlLog.objects.create(source_url=self.url, started_at=start + timedelta(hours=1))
        tied = CrawlLog.objects.create(source_url=self.url, started_at=recent.started_at)
        self.assertEqual(list(CrawlLog.objects.filter(source_url=self.url)), [tied, recent, old])
        self.assertEqual(CrawlLog.objects.first(), tied)

    def test_finish_before_start_rejected_by_model_validation(self):
        start = timezone.now()
        log = CrawlLog(source_url=self.url, started_at=start, finished_at=start - timedelta(seconds=1))
        with self.assertRaises(ValidationError) as error:
            log.full_clean()
        self.assertIn('finished_at', error.exception.message_dict)

    def test_finish_before_start_rejected_by_database_without_full_clean(self):
        start = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CrawlLog.objects.create(
                source_url=self.url, started_at=start, finished_at=start - timedelta(seconds=1),
            )

    def test_equal_start_and_finish_are_allowed(self):
        start = timezone.now()
        log = CrawlLog.objects.create(source_url=self.url, started_at=start, finished_at=start)
        log.full_clean()

    def test_invalid_result_rejected_by_model_and_database(self):
        log = CrawlLog(source_url=self.url, result='ACTIVE')
        with self.assertRaises(ValidationError) as error:
            log.full_clean()
        self.assertIn('result', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            log.save()

    def test_missing_url_rejected_by_model_and_database(self):
        log = CrawlLog()
        with self.assertRaises(ValidationError) as error:
            log.full_clean()
        self.assertIn('source_url', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            log.save()

    def test_collection_failure_preserves_existing_document_and_chunk(self):
        document = create_document(collected_at=timezone.now(), status=SourceDocument.Status.ACTIVE)
        chunk = SourceChunk.objects.create(source_document=document, chunk_text='기존 근거', chunk_order=0)
        checked_at = document.collected_at
        CrawlLog.objects.create(
            source_url=document.source_url, result=CrawlLog.Result.FAILED, error_message='접근 불가',
        )
        document.refresh_from_db()
        self.assertEqual(document.status, SourceDocument.Status.ACTIVE)
        self.assertEqual(document.collected_at, checked_at)
        self.assertEqual(document.chunks.get().pk, chunk.pk)
