"""관리자 TXT 업로드부터 기존 승인 액션까지 격리 DB에서 검증한다."""

import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.urls import reverse
from django.utils import timezone

from sources.models import CrawlLog, SourceChunk, SourceDocument, SourceRefreshTarget, SourceRevision
from sources.services.errors import CollectionError
from sources.services.manual_import import MAX_TXT_BYTES, read_official_txt
from sources.services.source_registry import load_registry


class AdminTxtImportTests(TestCase):
    permissions = (
        'add_sourcedocument', 'change_sourcedocument',
        'add_sourcerevision', 'change_sourcerevision',
    )

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser(email='txt-admin@example.com', password=None)
        cls.staff = User.objects.create_user(email='txt-staff@example.com', is_staff=True)
        cls.regular = User.objects.create_user(email='txt-user@example.com')
        cls.inactive = User.objects.create_user(email='txt-inactive@example.com', is_staff=True, is_active=False)

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('admin:sources_sourcedocument_import_txt')
        self.list_url = reverse('admin:sources_sourcedocument_changelist')
        self.revisions_url = reverse('admin:sources_sourcerevision_changelist')
        self.text = '공식 신청 안내입니다.\r\n신청대상과 구비서류를 원문대로 확인하세요.\n'
        self.data = {
            'title': '공식 신청 안내', 'agency': '법무부',
            'url': 'https://www.immigration.go.kr/test-fixtures/admin-upload',
            'region': '전국', 'category': '출입국·체류', 'published_at': '2025-01-01',
            'terms_status': 'confirmed', 'terms_note': '테스트용 가상 허가: 출처표시 조건으로 AI 가공·임베딩·검색 허용',
            'terms_url': 'https://www.immigration.go.kr/test-fixtures/terms',
            'terms_checked_on': '2026-01-01',
        }
        for name in ('requests.sessions.Session.request', 'httpx.Client.send'):
            blocker = patch(name, side_effect=AssertionError('외부 서비스 호출 금지'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def upload(self, *, raw=None, filename='official.txt', **changes):
        file = SimpleUploadedFile(filename, raw if raw is not None else self.text.encode('utf-8'), content_type='text/plain')
        return self.client.post(self.url, {**self.data, 'file': file, **changes})

    def approve(self, revision):
        return self.client.post(self.revisions_url, {
            'action': 'approve_candidates', '_selected_action': [revision.pk],
        }, follow=True)

    def assert_no_import(self):
        for model in (SourceDocument, SourceRevision, SourceChunk, SourceRefreshTarget, CrawlLog, LogEntry):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_entry_link_get_form_csrf_and_get_has_no_side_effects(self):
        self.assertContains(self.client.get(self.list_url), self.url)
        response = self.client.get(self.url)
        self.assertContains(response, 'multipart/form-data')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, self.admin.email)
        self.assertNotIn('registered_by', response.context['form'].fields)
        self.assert_no_import()
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_utf8_registration_preserves_body_hash_provenance_and_pending_state(self):
        with patch('ai.chatbot.indexing.embed_passages') as embed:
            response = self.upload(registered_by='forged@example.com', review_status='APPROVED', status='ACTIVE')
        embed.assert_not_called()
        revision, document = SourceRevision.objects.get(), SourceDocument.objects.get()
        self.assertRedirects(response, reverse('admin:sources_sourcerevision_change', args=[revision.pk]))
        self.assertEqual((document.status, document.review_status), ('PENDING', 'PENDING_REVIEW'))
        self.assertEqual((document.content_text, document.content_hash, document.version), ('', '', 0))
        self.assertFalse(document.ai_eligible)
        self.assertFalse(document.ai_reprocessing_required)
        self.assertEqual(revision.review_status, 'PENDING_REVIEW')
        self.assertEqual((revision.base_version, revision.previous_hash), (0, ''))
        self.assertEqual(revision.content_text, self.text)
        digest = hashlib.sha256(self.text.encode('utf-8')).hexdigest()
        self.assertEqual(revision.content_hash, digest)
        self.assertEqual(revision.metadata['published_at'], self.data['published_at'])
        metadata = revision.metadata['metadata']
        self.assertEqual(metadata['manual_import'], {
            'method': 'manual_txt', 'filename': 'official.txt', 'registered_by': self.admin.email,
            'encoding': 'utf-8', 'raw_sha256': digest, 'byte_size': len(self.text.encode('utf-8')),
        })
        self.assertEqual(metadata['usage_terms']['checked_by'], self.admin.email)
        self.assertEqual(metadata['usage_terms']['checked_on'], self.data['terms_checked_on'])
        log = LogEntry.objects.get()
        self.assertEqual((log.user_id, log.object_id, log.action_flag), (self.admin.pk, str(revision.pk), ADDITION))
        self.assertEqual(log.content_type.model, 'sourcerevision')
        self.assertIn(digest, log.change_message)
        self.assertNotIn(self.text, log.change_message)
        for model in (SourceChunk, SourceRefreshTarget, CrawlLog):
            self.assertFalse(model.objects.exists())

    def test_uploaded_candidate_can_be_approved_using_existing_admin_action(self):
        self.upload()
        revision = SourceRevision.objects.get()
        response = self.approve(revision)
        self.assertContains(response, '검토 결과를 기록했습니다')
        revision.refresh_from_db()
        document = SourceDocument.objects.get()
        self.assertEqual(revision.review_status, 'APPROVED')
        self.assertEqual((document.status, document.review_status), ('PENDING', 'APPROVED'))
        self.assertEqual((document.content_text, document.content_hash), (self.text, revision.content_hash))
        self.assertEqual(document.version, 1)
        self.assertTrue(document.ai_reprocessing_required)
        self.assertFalse(document.ai_eligible)
        self.assertEqual(document.reviewed_by, self.admin)
        self.assertEqual(revision.reviewed_by, self.admin)
        self.assertIsNotNone(revision.reviewed_at)
        self.assertEqual(LogEntry.objects.filter(action_flag=CHANGE).count(), 1)
        self.assertFalse(SourceChunk.objects.exists())

    def test_bom_and_temporary_upload_use_identical_validation(self):
        raw = b'\xef\xbb\xbf' + self.text.encode('utf-8')
        with override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0):
            response = self.upload(raw=raw, filename='official.TXT')
        self.assertEqual(response.status_code, 302)
        revision = SourceRevision.objects.get()
        self.assertEqual(revision.content_text, self.text)
        trace = revision.metadata['metadata']['manual_import']
        self.assertEqual(trace['encoding'], 'utf-8-sig')
        self.assertEqual(trace['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(trace['byte_size'], len(raw))

    def test_invalid_files_are_reported_without_writes(self):
        for filename, raw in (
            ('official.csv', b'plain text'), ('official.txt.exe', b'plain text'),
            ('official.txt', b''), ('official.txt', b' \r\n\t'),
            ('official.txt', b'\xef\xbb\xbf'), ('official.txt', b'\xff\xfe'),
            ('official.txt', '공식자료'.encode('cp949')),
            ('official.txt', b'%PDF-1.7 fake'), ('official.txt', b'PK\x03\x04fake'),
            ('official.txt', b'header\x00binary'), ('official.txt', b'{\\rtf fake}'),
        ):
            with self.subTest(filename=filename, raw=raw):
                response = self.upload(raw=raw, filename=filename)
                self.assertEqual(response.status_code, 200)
                self.assertIn('file', response.context['form'].errors)
                self.assert_no_import()

    def test_missing_file_and_posted_server_path_are_rejected(self):
        for extra in ({}, {'file': 'sources/data/manual/server-secret.txt'}):
            with self.subTest(extra=extra):
                response = self.client.post(self.url, {**self.data, **extra})
                self.assertIn('file', response.context['form'].errors)
                self.assert_no_import()

    def test_size_over_limit_including_bom_is_rejected(self):
        for raw in (b'a' * (MAX_TXT_BYTES + 1), b'\xef\xbb\xbf' + b'a' * (MAX_TXT_BYTES - 2)):
            with self.subTest(bom=raw.startswith(b'\xef\xbb\xbf')):
                self.assertContains(self.upload(raw=raw), 'TXT_TOO_LARGE')
                self.assert_no_import()

    def test_exact_size_limit_is_accepted(self):
        response = self.upload(raw=b'a' * MAX_TXT_BYTES)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SourceRevision.objects.get().metadata['metadata']['manual_import']['byte_size'], MAX_TXT_BYTES)

    def test_uploaded_stream_cannot_bypass_size_or_read_error_checks(self):
        upload = SimpleUploadedFile('official.txt', b'a' * (MAX_TXT_BYTES + 1))
        upload.size = 1
        with self.assertRaisesMessage(CollectionError, 'TXT_TOO_LARGE'):
            read_official_txt(upload)
        body = encode_multipart(BOUNDARY, {**self.data, 'file': SimpleUploadedFile('official.txt', b'official text')})
        with patch('django.core.files.uploadedfile.InMemoryUploadedFile.read', side_effect=OSError('server-secret')):
            response = self.client.generic('POST', self.url, body, content_type=MULTIPART_CONTENT)
        self.assertContains(response, 'TXT_UPLOAD_FAILED')
        self.assertNotContains(response, 'server-secret')
        self.assert_no_import()

    def test_official_https_allowlist_applies_to_source_and_terms_urls(self):
        for field in ('url', 'terms_url'):
            for value in (
                'http://www.immigration.go.kr/page', 'https://example.org/page',
                'https://www.immigration.go.kr.evil.org/page', 'https://127.0.0.1/page',
                'https://user:secret@www.immigration.go.kr/page',
                'https://www.immigration.go.kr/page?serviceKey=secret',
                'https://www.immigration.go.kr:8443/page',
            ):
                with self.subTest(field=field, value=value):
                    response = self.upload(**{field: value})
                    self.assertTrue(response.context['form'].errors)
                    self.assert_no_import()

    def test_invalid_metadata_dates_and_missing_evidence_do_not_write(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        for changes in (
            {'title': ''}, {'agency': 'a' * 256}, {'region': '미상'}, {'category': '미상'},
            {'terms_status': 'invalid'}, {'terms_note': ''}, {'terms_note': '근거\n두 줄'},
            {'terms_checked_on': ''}, {'terms_checked_on': tomorrow}, {'published_at': tomorrow},
            {'terms_note': 'a' * 4001},
        ):
            with self.subTest(changes=changes):
                response = self.upload(**changes)
                self.assertTrue(response.context['form'].errors)
                self.assert_no_import()

    def test_duplicate_reuses_pending_revision_and_records_actual_staff_actor(self):
        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='sources', codename__in=self.permissions))
        self.client.force_login(self.staff)
        self.upload()
        revision = SourceRevision.objects.get()
        response = self.upload(url=self.data['url'] + '#section', terms_note='같은 본문에 대한 최신 확인 근거')
        self.assertRedirects(response, reverse('admin:sources_sourcerevision_change', args=[revision.pk]))
        self.assertEqual((SourceDocument.objects.count(), SourceRevision.objects.count()), (1, 1))
        current = SourceRevision.objects.get()
        self.assertEqual(current.created_at, revision.created_at)
        self.assertEqual(current.metadata['metadata']['usage_terms']['note'], '같은 본문에 대한 최신 확인 근거')
        self.assertEqual(current.metadata['metadata']['manual_import']['registered_by'], self.staff.email)
        self.assertEqual(list(LogEntry.objects.order_by('id').values_list('action_flag', flat=True)), [ADDITION, CHANGE])
        self.assertEqual(set(LogEntry.objects.values_list('user_id', flat=True)), {self.staff.pk})

    def test_approved_identical_upload_does_not_create_revision_or_change_version(self):
        self.upload()
        self.approve(SourceRevision.objects.get())
        before = SourceDocument.objects.get()
        response = self.upload()
        self.assertRedirects(response, reverse('admin:sources_sourcedocument_change', args=[before.pk]))
        after = SourceDocument.objects.get()
        self.assertEqual((after.version, after.content_hash, after.status), (before.version, before.content_hash, before.status))
        self.assertEqual(SourceRevision.objects.count(), 1)
        self.assertEqual(LogEntry.objects.latest('id').content_type.model, 'sourcedocument')

    def test_new_body_preserves_existing_active_document_and_chunks_until_approval(self):
        self.upload()
        self.approve(SourceRevision.objects.get())
        document = SourceDocument.objects.get()
        SourceDocument.objects.filter(pk=document.pk).update(status='ACTIVE', ai_reprocessing_required=False)
        document.refresh_from_db()
        chunk = SourceChunk.objects.create(source_document=document, chunk_order=0, chunk_text=self.text, source_content_hash=document.content_hash)
        response = self.upload(raw='변경된 공식 본문'.encode('utf-8'))
        self.assertEqual(response.status_code, 302)
        after = SourceDocument.objects.get()
        for field in ('status', 'review_status', 'content_hash', 'content_text', 'version', 'metadata', 'ai_reprocessing_required'):
            self.assertEqual(getattr(after, field), getattr(document, field), field)
        revision = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        self.assertEqual((revision.previous_hash, revision.base_version), (document.content_hash, document.version))
        self.assertEqual(SourceChunk.objects.get().pk, chunk.pk)

    def test_older_candidate_is_rejected_by_existing_observation_conflict_check(self):
        self.upload()
        older = SourceRevision.objects.get()
        self.upload(raw='최신 공식 본문'.encode('utf-8'))
        self.assertContains(self.approve(older), 'REVIEW_CONFLICT')
        older.refresh_from_db()
        self.assertEqual(older.review_status, 'PENDING_REVIEW')
        self.assertEqual(SourceDocument.objects.get().content_text, '')

    def test_unverified_prohibited_and_registry_excluded_candidates_can_be_approved(self):
        excluded = next(spec.url for spec in load_registry() if not spec.enabled)
        cases = (
            ('unverified', self.data['url'] + '/unverified'),
            ('prohibited', self.data['url'] + '/prohibited'),
            ('confirmed', excluded),
        )
        for status, url in cases:
            with self.subTest(status=status, url=url):
                response = self.upload(url=url, terms_status=status)
                self.assertEqual(response.status_code, 302)
                revision = SourceRevision.objects.get(document__source_url=url)
                self.assertContains(self.approve(revision), '검토 결과를 기록했습니다')
                revision.refresh_from_db()
                document = revision.document
                self.assertEqual((revision.review_status, document.review_status, document.status), ('APPROVED', 'APPROVED', 'PENDING'))
                self.assertFalse(document.ai_eligible)
                self.assertEqual(document.content_text, self.text)
                self.assertEqual(document.metadata['usage_terms']['status'], status)
                self.assertTrue(document.ai_reprocessing_required)
        self.assertEqual(LogEntry.objects.filter(action_flag=CHANGE).count(), 3)

    def test_correcting_terms_reuses_candidate_and_then_allows_existing_approval(self):
        self.upload(terms_status='unverified', terms_checked_on='')
        revision = SourceRevision.objects.get()
        self.upload()
        self.assertEqual(SourceRevision.objects.get().pk, revision.pk)
        self.approve(revision)
        self.assertEqual(SourceDocument.objects.get().review_status, 'APPROVED')

    def test_unverified_with_check_date_is_informational_and_does_not_block_approval(self):
        response = self.upload(terms_status='unverified')
        self.assertEqual(response.status_code, 302)
        revision = SourceRevision.objects.get()
        detail_url = reverse('admin:sources_sourcerevision_change', args=[revision.pk])
        correction_url = f'{self.url}?revision={revision.pk}'
        detail = self.client.get(detail_url)
        self.assertNotContains(detail, '승인 차단')
        self.assertContains(detail, '미확인')
        self.assertContains(detail, correction_url)
        self.assertContains(detail, '확인일을 입력해도 AI 이용조건 확인 상태는 자동으로 바뀌지 않습니다')
        self.assertContains(self.client.get(self.revisions_url), '미확인')
        response = self.approve(revision)
        self.assertContains(response, '검토 결과를 기록했습니다')
        self.assertEqual(SourceDocument.objects.get().review_status, 'APPROVED')

    def test_correction_prefills_metadata_without_confirming_terms_or_modifying_rows(self):
        self.upload(terms_status='unverified')
        revision = SourceRevision.objects.get()
        before = [list(model.objects.values()) for model in (SourceDocument, SourceRevision, LogEntry)]
        response = self.client.get(self.url, {'revision': revision.pk})
        self.assertContains(response, '기존 입력을 불러왔습니다')
        initial = response.context['form'].initial
        for field in self.data:
            self.assertEqual(initial[field], 'unverified' if field == 'terms_status' else self.data[field], field)
        self.assertNotIn('file', initial)
        self.assertEqual([list(model.objects.values()) for model in (SourceDocument, SourceRevision, LogEntry)], before)

    def test_correction_form_requires_file_and_explicit_confirmation_then_approval(self):
        self.upload(terms_status='unverified')
        revision = SourceRevision.objects.get()
        correction_url = f'{self.url}?revision={revision.pk}'
        initial = self.client.get(correction_url).context['form'].initial
        response = self.client.post(correction_url, {**initial, 'terms_status': 'confirmed'})
        self.assertIn('file', response.context['form'].errors)
        revision.refresh_from_db()
        self.assertEqual(revision.metadata['metadata']['usage_terms']['status'], 'unverified')
        file = SimpleUploadedFile('official.txt', self.text.encode('utf-8'), content_type='text/plain')
        response = self.client.post(correction_url, {**initial, 'file': file, 'terms_status': 'confirmed'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual((SourceDocument.objects.count(), SourceRevision.objects.count()), (1, 1))
        revision.refresh_from_db()
        self.assertEqual(revision.review_status, 'PENDING_REVIEW')
        self.assertEqual(revision.metadata['metadata']['usage_terms']['status'], 'confirmed')
        self.assertEqual(SourceDocument.objects.get().review_status, 'PENDING_REVIEW')
        self.assertContains(self.client.get(self.revisions_url), 'AI 이용 허용 확인')
        self.assertContains(self.approve(revision), '검토 결과를 기록했습니다')
        revision.refresh_from_db()
        self.assertEqual(revision.review_status, 'APPROVED')
        document = SourceDocument.objects.get()
        self.assertEqual((document.status, document.review_status), ('PENDING', 'APPROVED'))
        self.assertTrue(document.ai_reprocessing_required)

    def test_prohibited_and_excluded_labels_are_informational_and_do_not_block_approval(self):
        excluded = next(spec.url for spec in load_registry() if not spec.enabled)
        for status, url, label in (
            ('prohibited', self.data['url'], '가공 금지'),
            ('confirmed', excluded, '수집 목록 제외 표시'),
        ):
            with self.subTest(status=status):
                self.upload(terms_status=status, url=url)
                revision = SourceRevision.objects.get(document__source_url=url)
                self.assertContains(self.client.get(self.revisions_url), label)
                self.approve(revision)
                revision.refresh_from_db()
                self.assertEqual(revision.review_status, 'APPROVED')

    def test_invalid_correction_identifiers_are_not_found(self):
        for value in ('missing', '999999', '-1', '999999999999999999999999999999999'):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(self.url, {'revision': value}).status_code, 404)
        self.assert_no_import()

    def test_reviewer_without_import_permissions_has_no_correction_link(self):
        self.upload(terms_status='unverified')
        revision = SourceRevision.objects.get()
        correction_url = f'{self.url}?revision={revision.pk}'
        self.staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='sources', codename__in=('change_sourcedocument', 'change_sourcerevision'),
        ))
        self.client.force_login(self.staff)
        detail = self.client.get(reverse('admin:sources_sourcerevision_change', args=[revision.pk]))
        self.assertContains(detail, '미확인')
        self.assertNotContains(detail, correction_url)
        self.assertNotContains(self.approve(revision), correction_url)
        revision.refresh_from_db()
        self.assertEqual(revision.review_status, 'APPROVED')
        self.assertEqual(self.client.get(correction_url).status_code, 403)
        response = self.client.post(correction_url, {**self.data, 'file': SimpleUploadedFile('official.txt', b'text')})
        self.assertEqual(response.status_code, 403)
        revision.refresh_from_db()
        self.assertEqual(revision.metadata['metadata']['usage_terms']['status'], 'unverified')

    def test_each_required_permission_is_enforced_for_get_post_and_link(self):
        self.client.force_login(self.staff)
        for missing in self.permissions:
            with self.subTest(missing=missing):
                self.staff.user_permissions.set(Permission.objects.filter(
                    content_type__app_label='sources', codename__in=[
                        *[value for value in self.permissions if value != missing],
                        'view_sourcedocument', 'view_sourcerevision',
                    ],
                ))
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.upload().status_code, 403)
                self.assertNotContains(self.client.get(self.list_url), self.url)
                self.assert_no_import()

    def test_anonymous_regular_and_inactive_users_cannot_upload(self):
        self.regular.user_permissions.add(*Permission.objects.filter(content_type__app_label='sources'))
        for user in (None, self.regular, self.inactive):
            with self.subTest(user=user):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                for response in (self.client.get(self.url), self.upload()):
                    self.assertEqual(response.status_code, 302)
                    self.assertIn(reverse('admin:login'), response.url)
                self.assert_no_import()

    def test_csrf_is_required_and_valid_token_allows_upload(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        self.assertEqual(self.upload().status_code, 403)
        self.assert_no_import()
        self.client.get(self.url)
        response = self.upload(csrfmiddlewaretoken=self.client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SourceRevision.objects.count(), 1)

    def test_database_error_is_sanitized_and_audit_failure_rolls_back_import(self):
        with patch('sources.services.manual_import.stage_official_record', side_effect=DatabaseError('secret-key')):
            response = self.upload()
        self.assertContains(response, '저장 충돌 또는 검증 실패')
        self.assertNotContains(response, 'secret-key')
        self.assert_no_import()
        with patch('sources.admin.SourceDocumentAdmin.log_addition', side_effect=DatabaseError('secret-key')):
            response = self.upload()
        self.assertContains(response, '저장 충돌 또는 검증 실패')
        self.assertNotContains(response, 'secret-key')
        self.assert_no_import()

    def test_identity_conflict_preserves_existing_api_document(self):
        document = SourceDocument.objects.create(
            provider='public_data', external_id='api-1', source_url=self.data['url'],
            title='기존 API 자료', agency='기존 기관', region='전국', category='출입국·체류',
        )
        before = list(SourceDocument.objects.values())
        self.assertContains(self.upload(), 'IDENTITY_CONFLICT')
        self.assertEqual(list(SourceDocument.objects.values()), before)
        self.assertFalse(SourceRevision.objects.exists())
        self.assertFalse(LogEntry.objects.exists())
        self.assertEqual(SourceDocument.objects.get().pk, document.pk)
