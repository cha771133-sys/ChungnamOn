from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from analytics.models import KPIEvent

from .helpers import AnalyticsFixtures


class AdminTests(AnalyticsFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        """조회 권한 유무와 superuser 여부가 다른 관리자를 준비한다."""
        super().setUpTestData()
        cls.staff = get_user_model().objects.create_user('kpi-staff@example.com', is_staff=True)
        cls.staff.user_permissions.add(Permission.objects.get(codename='view_kpievent'))
        cls.unprivileged = get_user_model().objects.create_user('no-kpi@example.com', is_staff=True)
        cls.superuser = get_user_model().objects.create_superuser('kpi-admin@example.com', password=None)

    def test_summary_requires_staff_and_explicit_view_permission(self):
        """비인증·일반 사용자·무권한 staff를 차단하고 허용된 관리자만 통과시킨다."""
        summary_url = reverse('admin:analytics_kpi_summary')
        self.assertEqual(self.client.get(summary_url).status_code, 302)
        for user, expected in ((self.user, 302), (self.unprivileged, 403), (self.staff, 200), (self.superuser, 200)):
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                self.assertEqual(self.client.get(summary_url).status_code, expected)

    def test_dashboard_periods_languages_and_invalid_inputs(self):
        """지원 기간·언어를 렌더링하고 잘못된 필터는 400으로 거부한다."""
        self.client.force_login(self.staff)
        url = reverse('admin:analytics_kpi_summary')
        for params in ({}, {'days': 7}, {'days': 30, 'language': 'vi'}, {'days': 7, 'language': 'unknown'}):
            response = self.client.get(url, params)
            self.assertContains(response, 'NOT_MEASURABLE')
            self.assertContains(response, 'N/A')
        for params in ({'days': 365}, {'days': 'x'}, {'days': 7, 'language': 'x'}):
            self.assertEqual(self.client.get(url, params).status_code, 400)

    def test_log_filters_search_and_read_only_without_document_content(self):
        """로그 검색·필터·읽기 전용 권한과 문서 내용 비노출을 확인한다."""
        document = self.document(title='민감 문서 제목', fail_reason='민감 오류')
        event = self.event(KPIEvent.Type.ANALYSIS_STARTED, document, language='vi')
        self.client.force_login(self.staff)
        url = reverse('admin:analytics_kpievent_changelist')
        response = self.client.get(url, {'event_type__exact': event.event_type, 'language__exact': 'vi', 'q': str(document.pk)})
        self.assertContains(response, 'KPI 요약')
        self.assertEqual(response.context['cl'].result_count, 1)
        detail = reverse('admin:analytics_kpievent_change', args=[event.pk])
        self.assertNotContains(self.client.get(detail), '민감 문서 제목')
        self.assertNotContains(self.client.get(detail), '민감 오류')
        self.assertEqual(self.client.post(detail, {'language': 'en'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:analytics_kpievent_add')).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:analytics_kpievent_delete', args=[event.pk]), {'post': 'yes'}).status_code, 403)
        self.client.force_login(self.unprivileged)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(detail).status_code, 403)

    def test_existing_parent_admin_delete_is_not_blocked_by_event_permissions(self):
        """문서 삭제 권한만으로 부모와 이벤트를 함께 삭제할 수 있음을 확인한다."""
        document = self.document()
        self.event(KPIEvent.Type.ANALYSIS_STARTED, document)
        self.staff.user_permissions.add(Permission.objects.get(codename='delete_document'))
        self.client.force_login(self.staff)
        url = reverse('admin:documents_document_delete', args=[document.pk])
        response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KPIEvent.objects.exists())
