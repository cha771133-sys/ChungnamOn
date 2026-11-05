from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import Mock

from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from sources.models import CrawlLog, SourceDocument, SourceRefreshTarget, SourceRevision
from sources.services.errors import CollectionError
from sources.services.refresh_service import refresh_target

from .test_official_refresh import make_record


class RefreshMigrationTests(TransactionTestCase):
    def test_existing_approved_text_chunks_and_logs_survive_without_fake_review(self):
        old = [('sources', '0003_agency_agencyalias')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            document = apps.get_model('sources', 'SourceDocument').objects.create(
                source_url='https://www.asan.go.kr/test-fixtures/old', title='기존 안내',
                agency='아산시', region='아산시', category='지방세', content_text='기존 정상 본문',
                content_hash='a' * 64, collected_at=timezone.now(), status='ACTIVE',
            )
            chunk = apps.get_model('sources', 'SourceChunk').objects.create(source_document_id=document.pk, chunk_order=0, chunk_text='기존 청크', embedding=[0.1])
            log = apps.get_model('sources', 'CrawlLog').objects.create(source_url=document.source_url, result='SUCCESS')
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            migrated = SourceDocument.objects.get(pk=document.pk)
            self.assertEqual(migrated.content_text, '기존 정상 본문')
            self.assertEqual(migrated.content_hash, 'a' * 64)
            self.assertEqual(migrated.collected_at, document.collected_at)
            self.assertEqual(migrated.status, 'ACTIVE')
            self.assertEqual(migrated.review_status, 'PENDING_REVIEW')
            self.assertIsNone(migrated.reviewed_by_id)
            self.assertEqual(migrated.provider, 'html')
            from sources.models import SourceChunk
            preserved = SourceChunk.objects.get(pk=chunk.pk)
            self.assertEqual(preserved.embedding, [0.1])
            self.assertEqual(preserved.source_content_hash, '')
            self.assertFalse(preserved.is_current)
            self.assertEqual(CrawlLog.objects.get(pk=log.pk).result, 'SUCCESS')
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


class RefreshConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.target = SourceRefreshTarget.objects.create(provider='public_data', external_id='service-1')

    def test_concurrent_same_target_only_one_external_call(self):
        started, release = Event(), Event()

        def fetch(target):
            """첫 외부 요청을 멈춰 둔 동안 중복 실행이 요청 없이 건너뛰는지 확인한다."""
            started.set()
            if not release.wait(10):
                raise RuntimeError('테스트 실행 제한 시간 초과')
            return make_record()

        client = Mock(fetch=Mock(side_effect=fetch))

        def run():
            close_old_connections()
            try:
                return refresh_target(SourceRefreshTarget.objects.get(pk=self.target.pk), client).outcome
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run)
            try:
                self.assertTrue(started.wait(10))
                second = pool.submit(run).result(timeout=10)
                self.assertEqual(second, 'skipped')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 'new')
        self.assertEqual(client.fetch.call_count, 1)
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.assertEqual(CrawlLog.objects.count(), 1)

    @skipUnless(connection.vendor == 'postgresql', 'PostgreSQL 실제 동시 쓰기는 PostgreSQL CI에서 검증')
    def test_simultaneous_claims_have_single_owner_in_postgresql(self):
        """PostgreSQL의 동시 잠금 획득 요청 중 하나만 소유권과 실행 이력을 얻는다."""
        barrier = Barrier(2)
        from sources.services.refresh_service import claim

        def run():
            close_old_connections()
            try:
                target = SourceRefreshTarget.objects.get(pk=self.target.pk)
                barrier.wait(timeout=10)
                return claim(target)[0]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(run), pool.submit(run)]
            tokens = [task.result(timeout=15) for task in tasks]
        self.assertEqual(sum(token is not None for token in tokens), 1)
        self.assertEqual(CrawlLog.objects.count(), 1)

    @skipUnless(connection.vendor == 'postgresql', 'PostgreSQL 실제 동시 승인은 PostgreSQL CI에서 검증')
    def test_simultaneous_approval_cannot_apply_same_revision_twice(self):
        """PostgreSQL에서 같은 후보의 동시 승인은 한 번만 반영되고 나머지는 충돌한다."""
        from django.contrib.auth import get_user_model
        from sources.services.refresh_service import review_revision
        user = get_user_model().objects.create_superuser(email='concurrent-review@example.com', password='test-password')
        refresh_target(self.target, Mock(fetch=Mock(return_value=make_record())))
        revision = SourceRevision.objects.get()
        barrier = Barrier(2)

        def run():
            """독립 연결에서 동시 검토 결과를 모으고 성공·충돌과 관계없이 연결을 정리한다."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    review_revision(revision.pk, user, approve=True)
                    return 'approved'
                except CollectionError as error:
                    return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(run), pool.submit(run)]
            results = [task.result(timeout=15) for task in tasks]
        self.assertCountEqual(results, ['approved', 'REVIEW_CONFLICT'])
        self.assertEqual(SourceDocument.objects.get().version, 1)
