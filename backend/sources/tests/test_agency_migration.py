from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AgencyMigrationTests(TransactionTestCase):
    def test_existing_source_document_chunks_and_log_survive(self):
        old = [('sources', '0002_sourcedocument_content_text')]
        new = [('sources', '0003_agency_agencyalias')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            source = apps.get_model('sources', 'SourceDocument').objects.create(
                source_url='https://www.asan.go.kr/test/migration', title='가상 자료',
                agency='가상시청', region='아산시', category='지방세', content_text='보존할 가상 본문',
                content_hash='a' * 64,
            )
            chunk = apps.get_model('sources', 'SourceChunk').objects.create(
                source_document_id=source.pk, chunk_text='보존할 가상 청크', chunk_order=0, embedding=[0.1],
            )
            log = apps.get_model('sources', 'CrawlLog').objects.create(source_url=source.source_url)
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            apps = executor.loader.project_state(new).apps
            self.assertEqual(apps.get_model('sources', 'SourceDocument').objects.get(pk=source.pk).content_text, '보존할 가상 본문')
            self.assertEqual(apps.get_model('sources', 'SourceChunk').objects.get(pk=chunk.pk).embedding, [0.1])
            self.assertEqual(apps.get_model('sources', 'CrawlLog').objects.get(pk=log.pk).source_url, source.source_url)
            self.assertEqual(apps.get_model('sources', 'Agency').objects.count(), 0)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
