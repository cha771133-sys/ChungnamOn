from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ContentMigrationTests(TransactionTestCase):
    def test_p16_rows_and_chunks_survive_content_field_addition(self):
        old_target = [('sources', '0001_initial')]
        new_target = [('sources', '0002_sourcedocument_content_text')]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            document = apps.get_model('sources', 'SourceDocument').objects.create(
                source_url='https://www.asan.go.kr/test-fixtures/migration',
                title='기존 자료', agency='아산시청', region='아산시', category='지방세',
                content_hash='a' * 64,
            )
            chunk = apps.get_model('sources', 'SourceChunk').objects.create(
                source_document_id=document.pk, chunk_text='기존 AI 근거', chunk_order=0, embedding=[0.1],
            )
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            migrated = new_apps.get_model('sources', 'SourceDocument').objects.get(pk=document.pk)
            self.assertEqual(migrated.content_text, '')
            self.assertEqual(migrated.content_hash, 'a' * 64)
            preserved = new_apps.get_model('sources', 'SourceChunk').objects.get(pk=chunk.pk)
            self.assertEqual(preserved.embedding, [0.1])
            self.assertEqual(preserved.chunk_text, '기존 AI 근거')
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
