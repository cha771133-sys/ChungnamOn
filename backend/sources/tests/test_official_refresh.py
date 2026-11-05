import uuid
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from sources.models import CrawlLog, SourceChunk, SourceDocument, SourceRefreshTarget, SourceRevision
from sources.services.errors import CollectionError
from sources.services.freshness import filter_freshness, freshness, freshness_details, is_due, next_due_at
from sources.services.normalization import OfficialRecord
from sources.services.refresh_service import _apply_observation, claim, refresh_target, review_revision
from sources.services.source_service import collect_source

from .pipeline_helpers import collector, spec


def make_record(**changes):
    return OfficialRecord(**{
        'provider': 'public_data', 'external_id': 'service-1', 'source_url': None,
        'title': '공식 지원 안내', 'agency': '충청남도 아산시', 'region': '확인 필요',
        'category': '공공서비스 지원정책', 'content_text': '지원대상과 신청조건을 원문대로 보존합니다.', **changes,
    })


class OfficialRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(email='refresh-admin@example.com', password='test-password')
        cls.user = get_user_model().objects.create_user(email='refresh-user@example.com', password='test-password')

    def setUp(self):
        self.target = SourceRefreshTarget.objects.create(provider='public_data', external_id='service-1')
        self.record = make_record()
        self.client_stub = Mock(fetch=Mock(return_value=self.record))

    def refresh(self, record=None, **options):
        self.client_stub.fetch.return_value = record or self.record
        self.target.refresh_from_db()
        return refresh_target(self.target, self.client_stub, **options)

    def approve_initial(self):
        self.refresh()
        review_revision(SourceRevision.objects.get().pk, self.admin, approve=True)
        return SourceDocument.objects.get()

    def test_new_record_is_candidate_with_success_dates_and_stable_identity(self):
        self.assertEqual(self.refresh().outcome, 'new')
        document = SourceDocument.objects.get()
        revision = SourceRevision.objects.get()
        self.assertIsNone(document.source_url)
        self.assertEqual(document.content_text, '')
        self.assertEqual(document.content_hash, '')
        self.assertIsNone(document.collected_at)
        self.assertEqual(document.review_status, 'PENDING_REVIEW')
        self.assertEqual(document.status, 'PENDING')
        self.assertIsNotNone(document.last_success_at)
        self.assertEqual(revision.content_hash, self.record.content_hash)
        self.assertEqual(revision.content_text, self.record.content_text)
        self.assertEqual(CrawlLog.objects.get().result, 'SUCCESS')
        self.assertIsNone(revision.reviewed_by_id)

    def test_approve_records_actual_reviewer_and_keeps_collection_time(self):
        self.refresh()
        revision = SourceRevision.objects.get()
        before = timezone.now()
        review_revision(revision.pk, self.admin, approve=True)
        revision.refresh_from_db()
        document = SourceDocument.objects.get()
        self.assertEqual(document.review_status, 'APPROVED')
        self.assertEqual(document.content_text, self.record.content_text)
        self.assertEqual(document.content_hash, self.record.content_hash)
        self.assertEqual(document.collected_at, revision.collected_at)
        self.assertGreaterEqual(document.reviewed_at, before)
        self.assertEqual((revision.reviewed_by_id, document.reviewed_by_id), (self.admin.pk, self.admin.pk))
        self.assertTrue(document.ai_reprocessing_required)
        self.assertEqual(document.status, 'PENDING')

    def test_same_body_refresh_no_revision_no_reembedding_and_updates_confirmation(self):
        document = self.approve_initial()
        SourceDocument.objects.filter(pk=document.pk).update(ai_reprocessing_required=False, status='ACTIVE', collected_at=timezone.now() - timedelta(days=1))
        old_version = document.version
        self.assertEqual(self.refresh().outcome, 'unchanged')
        document.refresh_from_db()
        self.assertEqual(SourceRevision.objects.count(), 1)
        self.assertGreater(document.collected_at, timezone.now() - timedelta(minutes=1))
        self.assertFalse(document.ai_reprocessing_required)
        self.assertEqual(document.version, old_version)

    def test_repeated_unreviewed_body_deduplicates_document_and_candidate(self):
        self.refresh()
        self.assertEqual(self.refresh().outcome, 'unchanged')
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.assertEqual(SourceRevision.objects.count(), 1)
        self.assertEqual(CrawlLog.objects.count(), 2)

    def test_same_body_metadata_review_does_not_request_reembedding_again(self):
        self.record = replace(self.record, source_url='https://www.gov.kr/test-fixtures/old-link')
        document = self.approve_initial()
        SourceDocument.objects.filter(pk=document.pk).update(ai_reprocessing_required=False, status='ACTIVE')
        updated_link = replace(self.record, source_url='https://www.gov.kr/test-fixtures/new-link')
        self.assertEqual(self.refresh(updated_link).outcome, 'candidate')
        candidate = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        review_revision(candidate.pk, self.admin, approve=True)
        document.refresh_from_db()
        self.assertFalse(document.ai_reprocessing_required)
        self.assertEqual(document.status, 'ACTIVE')
        self.assertEqual(document.source_url, updated_link.source_url)

    def test_changed_body_preserves_approved_body_hash_date_and_chunks_until_approval(self):
        document = self.approve_initial()
        old_hash, old_date = document.content_hash, document.collected_at
        chunk = SourceChunk.objects.create(source_document=document, chunk_text='기존 청크', chunk_order=0, embedding=[0.1], source_content_hash=old_hash)
        changed = replace(self.record, content_text='수정된 지원대상과 신청조건 원문입니다.')
        self.assertEqual(self.refresh(changed).outcome, 'candidate')
        document.refresh_from_db()
        self.assertEqual((document.content_text, document.content_hash, document.collected_at), (self.record.content_text, old_hash, old_date))
        candidate = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        self.assertEqual(candidate.previous_content_text, self.record.content_text)
        self.assertEqual(candidate.previous_hash, old_hash)
        review_revision(candidate.pk, self.admin, approve=True)
        document.refresh_from_db()
        chunk.refresh_from_db()
        self.assertEqual(document.content_hash, changed.content_hash)
        self.assertEqual(chunk.embedding, [0.1])
        self.assertEqual(chunk.chunk_text, '기존 청크')
        self.assertFalse(chunk.is_current)
        self.assertEqual(document.status, 'PENDING')

    def test_failure_preserves_all_approved_fields_and_last_success(self):
        document = self.approve_initial()
        snapshot = SourceDocument.objects.values().get(pk=document.pk)
        self.target.refresh_from_db()
        last_success = self.target.last_success_at
        self.client_stub.fetch.side_effect = CollectionError('NETWORK_FAILED', '제한 시간 초과')
        self.assertEqual(self.refresh().outcome, 'failed')
        self.assertEqual(SourceDocument.objects.values().get(pk=document.pk), snapshot)
        self.target.refresh_from_db()
        self.assertEqual(self.target.last_success_at, last_success)
        self.assertEqual(self.target.failures, 1)
        self.assertIsNotNone(self.target.retry_after)
        self.assertIsNone(self.target.lease_token)
        self.assertEqual(CrawlLog.objects.order_by('-id').first().error_code, 'NETWORK_FAILED')

    def test_unexpected_error_message_cannot_leak_credentials(self):
        self.client_stub.fetch.side_effect = RuntimeError('secret-key-in-request')
        result = self.refresh()
        self.assertEqual(result.outcome, 'failed')
        self.assertNotIn('secret-key-in-request', result.message)
        self.assertNotIn('secret-key-in-request', CrawlLog.objects.get().error_message)

    def test_initial_reject_keeps_body_empty_and_allows_later_collection(self):
        self.refresh()
        review_revision(SourceRevision.objects.get().pk, self.admin, approve=False)
        document = SourceDocument.objects.get()
        self.assertEqual(document.review_status, 'REJECTED')
        self.assertEqual(document.content_text, '')
        self.refresh()
        self.assertEqual(SourceRevision.objects.filter(review_status='PENDING_REVIEW').count(), 1)
        self.assertEqual(SourceDocument.objects.count(), 1)

    def test_changed_rejection_preserves_approved_document_and_reviewer_history(self):
        document = self.approve_initial()
        self.refresh(replace(self.record, content_text='변경 후보'))
        candidate = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        review_revision(candidate.pk, self.admin, approve=False)
        document.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual(document.content_text, self.record.content_text)
        self.assertEqual(document.review_status, 'APPROVED')
        self.assertEqual(candidate.review_status, 'REJECTED')
        self.assertEqual(candidate.reviewed_by_id, self.admin.pk)
        self.assertIsNotNone(candidate.reviewed_at)

    def test_old_candidate_cannot_override_latest_observation_or_new_approval(self):
        self.approve_initial()
        self.refresh(replace(self.record, content_text='변경 A'))
        candidate_a = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        self.refresh(replace(self.record, content_text='변경 B'))
        candidate_b = SourceRevision.objects.filter(review_status='PENDING_REVIEW').exclude(pk=candidate_a.pk).get()
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(candidate_a.pk, self.admin, approve=True)
        review_revision(candidate_b.pk, self.admin, approve=True)
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(candidate_a.pk, self.admin, approve=True)
        self.assertEqual(SourceDocument.objects.get().content_text, '변경 B')

    def test_candidate_reverted_upstream_cannot_be_approved(self):
        self.approve_initial()
        self.refresh(replace(self.record, content_text='일시적 변경'))
        candidate = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        self.refresh()
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(candidate.pk, self.admin, approve=True)

    def test_admin_metadata_edit_conflicts_with_candidate(self):
        document = self.approve_initial()
        self.refresh(replace(self.record, content_text='변경'))
        candidate = SourceRevision.objects.get(review_status='PENDING_REVIEW')
        SourceDocument.objects.filter(pk=document.pk).update(version=document.version + 1, title='관리자 수정')
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(candidate.pk, self.admin, approve=True)

    def test_regular_user_cannot_review_and_double_review_rejected(self):
        self.refresh()
        revision = SourceRevision.objects.get()
        with self.assertRaisesMessage(CollectionError, 'PERMISSION_DENIED'):
            review_revision(revision.pk, self.user, approve=True)
        review_revision(revision.pk, self.admin, approve=True)
        with self.assertRaisesMessage(CollectionError, 'REVIEW_CONFLICT'):
            review_revision(revision.pk, self.admin, approve=True)

    def test_inactive_document_not_reactivated_by_collection_or_review(self):
        document = self.approve_initial()
        SourceDocument.objects.filter(pk=document.pk).update(status='INACTIVE')
        self.refresh(replace(self.record, content_text='변경'))
        review_revision(SourceRevision.objects.get(review_status='PENDING_REVIEW').pk, self.admin, approve=True)
        self.assertEqual(SourceDocument.objects.get().status, 'INACTIVE')

    def test_target_and_document_external_identity_database_constraints(self):
        self.refresh()
        for model, fields in ((SourceRefreshTarget, {'provider': 'public_data', 'external_id': 'service-1'}), (SourceDocument, {'provider': 'public_data', 'external_id': 'service-1', 'source_url': None})):
            with self.subTest(model=model), self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**fields)

    def test_running_lease_prevents_duplicate_request_and_log(self):
        token, log = claim(self.target)
        self.assertIsNotNone(token)
        result = self.refresh()
        self.assertEqual(result.outcome, 'skipped')
        self.client_stub.fetch.assert_not_called()
        self.assertEqual(CrawlLog.objects.count(), 1)
        self.assertEqual(CrawlLog.objects.get(pk=log.pk).result, 'RUNNING')

    def test_expired_lease_recovers_and_finishes_abandoned_log(self):
        _, log = claim(self.target)
        SourceRefreshTarget.objects.filter(pk=self.target.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.refresh().outcome, 'new')
        log.refresh_from_db()
        self.assertEqual(log.error_code, 'LEASE_EXPIRED')
        self.assertEqual(log.result, 'FAILED')

    def test_lost_lease_cannot_write_data_or_release_new_owners_lease(self):
        replacement = uuid.uuid4()
        def fetch(target):
            SourceRefreshTarget.objects.filter(pk=target.pk).update(lease_token=replacement)
            return self.record
        self.client_stub.fetch.side_effect = fetch
        self.assertEqual(self.refresh().outcome, 'failed')
        self.assertFalse(SourceDocument.objects.exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.lease_token, replacement)

    def test_older_result_cannot_replace_newer_observation(self):
        document = self.approve_initial()
        current_start = document.observed_started_at
        with transaction.atomic():
            outcome, _ = _apply_observation(self.target, replace(self.record, content_text='고립된 이전 응답'), current_start - timedelta(minutes=1), timezone.now())
        self.assertEqual(outcome, 'skipped')
        self.assertEqual(SourceRevision.objects.count(), 1)

    def test_dry_run_has_no_database_changes_including_logs_and_ai(self):
        self.approve_initial()
        models = (SourceDocument, SourceRefreshTarget, SourceRevision, CrawlLog, SourceChunk)
        before = [list(model.objects.values()) for model in models]
        self.assertEqual(self.refresh(replace(self.record, content_text='저장하지 않을 변경'), dry_run=True).outcome, 'candidate')
        self.assertEqual([list(model.objects.values()) for model in models], before)
        self.client_stub.fetch.assert_called()

    def test_failed_run_can_retry_safely_and_clears_failure_state(self):
        self.client_stub.fetch.side_effect = CollectionError('NETWORK_FAILED', '실패')
        self.refresh()
        self.client_stub.fetch.side_effect = None
        self.assertEqual(self.refresh().outcome, 'new')
        self.target.refresh_from_db()
        self.assertEqual(self.target.failures, 0)
        self.assertIsNone(self.target.retry_after)

    def test_disabled_target_skips_and_changed_identity_is_rejected(self):
        self.target.enabled = False
        self.assertEqual(refresh_target(self.target, self.client_stub).outcome, 'skipped')
        self.client_stub.fetch.assert_not_called()
        self.target.enabled = True
        self.assertEqual(self.refresh(replace(self.record, external_id='wrong')).outcome, 'failed')
        self.assertFalse(SourceDocument.objects.exists())

    def test_legacy_html_command_uses_review_workflow_for_managed_targets(self):
        html_spec = spec()
        target = SourceRefreshTarget.objects.create(provider='html', source_url=html_spec.url, configuration={key: getattr(html_spec, key) for key in ('title', 'agency', 'region', 'category')})
        result = collect_source(html_spec, collector())
        self.assertEqual(result.outcome, 'new')
        target.refresh_from_db()
        self.assertEqual(target.document.content_text, '')
        self.assertIn('8월 31일', target.document.revisions.get().content_text)

    def test_legacy_writer_admitted_before_registration_cannot_overwrite_approved_body(self):
        html_spec = spec()
        original_create = CrawlLog.objects.create
        registered = False

        def create_log(**kwargs):
            nonlocal registered
            if not registered and 'target' not in kwargs:
                # 이전 수집기의 사전 조회 직후 등록·수집·승인이 끝나고,
                # 이전 수집기의 시작 로그는 그보다 나중에 생기는 순서를 재현한다.
                registered = True
                target = SourceRefreshTarget.objects.create(provider='html', source_url=html_spec.url, configuration={key: getattr(html_spec, key) for key in ('title', 'agency', 'region', 'category')})
                approved_client = collector()
                response = approved_client.fetch.return_value
                from sources.services.collector import HTMLResponse
                approved_client.fetch.return_value = HTMLResponse(response.body.replace('8월 31일'.encode(), '9월 30일'.encode()), response.content_type, response.url)
                self.assertEqual(refresh_target(target, approved_client).outcome, 'new')
                revision = SourceRevision.objects.get(provider='html')
                review_revision(revision.pk, self.admin, approve=True)
            return original_create(**kwargs)

        with patch('sources.services.source_service.CrawlLog.objects.create', side_effect=create_log):
            result = collect_source(html_spec, collector())
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('MANAGED_SOURCE', result.message)
        document = SourceDocument.objects.get(provider='html')
        self.assertIn('9월 30일', document.content_text)
        self.assertEqual(document.reviewed_by_id, self.admin.pk)
        self.assertEqual(document.review_status, 'APPROVED')
        self.assertEqual(document.status, 'PENDING')
        self.assertTrue(document.ai_reprocessing_required)
        self.assertEqual(SourceRevision.objects.filter(document=document).count(), 1)

    def test_old_chunks_require_matching_hash_and_processing_acknowledgment(self):
        document = self.approve_initial()
        chunk = SourceChunk.objects.create(source_document=document, chunk_order=0, chunk_text='원문', embedding=[1])
        self.assertFalse(chunk.is_current)
        chunk.source_content_hash = document.content_hash
        self.assertFalse(chunk.is_current)
        SourceDocument.objects.filter(pk=document.pk).update(status='ACTIVE', ai_reprocessing_required=False, source_url='https://www.gov.kr/official-test-fixture')
        chunk.source_document.refresh_from_db()
        self.assertTrue(chunk.is_current)

    def test_target_validation_rejects_bad_urls_and_missing_api_ids(self):
        for target in (SourceRefreshTarget(provider='html', source_url='https://127.0.0.1/a'), SourceRefreshTarget(provider='law'), SourceRefreshTarget(provider='public_data', external_id='x', source_url='https://www.gov.kr/?serviceKey=secret')):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                target.full_clean()


