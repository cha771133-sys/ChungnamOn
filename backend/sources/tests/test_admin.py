from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from sources.models import CrawlLog, SourceChunk, SourceDocument

from .helpers import create_document


class SourcesAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.superuser = User.objects.create_superuser(
            **{User.USERNAME_FIELD: 'admin@example.com'}, password='test-password',
        )
        cls.staff = User.objects.create_user(
            **{User.USERNAME_FIELD: 'staff@example.com'}, password='test-password', is_staff=True,
        )
        cls.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='sources'))
        cls.unprivileged_staff = User.objects.create_user(
            **{User.USERNAME_FIELD: 'staff-no-permission@example.com'},
            password='test-password', is_staff=True,
        )
        cls.user = User.objects.create_user(
            **{User.USERNAME_FIELD: 'user@example.com'}, password='test-password',
        )
        cls.document = create_document(content_hash='a' * 64)
        cls.other_document = create_document(
            source_url='https://www.cheonan.go.kr/notices/2', title='복지 신청 안내',
            agency='천안시청', region='천안시', category='복지', status=SourceDocument.Status.INACTIVE,
        )
        cls.chunk = SourceChunk.objects.create(
            source_document=cls.document, chunk_text='주민세 납부 방법', chunk_order=0,
            embedding=[0.123456789],
        )
        cls.other_chunk = SourceChunk.objects.create(
            source_document=cls.other_document, chunk_text='복지 신청 서류', chunk_order=0,
        )
        cls.log = CrawlLog.objects.create(
            source_url=cls.document.source_url, result=CrawlLog.Result.FAILED,
            error_message='연결 시간 초과',
        )
        cls.other_log = CrawlLog.objects.create(
            source_url=cls.other_document.source_url, result=CrawlLog.Result.SUCCESS,
        )
        cls.objects = (cls.document, cls.chunk, cls.log)

    def url(self, obj, action='changelist'):
        name = f'admin:sources_{obj._meta.model_name}_{action}'
        return reverse(name, args=[obj.pk] if action in ('change', 'delete', 'history') else None)

    def assert_result_ids(self, obj, params, expected):
        response = self.client.get(self.url(obj), params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['cl'].result_list.values_list('pk', flat=True)), set(expected))

    def test_three_models_registered(self):
        for model in (SourceDocument, SourceChunk, CrawlLog):
            with self.subTest(model=model):
                self.assertTrue(admin.site.is_registered(model))

    def test_staff_with_permissions_can_view_lists_and_details(self):
        self.client.force_login(self.staff)
        for obj in self.objects:
            for action in ('changelist', 'change'):
                with self.subTest(model=type(obj).__name__, action=action):
                    self.assertEqual(self.client.get(self.url(obj, action)).status_code, 200)

    def test_anonymous_is_redirected_to_login(self):
        for obj in self.objects:
            with self.subTest(model=type(obj).__name__):
                response = self.client.get(self.url(obj))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('admin:login'), response.url)

    def test_regular_user_cannot_access_admin_even_with_model_permissions(self):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='sources'))
        self.client.force_login(self.user)
        for obj in self.objects:
            with self.subTest(model=type(obj).__name__):
                self.assertEqual(self.client.get(self.url(obj)).status_code, 302)
                self.assertEqual(self.client.get(self.url(obj, 'change')).status_code, 302)

    def test_staff_without_model_permissions_is_forbidden(self):
        self.client.force_login(self.unprivileged_staff)
        for obj in self.objects:
            with self.subTest(model=type(obj).__name__):
                self.assertEqual(self.client.get(self.url(obj)).status_code, 403)
                self.assertEqual(self.client.get(self.url(obj, 'change')).status_code, 403)

    def test_document_search_and_all_filters(self):
        self.client.force_login(self.staff)
        for query in ('주민세', 'asan.go.kr', '아산시청'):
            with self.subTest(query=query):
                self.assert_result_ids(self.document, {'q': query}, [self.document.pk])
        for field, value in (
            ('agency', '아산시청'), ('region', '아산시'), ('category', '지방세'),
            ('status', SourceDocument.Status.PENDING),
        ):
            with self.subTest(field=field):
                self.assert_result_ids(self.document, {f'{field}__exact': value}, [self.document.pk])
        response = self.client.get(self.url(self.document))
        self.assertContains(response, '공식자료 확인일')

    def test_document_detail_shows_related_chunks_and_protects_pipeline_fields(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url(self.document, 'change'))
        self.assertContains(response, self.chunk.chunk_text)
        self.assertContains(response, self.url(self.chunk, 'change'))
        form_fields = response.context['adminform'].form.fields
        for field in ('source_url', 'content_hash', 'collected_at', 'content_text'):
            self.assertNotIn(field, form_fields)
        inlines = response.context['inline_admin_formsets']
        self.assertEqual(len(inlines), 1)
        self.assertFalse(inlines[0].has_add_permission)
        self.assertFalse(inlines[0].has_change_permission)
        self.assertFalse(inlines[0].has_delete_permission)

    def test_document_metadata_edit_keeps_url_hash_and_collection_time(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url(self.document, 'change'), {
            'title': '수정된 공식자료 제목', 'agency': self.document.agency,
            'region': self.document.region, 'category': self.document.category,
            'status': SourceDocument.Status.INACTIVE, 'published_at': '',
            'expected_version': self.document.version,
            'source_url': 'https://example.com/tampered', 'content_hash': '변조',
            'collected_at': '2026-01-01 00:00:00',
            'chunks-TOTAL_FORMS': '1', 'chunks-INITIAL_FORMS': '1',
            'chunks-MIN_NUM_FORMS': '0', 'chunks-MAX_NUM_FORMS': '0',
        })
        self.assertEqual(response.status_code, 302)
        document = SourceDocument.objects.get(pk=self.document.pk)
        self.assertEqual(document.title, '수정된 공식자료 제목')
        self.assertEqual(document.status, SourceDocument.Status.INACTIVE)
        self.assertEqual(document.source_url, self.document.source_url)
        self.assertEqual(document.content_hash, 'a' * 64)
        self.assertIsNone(document.collected_at)
        self.assertEqual(document.chunks.get().pk, self.chunk.pk)

    def test_staff_can_register_uncollected_document(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url(self.document, 'add'), {
            'source_url': 'HTTPS://WWW.ASAN.GO.KR:443/notices/new#intro',
            'title': '확인 대기 자료', 'agency': '아산시청', 'region': '아산시', 'category': '지방세',
            'status': SourceDocument.Status.PENDING, 'published_at': '',
            'chunks-TOTAL_FORMS': '0', 'chunks-INITIAL_FORMS': '0',
            'chunks-MIN_NUM_FORMS': '0', 'chunks-MAX_NUM_FORMS': '0',
        })
        self.assertEqual(response.status_code, 302)
        document = SourceDocument.objects.get(source_url='https://www.asan.go.kr/notices/new')
        self.assertIsNone(document.collected_at)
        self.assertIsNone(document.published_at)
        self.assertEqual(document.content_hash, '')
        self.assertFalse(document.chunks.exists())

    def test_chunk_search_filter_and_no_embedding_in_list(self):
        self.client.force_login(self.staff)
        self.assert_result_ids(self.chunk, {'q': '납부 방법'}, [self.chunk.pk])
        self.assert_result_ids(
            self.chunk, {'source_document__id__exact': self.document.pk}, [self.chunk.pk],
        )
        response = self.client.get(self.url(self.chunk))
        self.assertNotContains(response, '0.123456789')

    def test_log_search_filter_and_error_detail(self):
        self.client.force_login(self.staff)
        self.assert_result_ids(self.log, {'q': 'asan.go.kr'}, [self.log.pk])
        self.assert_result_ids(self.log, {'result__exact': CrawlLog.Result.FAILED}, [self.log.pk])
        response = self.client.get(self.url(self.log, 'change'))
        self.assertContains(response, '연결 시간 초과')

    def test_pipeline_models_are_read_only_even_for_superuser(self):
        self.client.force_login(self.superuser)
        for obj in (self.chunk, self.log):
            with self.subTest(model=type(obj).__name__):
                self.assertEqual(self.client.get(self.url(obj, 'add')).status_code, 403)
                self.assertEqual(self.client.post(self.url(obj, 'change'), {}).status_code, 403)
                self.assertEqual(self.client.post(self.url(obj, 'delete'), {'post': 'yes'}).status_code, 403)
                response = self.client.get(self.url(obj, 'change'))
                self.assertNotContains(response, 'name="_save"')
                self.assertFalse(response.context['adminform'].form.fields)
        self.assertTrue(SourceChunk.objects.filter(pk=self.chunk.pk).exists())
        self.assertTrue(CrawlLog.objects.filter(pk=self.log.pk).exists())

    def test_document_delete_disabled_and_no_bulk_delete(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url(self.document, 'delete'), {'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        for obj in self.objects:
            with self.subTest(model=type(obj).__name__):
                response = self.client.get(self.url(obj))
                self.assertNotContains(response, 'value="delete_selected"')

    def test_view_only_staff_can_read_but_cannot_edit_metadata(self):
        self.unprivileged_staff.user_permissions.add(
            *Permission.objects.filter(content_type__app_label='sources', codename__startswith='view_'),
        )
        self.client.force_login(self.unprivileged_staff)
        for obj in self.objects:
            with self.subTest(model=type(obj).__name__):
                self.assertEqual(self.client.get(self.url(obj, 'change')).status_code, 200)
                self.assertEqual(self.client.post(self.url(obj, 'change'), {}).status_code, 403)
