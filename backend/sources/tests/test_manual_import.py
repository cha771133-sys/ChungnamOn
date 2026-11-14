"""TXT 입력부터 기존 관리자 승인·AI 계약까지 외부 요청 없이 검증한다."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ai.chatbot import build_source_chunks
from ai.chatbot.config import reset_settings
from ai.chatbot.indexing import _targets
from ai.chatbot.retriever import search
from sources.models import CrawlLog, SourceChunk, SourceDocument, SourceRefreshTarget, SourceRevision
from sources.services.errors import CollectionError
from sources.services.manual_import import MAX_TXT_BYTES, import_official_txt
from sources.services.normalization import OfficialRecord
from sources.services.refresh_service import refresh_target, review_revision, stage_official_record
from sources.services.source_registry import load_registry
from sources.services.source_service import collect_source
from sources.services.usage_conditions import is_registry_excluded

from .pipeline_helpers import collector, spec


class ManualImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            email='manual-review@example.com', password='test-password',
        )

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / 'official.txt'
        self.text = '공식 안내 본문입니다. 신청대상과 접수절차를 원문대로 보존합니다.\r\n신청서류를 확인하세요.\n'
        self.file.write_bytes(self.text.encode('utf-8'))
        self.url = 'https://www.immigration.go.kr/test-fixtures/manual-txt'
        self.output = StringIO()
        self.options = {
            'file': str(self.file), 'url': self.url, 'agency': '법무부', 'title': '공식 신청 안내',
            'region': '전국', 'category': '출입국·체류', 'registered_by': '등록 담당자',
            'terms_note': '테스트용 가상 허가: 출처표시 조건으로 AI 정제·임베딩·검색 허용',
            'terms_status': 'confirmed', 'terms_checked_on': date(2026, 1, 1),
        }
        settings = override_settings(SOURCE_TXT_IMPORT_ROOT=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        environment = patch.dict(os.environ, {
            'AI_EMBEDDER': 'hash', 'AI_EMBED_DIM': '1024',
            'AI_LLM_PROVIDER': 'mock', 'AI_RETRIEVER': 'db',
        })
        environment.start()
        self.addCleanup(environment.stop)
        reset_settings()
        self.addCleanup(reset_settings)
        for name in ('requests.sessions.Session.request', 'httpx.Client.send'):
            blocker = patch(name, side_effect=AssertionError('테스트에서 외부 요청 금지'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def run_import(self, **changes):
        call_command('import_official_source_txt', stdout=self.output, **{**self.options, **changes})
        return SourceDocument.objects.get(source_url=changes.get('url', self.url))

    def pending(self):
        return SourceRevision.objects.get(review_status='PENDING_REVIEW')

    def approve(self):
        review_revision(self.pending().pk, self.admin, approve=True)
        return SourceDocument.objects.get()

    def results(self):
        return search('공식 신청 안내', regions=['전국'], top_k=50, min_score=-1)

    def snapshots(self):
        return [list(model.objects.values()) for model in (
            SourceDocument, SourceRevision, SourceChunk, SourceRefreshTarget, CrawlLog,
        )]

    def test_utf8_creates_candidate_with_exact_text_hash_and_provenance(self):
        raw = self.file.read_bytes()
        with patch('ai.chatbot.indexing.embed_passages') as embed:
            document = self.run_import(published_at=date(2025, 3, 25))
        embed.assert_not_called()
        revision = self.pending()
        self.assertEqual((document.status, document.review_status), ('PENDING', 'PENDING_REVIEW'))
        self.assertEqual((document.content_text, document.content_hash), ('', ''))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertFalse(document.ai_eligible)
        self.assertEqual(revision.document_id, document.pk)
        self.assertEqual(revision.content_text, self.text)
        self.assertEqual(revision.content_hash, hashlib.sha256(self.text.encode('utf-8')).hexdigest())
        self.assertEqual(revision.metadata['published_at'], '2025-03-25')
        trace = revision.metadata['metadata']['manual_import']
        self.assertEqual(trace['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((trace['filename'], trace['registered_by']), ('official.txt', '등록 담당자'))
        self.assertEqual(self.file.read_bytes(), raw)
        self.assertFalse(_targets().filter(pk=document.pk).exists())
        self.assertEqual(self.results(), [])
        self.assertEqual((SourceRefreshTarget.objects.count(), CrawlLog.objects.count()), (0, 0))
        self.assertIn(f'document={document.pk} revision={revision.pk}', self.output.getvalue())

    def test_utf8_bom_is_removed_from_body_and_kept_in_raw_fingerprint(self):
        raw = b'\xef\xbb\xbf' + self.text.encode('utf-8')
        self.file.write_bytes(raw)
        self.run_import()
        revision = self.pending()
        self.assertEqual(revision.content_text, self.text)
        self.assertEqual(revision.content_hash, hashlib.sha256(self.text.encode('utf-8')).hexdigest())
        self.assertEqual(revision.metadata['metadata']['manual_import']['encoding'], 'utf-8-sig')
        self.assertEqual(self.file.read_bytes(), raw)

    def test_empty_invalid_encoding_and_binary_files_leave_database_untouched(self):
        cases = (
            (b'', 'TXT_EMPTY'), (b' \r\n\t', 'TXT_EMPTY'), (b'\xef\xbb\xbf', 'TXT_EMPTY'),
            ('공식 안내'.encode('cp949'), 'TXT_ENCODING_INVALID'),
            ('공식 안내'.encode('utf-16'), 'TXT_ENCODING_INVALID'),
            (b'header\x00binary', 'TXT_BINARY_REJECTED'),
            (b'header\x1bbinary', 'TXT_BINARY_REJECTED'),
            (b'%PDF-1.7 fake', 'TXT_BINARY_REJECTED'),
            (b'\xef\xbb\xbf%PDF-1.7 fake', 'TXT_BINARY_REJECTED'),
            (b'PK\x03\x04fake', 'TXT_BINARY_REJECTED'),
        )
        for raw, code in cases:
            with self.subTest(code=code, raw=raw):
                self.file.write_bytes(raw)
                before = self.snapshots()
                with self.assertRaisesMessage(CommandError, code):
                    self.run_import()
                self.assertEqual(self.snapshots(), before)
                self.assertEqual(self.file.read_bytes(), raw)

    def test_size_limit_includes_bom_and_accepts_boundary(self):
        self.file.write_bytes(b'a' * MAX_TXT_BYTES)
        self.run_import()
        before = self.snapshots()
        self.file.write_bytes(b'\xef\xbb\xbf' + b'a' * (MAX_TXT_BYTES - 2))
        with self.assertRaisesMessage(CommandError, 'TXT_TOO_LARGE'):
            self.run_import()
        self.assertEqual(self.snapshots(), before)

    def test_missing_outside_traversal_directory_extension_and_network_paths_rejected(self):
        outside = TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_file = Path(outside.name) / 'outside.txt'
        outside_file.write_text('허용 폴더 밖 실제 파일', encoding='utf-8')
        directory = self.root / 'directory.txt'
        directory.mkdir()
        other = self.root / 'official.csv'
        other.write_text('일반 본문', encoding='utf-8')
        for file in (
            self.root / 'missing.txt', self.root / '..' / self.root.name / 'official.txt',
            outside_file, directory, other,
            '\\\\server\\share\\file.txt', str(self.file) + ':hidden.txt',
        ):
            with self.subTest(file=str(file)), self.assertRaisesMessage(CommandError, 'TXT_PATH_INVALID'):
                self.run_import(file=str(file))
        self.assertFalse(SourceDocument.objects.exists())

    def test_symlink_or_junction_path_is_rejected(self):
        link = self.root / 'linked.txt'
        try:
            link.symlink_to(self.file)
        except OSError:
            # Windows 일반 계정은 링크 생성 권한이 없으므로 감지 결과만 mock한다.
            with patch.object(Path, 'is_junction', return_value=True), self.assertRaisesMessage(CommandError, 'TXT_PATH_INVALID'):
                self.run_import()
        else:
            with self.assertRaisesMessage(CommandError, 'TXT_PATH_INVALID'):
                self.run_import(file=str(link))

    def test_backend_relative_path_under_default_root_is_supported(self):
        with override_settings(BASE_DIR=self.root, SOURCE_TXT_IMPORT_ROOT=self.root):
            document = self.run_import(file='official.txt')
        self.assertEqual(document.source_url, self.url)

    def test_invalid_official_urls_are_rejected_without_echoing_secrets(self):
        for url in (
            'http://www.immigration.go.kr/page', 'https://example.org/page',
            'https://www.immigration.go.kr.evil.org/page', 'https://127.0.0.1/page',
            'https://user:super-secret@www.immigration.go.kr/page',
            'https://www.immigration.go.kr/page?serviceKey=super-secret',
            'https://www.immigration.go.kr:8443/page', '',
        ):
            with self.subTest(url=url), self.assertRaises(CommandError) as error:
                self.run_import(url=url)
            self.assertNotIn('super-secret', str(error.exception))
        self.assertFalse(SourceDocument.objects.exists())

    def test_metadata_and_dates_are_validated_before_database_writes(self):
        for changes in (
            {'title': ''}, {'agency': 'a' * 256}, {'registered_by': ''}, {'terms_note': ''},
            {'terms_url': 'https://example.org/terms'}, {'terms_checked_on': None},
            {'published_at': timezone.localdate() + timedelta(days=1)},
            {'terms_checked_on': timezone.localdate() + timedelta(days=1)},
        ):
            with self.subTest(changes=changes), self.assertRaises(CommandError):
                self.run_import(**changes)
        for args in (('--region', '미상'), ('--category', '미상'), ('--published-at', 'invalid')):
            with self.subTest(args=args), self.assertRaises(CommandError):
                call_command('import_official_source_txt', *args, **self.options)
        self.assertFalse(SourceDocument.objects.exists())

    def test_database_failure_is_sanitized_and_file_untouched(self):
        before = self.file.read_bytes()
        with patch('sources.services.manual_import.stage_official_record', side_effect=DatabaseError('secret-key')), self.assertRaisesMessage(CommandError, 'IMPORT_DB_FAILED') as error:
            self.run_import()
        self.assertNotIn('secret-key', str(error.exception))
        self.assertEqual(self.file.read_bytes(), before)

    def test_pending_duplicate_reuses_document_revision_and_original_creation_time(self):
        document = self.run_import()
        revision = self.pending()
        self.run_import(url=self.url + '#section')
        self.assertEqual((SourceDocument.objects.count(), SourceRevision.objects.count()), (1, 1))
        self.assertEqual(self.pending().created_at, revision.created_at)
        self.assertEqual(self.pending().pk, revision.pk)
        self.assertEqual(SourceDocument.objects.get().pk, document.pk)
        self.assertIn('[unchanged]', self.output.getvalue())

    def test_approved_duplicate_does_not_request_reprocessing(self):
        self.run_import()
        document = self.approve()
        build_source_chunks([document.pk])
        document.refresh_from_db()
        version = document.version
        self.run_import()
        document.refresh_from_db()
        self.assertEqual((document.status, document.version), ('ACTIVE', version))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertEqual(SourceRevision.objects.count(), 1)

    def test_changed_body_preserves_approved_active_document_and_chunks_until_approval(self):
        self.run_import()
        document = self.approve()
        build_source_chunks([document.pk])
        document.refresh_from_db()
        approved = (document.content_text, document.content_hash, document.version, document.collected_at, document.metadata)
        old_chunks = list(SourceChunk.objects.values())
        self.file.write_text('변경된 공식 신청조건과 절차입니다.', encoding='utf-8')
        self.run_import(title='변경된 안내')
        revision = self.pending()
        document.refresh_from_db()
        self.assertEqual((document.content_text, document.content_hash, document.version, document.collected_at, document.metadata), approved)
        self.assertEqual((document.status, document.review_status), ('ACTIVE', 'APPROVED'))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertEqual(list(SourceChunk.objects.values()), old_chunks)
        self.assertTrue(document.chunks.get().is_current)
        self.assertTrue(self.results())
        self.assertEqual((revision.previous_content_text, revision.previous_hash), approved[:2])
        review_revision(revision.pk, self.admin, approve=True)
        document.refresh_from_db()
        self.assertEqual((document.status, document.review_status), ('PENDING', 'APPROVED'))
        self.assertTrue(document.ai_reprocessing_required)
        self.assertFalse(document.chunks.get().is_current)
        self.assertEqual(self.results(), [])

    def test_same_body_metadata_change_needs_review_without_unnecessary_ai_reprocessing(self):
        self.run_import()
        document = self.approve()
        build_source_chunks([document.pk])
        self.run_import(title='원문 제목 정정')
        document = self.approve()
        self.assertEqual(document.title, '원문 제목 정정')
        self.assertEqual(document.status, 'ACTIVE')
        self.assertFalse(document.ai_reprocessing_required)

    def test_existing_html_url_is_linked_and_api_identity_conflict_rolls_back(self):
        original = SourceDocument.objects.create(
            source_url=self.url, title='기존 제목', agency='법무부', region='전국', category='출입국·체류',
            content_text='기존 승인 본문', content_hash='a' * 64, status='ACTIVE', review_status='APPROVED',
        )
        self.assertEqual(self.run_import().pk, original.pk)
        self.assertEqual(self.pending().previous_content_text, '기존 승인 본문')
        original.provider, original.external_id = 'public_data', 'service-1'
        original.save(update_fields=['provider', 'external_id'])
        conflict_before = self.snapshots()
        with self.assertRaisesMessage(CommandError, 'IDENTITY_CONFLICT'):
            self.run_import()
        self.assertEqual(self.snapshots(), conflict_before)

    def test_version_and_latest_observation_conflicts_reject_old_candidate(self):
        document = self.run_import()
        first = self.pending()
        self.file.write_text('두 번째 공식 본문', encoding='utf-8')
        self.run_import()
        latest = SourceRevision.objects.exclude(pk=first.pk).get()
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(first.pk, self.admin, approve=True)
        SourceDocument.objects.filter(pk=document.pk).update(version=1)
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(latest.pk, self.admin, approve=True)
        document.refresh_from_db()
        self.assertEqual(document.content_text, '')

    def test_double_approval_conflicts_and_new_version_reimport_is_reviewable(self):
        self.run_import()
        first = self.pending()
        document = self.approve()
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(first.pk, self.admin, approve=True)
        self.file.write_text('세 번째 승인 대상 본문', encoding='utf-8')
        self.run_import()
        self.assertEqual(self.pending().base_version, document.version)
        self.approve()

    def test_older_observation_cannot_replace_latest_import(self):
        document = self.run_import()
        revision = self.pending()
        record = OfficialRecord(
            provider='html', external_id='', source_url=self.url, title='이전 문서',
            agency='법무부', region='전국', category='출입국·체류', content_text='이전 본문',
        )
        outcome, _ = stage_official_record(
            record, document.observed_started_at - timedelta(seconds=1), timezone.now(),
        )
        self.assertEqual(outcome, 'skipped')
        self.assertEqual(self.pending().pk, revision.pk)
        self.approve()

    def test_unverified_and_prohibited_terms_cannot_be_approved_or_activated(self):
        for status in ('unverified', 'prohibited'):
            with self.subTest(status=status):
                document = self.run_import(terms_status=status, terms_checked_on=None)
                with self.assertRaisesMessage(CollectionError, 'TERMS_UNCONFIRMED'):
                    review_revision(self.pending().pk, self.admin, approve=True)
                document.refresh_from_db()
                self.assertEqual((document.status, document.review_status), ('PENDING', 'PENDING_REVIEW'))
                self.assertFalse(_targets().exists())
                self.assertEqual(self.results(), [])

    def test_confirmed_terms_reimport_updates_same_pending_candidate(self):
        self.run_import(terms_status='unverified', terms_checked_on=None)
        revision_id = self.pending().pk
        self.run_import()
        self.assertEqual(self.pending().pk, revision_id)
        document = self.approve()
        self.assertEqual((document.status, document.review_status), ('PENDING', 'APPROVED'))
        self.assertTrue(document.ai_reprocessing_required)

    def test_disabled_registry_notice_stays_blocked_even_if_importer_claims_permission(self):
        disabled = next(item for item in load_registry() if not item.enabled and 'pds_no=' in item.url)
        document = self.run_import(url=disabled.url)
        with self.assertRaisesMessage(CollectionError, 'SOURCE_DISABLED'):
            review_revision(self.pending().pk, self.admin, approve=True)
        self.assertEqual(document.status, 'PENDING')
        self.assertFalse(_targets().exists())

    def test_automatic_refresh_cannot_remove_manual_usage_gate(self):
        document = self.run_import(terms_status='unverified', terms_checked_on=None)
        target = SourceRefreshTarget.objects.create(provider='html', source_url=self.url, document=document)
        record = OfficialRecord(
            provider='html', external_id='', source_url=self.url, title='자동 본문',
            agency='법무부', region='전국', category='출입국·체류', content_text='자동으로 읽은 공식 본문',
        )
        with patch('sources.services.refresh_service.fetch_record', return_value=record):
            self.assertEqual(refresh_target(target, Mock()).outcome, 'candidate')
        candidate = SourceRevision.objects.get(content_hash=record.content_hash)
        with self.assertRaisesMessage(CollectionError, 'TERMS_UNCONFIRMED'):
            review_revision(candidate.pk, self.admin, approve=True)

    def test_automatic_refresh_preserves_manual_policy_on_previously_automatic_document(self):
        document = SourceDocument.objects.create(
            source_url=self.url, title='기존 자동 자료', agency='법무부', region='전국', category='출입국·체류',
            content_text=self.text, content_hash=hashlib.sha256(self.text.encode('utf-8')).hexdigest(),
            status='ACTIVE', review_status='APPROVED', metadata={'upstream': 'previous'},
        )
        self.run_import(terms_status='unverified', terms_checked_on=None)
        manual = self.pending()
        trace = manual.metadata['metadata']['manual_import']
        target = SourceRefreshTarget.objects.create(provider='html', source_url=self.url, document=document)
        record = OfficialRecord(
            provider='html', external_id='', source_url=self.url, title='자동 수집 제목 정정',
            agency='법무부', region='전국', category='출입국·체류', content_text=self.text,
            metadata={'upstream': 'changed'},
        )
        with patch('sources.services.refresh_service.fetch_record', return_value=record):
            self.assertEqual(refresh_target(target, Mock()).outcome, 'unchanged')
        candidate = self.pending()
        self.assertEqual(candidate.pk, manual.pk)
        with self.assertRaisesMessage(CollectionError, 'TERMS_UNCONFIRMED'):
            review_revision(candidate.pk, self.admin, approve=True)
        self.assertEqual(candidate.metadata['metadata']['manual_import'], trace)
        self.assertEqual(candidate.metadata['metadata']['usage_terms']['status'], 'unverified')
        self.assertEqual(candidate.metadata['metadata']['upstream'], 'changed')
        self.assertEqual(candidate.metadata['title'], '자동 수집 제목 정정')

    def test_disabled_notice_url_variants_cannot_bypass_exclusion(self):
        disabled = next(item for item in load_registry() if not item.enabled and 'pds_no=' in item.url)
        parts = urlsplit(disabled.url)
        pairs = parse_qsl(parts.query)
        notice_id = dict(pairs)['pds_no']
        variants = (
            disabled.url + '&utm_source=manual',
            urlunsplit(parts._replace(query=urlencode(list(reversed(pairs))))),
            urlunsplit(parts._replace(netloc='asan.go.kr', query='pds_no=' + notice_id)),
            urlunsplit(parts._replace(query='%70ds_no=' + notice_id + '&utm_medium=txt')),
        )
        for url in variants:
            with self.subTest(url=url):
                document = self.run_import(url=url)
                candidate = document.revisions.get(review_status='PENDING_REVIEW')
                self.assertTrue(candidate.metadata['metadata']['usage_terms']['registry_excluded'])
                with self.assertRaisesMessage(CollectionError, 'SOURCE_DISABLED'):
                    review_revision(candidate.pk, self.admin, approve=True)
                document.refresh_from_db()
                self.assertEqual(document.status, 'PENDING')

    def test_exclusion_identity_keeps_other_official_pages_available(self):
        self.assertFalse(is_registry_excluded('https://www.asan.go.kr/main/cms/?no=93&utm_source=manual'))
        self.assertFalse(is_registry_excluded('https://www.asan.go.kr/main/cms/?pds_no=test-fixture-other'))
        disabled_paths = [spec for spec in load_registry() if not spec.enabled and not urlsplit(spec.url).query]
        for disabled in disabled_paths:
            with self.subTest(url=disabled.url):
                self.assertTrue(is_registry_excluded(disabled.url + '?utm_source=manual'))

    def test_rejection_keeps_new_document_out_of_search_and_can_be_resubmitted(self):
        self.run_import()
        revision = self.pending()
        review_revision(revision.pk, self.admin, approve=False)
        document = SourceDocument.objects.get()
        self.assertEqual(document.review_status, 'REJECTED')
        self.assertFalse(_targets().exists())
        self.assertEqual(self.results(), [])
        self.run_import()
        self.assertNotEqual(self.pending().pk, revision.pk)
        self.assertEqual(SourceRevision.objects.count(), 2)

    def test_rejecting_change_preserves_active_approved_search(self):
        self.run_import()
        document = self.approve()
        build_source_chunks([document.pk])
        document.refresh_from_db()
        before = (document.content_text, document.content_hash, document.version)
        self.file.write_text('반려할 변경 본문', encoding='utf-8')
        self.run_import()
        review_revision(self.pending().pk, self.admin, approve=False)
        document.refresh_from_db()
        self.assertEqual((document.content_text, document.content_hash, document.version), before)
        self.assertTrue(document.ai_eligible)
        self.assertTrue(self.results())

    def test_existing_collect_command_cannot_overwrite_manual_candidate(self):
        self.run_import()
        legacy_spec = replace(spec(), url=self.url)
        result = collect_source(legacy_spec, collector())
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('MANAGED_SOURCE', result.message)
        self.assertEqual(SourceDocument.objects.get().content_text, '')

    def test_existing_admin_screen_and_action_approve_manual_revision(self):
        self.run_import()
        revision = self.pending()
        self.client.force_login(self.admin)
        detail = self.client.get(reverse('admin:sources_sourcerevision_change', args=[revision.pk]))
        self.assertContains(detail, '공식 URL만으로 원문 일치를 보증하지 않습니다.')
        self.assertContains(detail, '등록 담당자')
        self.assertContains(detail, self.text.splitlines()[0])
        self.assertContains(detail, 'usage_terms')
        response = self.client.post(reverse('admin:sources_sourcerevision_changelist'), {
            'action': 'approve_candidates', '_selected_action': [revision.pk],
        })
        self.assertEqual(response.status_code, 302)
        revision.refresh_from_db()
        document = SourceDocument.objects.get()
        self.assertEqual(revision.review_status, 'APPROVED')
        self.assertEqual(revision.reviewed_by_id, self.admin.pk)
        self.assertTrue(LogEntry.objects.filter(object_id=str(revision.pk), user=self.admin).exists())
        self.assertEqual((document.status, document.review_status), ('PENDING', 'APPROVED'))
        self.assertTrue(document.ai_reprocessing_required)

    def test_admin_blocks_unverified_approval_and_forced_active_before_indexing(self):
        document = self.run_import(terms_status='unverified', terms_checked_on=None)
        self.client.force_login(self.admin)
        self.client.post(reverse('admin:sources_sourcerevision_changelist'), {
            'action': 'approve_candidates', '_selected_action': [self.pending().pk],
        })
        document.refresh_from_db()
        self.assertEqual(document.review_status, 'PENDING_REVIEW')
        data = {
            'title': document.title, 'agency': document.agency, 'region': document.region,
            'category': document.category, 'published_at': '', 'status': 'ACTIVE',
            'expected_version': document.version, '_save': '저장',
        }
        url = reverse('admin:sources_sourcedocument_change', args=[document.pk])
        response = self.client.post(url, data)
        self.assertContains(response, 'TXT는 변경 후보의 관리자 승인 전')
        self.run_import()
        document = self.approve()
        response = self.client.post(url, {**data, 'expected_version': document.version})
        self.assertContains(response, 'AI 재처리가 끝날 때까지')
        document.refresh_from_db()
        self.assertEqual(document.status, 'PENDING')

    def test_ai_contract_activates_only_after_successful_explicit_indexing(self):
        self.run_import()
        document = self.approve()
        self.assertTrue(_targets().filter(pk=document.pk).exists())
        self.assertTrue(document.content_hash and document.content_text)
        self.assertEqual(self.results(), [])
        stats = build_source_chunks([document.pk])
        document.refresh_from_db()
        self.assertEqual((stats['completed'], stats['activated']), (1, 1))
        self.assertEqual((document.status, document.review_status), ('ACTIVE', 'APPROVED'))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertTrue(document.chunks.get().is_current)
        self.assertTrue(self.results())

    def test_failed_indexing_and_inactive_document_do_not_activate(self):
        self.run_import()
        document = self.approve()
        with patch('ai.chatbot.indexing.embed_passages', side_effect=RuntimeError('테스트 임베딩 실패')), self.assertRaises(RuntimeError):
            build_source_chunks([document.pk])
        document.refresh_from_db()
        self.assertEqual(document.status, 'PENDING')
        self.assertTrue(document.ai_reprocessing_required)
        SourceDocument.objects.filter(pk=document.pk).update(status='INACTIVE')
        self.file.write_text('사용 중지 상태의 새 공식 본문', encoding='utf-8')
        self.run_import()
        document = self.approve()
        self.assertEqual(document.status, 'INACTIVE')
        self.assertEqual(build_source_chunks([document.pk])['activated'], 0)
        self.assertFalse(document.chunks.exists())


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL 실제 동시 쓰기는 격리된 PostgreSQL CI에서 검증')
class ManualImportConcurrencyTests(TransactionTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        file = root / 'concurrent.txt'
        file.write_text('동시 등록할 공식 TXT 본문', encoding='utf-8')
        settings = override_settings(SOURCE_TXT_IMPORT_ROOT=root)
        settings.enable()
        self.addCleanup(settings.disable)
        self.options = {
            'file': str(file), 'url': 'https://www.immigration.go.kr/test-fixtures/concurrent',
            'agency': '법무부', 'title': '동시 등록 안내', 'region': '전국', 'category': '출입국·체류',
            'registered_by': '동시 등록 테스트', 'terms_note': '가상 AI 가공 허가',
            'terms_status': 'confirmed', 'terms_checked_on': date(2026, 1, 1),
        }

    def run_together(self, first, second):
        barrier = Barrier(2)

        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(run, operation) for operation in (first, second)]
            return [task.result(timeout=20) for task in tasks]

    def test_concurrent_initial_imports_share_one_document_and_candidate(self):
        results = self.run_together(
            lambda: import_official_txt(**self.options), lambda: import_official_txt(**self.options),
        )
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.assertEqual(SourceRevision.objects.count(), 1)
        self.assertEqual(results[0].document_id, results[1].document_id)
        self.assertEqual(results[0].revision_id, results[1].revision_id)

    def test_import_and_approval_serialize_without_losing_new_candidate(self):
        result = import_official_txt(**self.options)
        original = SourceRevision.objects.get(pk=result.revision_id)
        admin = get_user_model().objects.create_superuser(
            email='manual-concurrent@example.com', password='test-password',
        )
        Path(self.options['file']).write_text('새롭게 변경된 공식 TXT 본문', encoding='utf-8')

        def approve():
            try:
                review_revision(original.pk, admin, approve=True)
                return 'APPROVED'
            except CollectionError as error:
                return error.code

        results = self.run_together(lambda: import_official_txt(**self.options), approve)
        self.assertIn(results[1], ('APPROVED', 'REVIEW_CONFLICT'))
        document = SourceDocument.objects.get()
        latest = SourceRevision.objects.get(pk=results[0].revision_id)
        self.assertEqual(latest.base_version, document.version)
        self.assertEqual(latest.review_status, 'PENDING_REVIEW')
        self.assertEqual(latest.content_text, '새롭게 변경된 공식 TXT 본문')
        self.assertIn(document.content_hash, ('', original.content_hash))
        self.assertEqual(document.status, 'PENDING')