class OfficialFreshnessTests(TestCase):
    @override_settings(OFFICIAL_SOURCE_INTERVAL_HOURS={'html': 2})
    def test_unregistered_document_uses_same_configured_period_as_database_filter(self):
        document = SourceDocument.objects.create(
            source_url='https://www.asan.go.kr/test-fixtures/without-target', title='기존 안내',
            agency='아산시', region='아산시', category='지방세', review_status='APPROVED',
            collected_at=timezone.now() - timedelta(hours=3),
        )
        self.assertEqual(freshness(document), 'STALE')
        self.assertEqual(list(filter_freshness(SourceDocument.objects.all(), 'STALE').values_list('pk', flat=True)), [document.pk])

    def test_different_type_periods_and_per_target_override(self):
        checked = timezone.now()
        intervals = []
        for provider in ('law', 'public_data', 'html'):
            target = SourceRefreshTarget(provider=provider, last_success_at=checked)
            intervals.append(next_due_at(target) - checked)
        self.assertEqual(len(set(intervals)), 3)
        with override_settings(OFFICIAL_SOURCE_INTERVAL_HOURS={'law': 2}):
            target = SourceRefreshTarget(provider='law', last_success_at=checked)
            self.assertEqual(next_due_at(target), checked + timedelta(hours=2))
            target.interval_hours = 5
            self.assertEqual(next_due_at(target), checked + timedelta(hours=5))

    def test_due_without_confirmation_disabled_running_and_failure_backoff(self):
        now = timezone.now()
        target = SourceRefreshTarget(provider='law')
        self.assertTrue(is_due(target, now))
        target.enabled = False
        self.assertFalse(is_due(target, now))
        target.enabled = True
        target.lease_expires_at = now + timedelta(minutes=1)
        self.assertFalse(is_due(target, now))
        target.lease_expires_at = None
        target.failures, target.retry_after = 1, now + timedelta(hours=1)
        self.assertFalse(is_due(target, now))
        self.assertTrue(is_due(target, now + timedelta(hours=2)))

    def test_freshness_and_database_filter_agree_for_all_states(self):
        now = timezone.now()
        documents = {}
        for index, state in enumerate(('CURRENT', 'STALE', 'REFRESH_FAILED', 'REVIEW_REQUIRED')):
            checked = now - timedelta(hours=10 if state == 'STALE' else 1)
            document = SourceDocument.objects.create(provider='law', external_id=str(index), review_status='PENDING_REVIEW' if state == 'REVIEW_REQUIRED' else 'APPROVED', last_success_at=checked)
            SourceRefreshTarget.objects.create(provider='law', external_id=str(index), document=document, last_success_at=checked, interval_hours=2, failures=1 if state == 'REFRESH_FAILED' else 0)
            documents[state] = document.pk
            self.assertEqual(freshness(document, now), state)
            self.assertEqual(freshness_details(document, now)['legal_effect'], '확인 필요')
        for state, pk in documents.items():
            with self.subTest(state=state):
                self.assertEqual(list(filter_freshness(SourceDocument.objects.all(), state, now).values_list('pk', flat=True)), [pk])
