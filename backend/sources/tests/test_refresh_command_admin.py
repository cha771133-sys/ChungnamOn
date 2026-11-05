from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from types import SimpleNamespace
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sources.management.commands.refresh_official_sources import CLIENTS
from sources.models import CrawlLog, SourceDocument, SourceRefreshTarget, SourceRevision
from sources.services.errors import CollectionError
from sources.services.refresh_service import refresh_target

from .pipeline_helpers import spec
from .test_official_refresh import make_record


class RefreshCommandTests(TestCase):
    def setUp(self):
        self.output, self.errors = StringIO(), StringIO()
        self.public = Mock(fetch=Mock(return_value=make_record()))
        self.law = Mock(fetch=Mock(return_value=make_record(provider='law', external_id='law-1', category='자치법규')))
        self.factory = patch.dict(CLIENTS, {'public_data': lambda: self.public, 'law': lambda: self.law})
        self.factory.start()
        self.addCleanup(self.factory.stop)
        registry = patch('sources.management.commands.refresh_official_sources.load_registry', return_value=[])
        registry.start()
        self.addCleanup(registry.stop)
        self.public_target = SourceRefreshTarget.objects.create(provider='public_data', external_id='service-1')
        self.law_target = SourceRefreshTarget.objects.create(provider='law', external_id='law-1', configuration={'collection_region': '아산시'})

    def command(self, *args):
        return call_command('refresh_official_sources', *args, stdout=self.output, stderr=self.errors)

    def test_all_runs_multiple_providers_and_closes_clients(self):
        self.command('--provider', 'all')
        self.assertEqual(SourceDocument.objects.count(), 2)
        self.assertEqual(CrawlLog.objects.filter(result='SUCCESS').count(), 2)
        self.public.close.assert_called_once()
        self.law.close.assert_called_once()
        self.assertIn('new=2', self.output.getvalue())
        self.assertIn('연결하지 않음', self.output.getvalue())

    def test_call_command_preserves_callers_existing_database_transaction(self):
        with patch.object(connections['default'], 'close') as close:
            self.command('--provider', 'public_data')
            close.assert_not_called()
        self.assertEqual(SourceDocument.objects.count(), 1)

    def test_provider_filter_and_limit_bound_requests(self):
        self.command('--provider', 'all', '--limit', '1')
        self.assertEqual(SourceDocument.objects.count(), 1)
        self.public.fetch.assert_called_once()
        self.law.fetch.assert_not_called()

    def test_due_only_and_disabled_targets_skip_without_external_call(self):
        SourceRefreshTarget.objects.filter(pk=self.public_target.pk).update(last_success_at=timezone.now())
        SourceRefreshTarget.objects.filter(pk=self.law_target.pk).update(enabled=False)
        self.command('--due-only')
        self.assertEqual(CrawlLog.objects.count(), 0)
        self.public.fetch.assert_not_called()
        self.law.fetch.assert_not_called()

    def test_due_only_failure_retries_after_backoff(self):
        SourceRefreshTarget.objects.filter(pk=self.public_target.pk).update(failures=1, retry_after=timezone.now() - timedelta(seconds=1))
        self.command('--provider', 'public_data', '--due-only')
        self.public.fetch.assert_called_once()

    def test_dry_run_does_not_register_or_update_any_database_rows(self):
        models = (SourceRefreshTarget, SourceDocument, SourceRevision, CrawlLog)
        before = [list(model.objects.values()) for model in models]
        self.command('--dry-run')
        self.assertEqual([list(model.objects.values()) for model in models], before)
        self.public.fetch.assert_called_once()
        self.assertIn('실제 외부 API/HTML', self.output.getvalue())

    def test_partial_failure_continues_other_provider_with_nonzero_exit(self):
        self.public.fetch.side_effect = CollectionError('NETWORK_FAILED', '일시적 장애')
        with self.assertRaisesMessage(CommandError, '부분 수집 실패'):
            self.command('--provider', 'all')
        self.law.fetch.assert_called_once()
        self.assertEqual(SourceDocument.objects.get().provider, 'law')
        self.assertEqual(CrawlLog.objects.filter(result='FAILED').count(), 1)
        self.assertIn('failed=1', self.output.getvalue())

    def test_all_failure_is_distinct_from_partial_failure(self):
        self.public.fetch.side_effect = CollectionError('AUTH_FAILED', '인증 오류')
        with self.assertRaisesMessage(CommandError, '전체 수집 실패'):
            self.command('--provider', 'public_data')

    def test_missing_credentials_logged_without_blocking_other_provider(self):
        def missing():
            raise CollectionError('AUTH_MISSING', '인증정보 설정이 필요합니다.')
        with patch.dict(CLIENTS, {'public_data': missing}), self.assertRaisesMessage(CommandError, '부분 수집 실패'):
            self.command()
        self.law.fetch.assert_called_once()
        log = CrawlLog.objects.get(provider='public_data')
        self.assertEqual(log.error_code, 'AUTH_MISSING')
        self.assertNotIn('OC=', log.source_url)

    def test_invalid_options_rejected_before_http_and_database_changes(self):
        for args in (('--limit', '0'), ('--limit', '1001'), ('--discover',), ('--discover', '--provider', 'law', '--query', '주민', '--region', '전국')):
            with self.subTest(args=args), self.assertRaises(CommandError):
                self.command(*args)
        self.public.fetch.assert_not_called()
        self.assertEqual(CrawlLog.objects.count(), 0)

    def test_discovery_deduplicates_ids_and_does_not_invent_source_url(self):
        self.public.list_services.return_value = [{'서비스ID': 'service-1'}, {'서비스ID': 'service-1'}]
        self.command('--provider', 'public_data', '--discover', '--query', '주민', '--region', '아산시')
        self.public.fetch.assert_called_once()
        self.assertEqual(SourceRefreshTarget.objects.filter(provider='public_data').count(), 1)
        self.assertIsNone(SourceDocument.objects.get().source_url)

    def test_discovery_dry_run_creates_nothing(self):
        self.public.list_services.return_value = [{'서비스ID': 'service-2'}]
        self.public.fetch.return_value = make_record(external_id='service-2')
        self.command('--provider', 'public_data', '--discover', '--query', '주민', '--dry-run')
        self.assertFalse(SourceRefreshTarget.objects.filter(external_id='service-2').exists())
        self.assertFalse(SourceDocument.objects.exists())
        self.assertFalse(CrawlLog.objects.exists())

    def test_unusable_discovery_link_does_not_stop_remaining_rows(self):
        self.public.list_services.return_value = [
            {'서비스ID': 'service-1', '상세조회URL': 'https://www.gov.kr/detail?serviceKey=private-key'},
            {'서비스ID': 'service-2', '상세조회URL': 'https://www.gov.kr/test-fixtures/normal'},
        ]
        self.public.fetch.side_effect = lambda target: make_record(external_id=target.external_id)
        self.command('--provider', 'public_data', '--discover', '--query', '주민')
        self.assertEqual(SourceDocument.objects.count(), 2)
        self.assertEqual(self.public.fetch.call_count, 2)
        self.assertNotIn('private-key', self.output.getvalue())
        self.assertIsNone(SourceRefreshTarget.objects.get(external_id='service-1').source_url)

    def test_legacy_registry_disabled_entries_are_not_registered(self):
        with patch('sources.management.commands.refresh_official_sources.load_registry', return_value=[spec(enabled=False)]):
            self.command('--provider', 'html')
        self.assertFalse(SourceRefreshTarget.objects.filter(provider='html').exists())


class RefreshAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(email='official-admin@example.com', password='test-password')
        cls.user = get_user_model().objects.create_user(email='official-user@example.com', password='test-password')
        cls.viewer = get_user_model().objects.create_user(email='official-viewer@example.com', password='test-password', is_staff=True)
        cls.viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='sources', codename__startswith='view_'))

    def setUp(self):
        target = SourceRefreshTarget.objects.create(provider='public_data', external_id='service-1')
        refresh_target(target, Mock(fetch=Mock(return_value=make_record())))
        self.revision = SourceRevision.objects.get()
        self.url = reverse('admin:sources_sourcerevision_changelist')

    def action(self, action):
        return self.client.post(self.url, {'action': action, '_selected_action': [str(self.revision.pk)], 'index': '0'})

    def test_admin_approve_uses_real_request_user_and_records_admin_history(self):
        self.client.force_login(self.admin)
        response = self.action('approve_candidates')
        self.assertEqual(response.status_code, 302)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.reviewed_by_id, self.admin.pk)
        self.assertEqual(self.revision.review_status, 'APPROVED')
        from django.contrib.admin.models import LogEntry
        self.assertTrue(LogEntry.objects.filter(user=self.admin, object_id=str(self.revision.pk)).exists())

    def test_admin_reject_preserves_candidate_and_records_reviewer(self):
        self.client.force_login(self.admin)
        self.action('reject_candidates')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.review_status, 'REJECTED')
        self.assertEqual(self.revision.reviewed_by_id, self.admin.pk)
        self.assertEqual(self.revision.document.content_text, '')

    def test_regular_user_with_permissions_is_blocked_from_new_admin_models(self):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='sources'))
        self.client.force_login(self.user)
        for name in ('sourcerevision', 'sourcerefreshtarget'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f'admin:sources_{name}_changelist')).status_code, 302)
        self.action('approve_candidates')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.review_status, 'PENDING_REVIEW')

    def test_view_only_staff_cannot_invoke_review_actions(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="approve_candidates"')
        self.action('approve_candidates')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.review_status, 'PENDING_REVIEW')

    def test_review_and_stale_filters_are_available(self):
        self.client.force_login(self.admin)
        url = reverse('admin:sources_sourcedocument_changelist')
        response = self.client.get(url, {'freshness': 'REVIEW_REQUIRED'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cl'].queryset.count(), 1)
        self.assertEqual(self.client.get(url, {'freshness': 'STALE'}).context['cl'].queryset.count(), 0)

    def test_failed_logs_display_external_id_error_and_existing_document(self):
        target = SourceRefreshTarget.objects.get()
        refresh_target(target, Mock(fetch=Mock(side_effect=CollectionError('NETWORK_FAILED', '시간 초과'))))
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin:sources_crawllog_changelist'), {'result__exact': 'FAILED'})
        self.assertContains(response, 'service-1')
        self.assertContains(response, 'NETWORK_FAILED')
        self.assertEqual(response.context['cl'].queryset.count(), 1)

    def test_revisions_are_immutable_even_for_superuser(self):
        self.client.force_login(self.admin)
        for action, data in (('add', {}), ('change', {}), ('delete', {'post': 'yes'})):
            url = reverse(f'admin:sources_sourcerevision_{action}', args=[self.revision.pk] if action != 'add' else None)
            self.assertEqual(self.client.post(url, data).status_code, 403)

    def test_editing_target_interval_cannot_overwrite_concurrent_running_lease(self):
        from django.contrib import admin
        target = SourceRefreshTarget.objects.get()
        target.interval_hours = 2
        token = uuid.uuid4()
        SourceRefreshTarget.objects.filter(pk=target.pk).update(lease_token=token, lease_expires_at=timezone.now() + timedelta(minutes=30))
        model_admin = admin.site._registry[SourceRefreshTarget]
        model_admin.save_model(SimpleNamespace(user=self.admin), target, SimpleNamespace(changed_data=['interval_hours']), True)
        target.refresh_from_db()
        self.assertEqual(target.interval_hours, 2)
        self.assertEqual(target.lease_token, token)
