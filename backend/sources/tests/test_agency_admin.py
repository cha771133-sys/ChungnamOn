from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sources.models import Agency
from .agency_helpers import create_agency


class AgencyAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.superuser = User.objects.create_superuser(email='agency-admin@example.com', password='test-password')
        cls.user = User.objects.create_user(email='agency-user@example.com', password='test-password')
        cls.staff = User.objects.create_user(email='agency-staff@example.com', password='test-password', is_staff=True)
        cls.agency = create_agency(department='가상부서')

    def test_registered_and_admin_displays_contact_provenance(self):
        self.assertTrue(admin.site.is_registered(Agency))
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:sources_agency_changelist'), {'q': '가상부서', 'region__exact': '아산시'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['cl'].result_list), [self.agency])
        for text in ('가상부서', '000-000-0000', 'https://www.asan.go.kr/test', '기관 정보 확인일'):
            self.assertContains(response, text)
        response = self.client.get(reverse('admin:sources_agency_change', args=[self.agency.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('created_at', response.context['adminform'].form.fields)
        self.assertEqual(len(response.context['inline_admin_formsets']), 1)

    def test_staff_model_permissions_required_and_regular_user_denied(self):
        for user, expected in ((self.user, 302), (self.staff, 403)):
            self.client.force_login(user)
            for action in ('changelist', 'add', 'change', 'delete'):
                args = [self.agency.pk] if action in ('change', 'delete') else []
                self.assertEqual(self.client.get(reverse(f'admin:sources_agency_{action}', args=args)).status_code, expected)
