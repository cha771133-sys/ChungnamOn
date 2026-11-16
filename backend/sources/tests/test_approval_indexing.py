"""실제 승인 서비스와 AI 완료 처리 사이의 상태·검색 계약을 검증한다."""
import os
from dataclasses import replace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from ai.chatbot import build_source_chunks
from ai.chatbot.config import reset_settings
from ai.chatbot.embedder import embed_passages
from ai.chatbot.retriever import search
from sources.models import SourceChunk, SourceDocument, SourceRefreshTarget, SourceRevision
from sources.services.refresh_service import refresh_target, review_revision

from .test_official_refresh import make_record


class ApprovalIndexingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            email='approval-indexing@example.com', password='test-password',
        )

    def setUp(self):
        patcher = patch.dict(os.environ, {
            'AI_EMBEDDER': 'hash', 'AI_EMBED_DIM': '1024',
            'AI_LLM_PROVIDER': 'mock', 'AI_RETRIEVER': 'db',
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_settings()
        self.addCleanup(reset_settings)
        self.record = make_record(
            source_url='https://www.gov.kr/test-fixtures/approval-indexing', region='아산시',
        )
        self.target = SourceRefreshTarget.objects.create(
            provider=self.record.provider, external_id=self.record.external_id,
        )

    def approve(self, record):
        """네트워크 대신 정규화된 응답을 주고 실제 수집 저장·승인 경로를 실행한다."""
        self.target.refresh_from_db()
        result = refresh_target(self.target, Mock(fetch=Mock(return_value=record)))
        self.assertIn(result.outcome, ('new', 'candidate'))
        revision = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        review_revision(revision.pk, self.admin, approve=True)
        return SourceDocument.objects.get(pk=revision.document_id)

    def results(self):
        return search('신청조건', regions=['아산시'], top_k=50, min_score=-1)

    def test_approval_and_reapproval_require_explicit_indexing_before_search(self):
        """승인만으로 처리하지 않으며 재승인된 본문만 완료 이후 검색에 사용한다."""
        with patch('ai.chatbot.indexing.embed_passages') as embed:
            document = self.approve(self.record)
        embed.assert_not_called()
        self.assertEqual((document.status, document.version), ('PENDING', 1))
        self.assertTrue(document.ai_reprocessing_required)
        self.assertFalse(document.ai_eligible)
        self.assertFalse(document.chunks.exists())
        self.assertEqual(self.results(), [])

        stats = build_source_chunks([document.pk])
        document.refresh_from_db()
        self.assertEqual((stats['completed'], stats['activated']), (1, 1))
        self.assertEqual((document.status, document.version), ('ACTIVE', 2))
        self.assertFalse(document.ai_reprocessing_required)
        old_chunk = document.chunks.get()
        old_snapshot = SourceChunk.objects.values().get(pk=old_chunk.pk)
        self.assertTrue(old_chunk.is_current)
        self.assertTrue(self.results())

        changed = replace(self.record, content_text='새 신청조건: 변경된 지원대상과 접수기간을 확인합니다.')
        with patch('ai.chatbot.indexing.embed_passages') as embed:
            document = self.approve(changed)
        embed.assert_not_called()
        self.assertEqual(document.content_hash, changed.content_hash)
        self.assertEqual((document.status, document.version), ('PENDING', 3))
        self.assertTrue(document.ai_reprocessing_required)
        self.assertFalse(document.ai_eligible)
        old_chunk.refresh_from_db()
        self.assertFalse(old_chunk.is_current)
        self.assertEqual(self.results(), [])

        stats = build_source_chunks([document.pk])
        document.refresh_from_db()
        self.assertEqual((stats['completed'], stats['activated']), (1, 1))
        self.assertEqual((document.status, document.version), ('ACTIVE', 4))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertEqual(SourceChunk.objects.values().get(pk=old_chunk.pk), old_snapshot)
        new_chunk = document.chunks.get(source_content_hash=changed.content_hash)
        self.assertNotEqual(new_chunk.pk, old_chunk.pk)
        self.assertGreater(new_chunk.chunk_order, old_chunk.chunk_order)
        self.assertTrue(new_chunk.is_current)
        results = self.results()
        self.assertTrue(results)
        self.assertTrue(all('새 신청조건' in result.text for result in results))

    def test_older_indexing_preserves_reapproved_and_completed_new_body(self):
        """실제 재승인과 최신 AI 완료가 먼저 끝나면 이전 작업이 완료 기록을 덮지 않는다."""
        document = self.approve(self.record)
        changed = replace(self.record, content_text='최신 신청조건: 새로운 지원대상과 신청절차입니다.')
        latest_document = latest_chunks = None

        def approve_and_complete_new_body(chunks):
            nonlocal latest_document, latest_chunks
            vectors = embed_passages(chunks)
            approved = self.approve(changed)
            self.assertEqual(approved.version, document.version + 1)
            with patch('ai.chatbot.indexing.embed_passages', side_effect=embed_passages):
                self.assertEqual(build_source_chunks([document.pk])['completed'], 1)
            latest_document = SourceDocument.objects.values().get(pk=document.pk)
            latest_chunks = list(document.chunks.values())
            return vectors

        with patch('ai.chatbot.indexing.embed_passages', side_effect=approve_and_complete_new_body):
            stats = build_source_chunks([document.pk])

        self.assertEqual((stats['changed'], stats['completed'], stats['activated']), (1, 0, 0))
        self.assertEqual((stats['documents'], stats['chunks']), (0, 0))
        self.assertEqual(SourceDocument.objects.values().get(pk=document.pk), latest_document)
        self.assertEqual(list(document.chunks.values()), latest_chunks)
        document.refresh_from_db()
        self.assertEqual((document.status, document.version), ('ACTIVE', 3))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertFalse(document.chunks.filter(source_content_hash=self.record.content_hash).exists())
        self.assertTrue(all(chunk.is_current for chunk in document.chunks.all()))
        self.assertTrue(self.results())

    def test_empty_chunking_does_not_complete_approved_document(self):
        """분할 결과가 없으면 승인 자료를 재처리 완료·ACTIVE로 오인하지 않는다."""
        document = self.approve(self.record)
        before = SourceDocument.objects.values().get(pk=document.pk)
        with patch('ai.chatbot.indexing.prepare_chunks', return_value=[]), patch(
            'ai.chatbot.indexing.embed_passages',
        ) as embed:
            stats = build_source_chunks([document.pk])
        embed.assert_not_called()
        self.assertEqual((stats['completed'], stats['activated'], stats['chunks']), (0, 0, 0))
        self.assertEqual(SourceDocument.objects.values().get(pk=document.pk), before)
        self.assertFalse(document.chunks.exists())
