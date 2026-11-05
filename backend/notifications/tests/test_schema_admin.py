from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from notifications.models import NotificationPreference, PushDevice


class NotificationSchemaTests(SimpleTestCase):
    def test_schema_documents_real_protected_apis_without_identifiers_in_response(self):
        """실제 JWT 경로만 문서화하고 응답 스키마에서 비밀 등록 식별자를 제외한다."""
        schema = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(schema)
        for path, method in (
            ('/api/notifications/devices/', 'post'), ('/api/notifications/devices/{pk}/', 'delete'),
            ('/api/notifications/preferences/', 'get'), ('/api/notifications/preferences/', 'patch'),
        ):
            operation = schema['paths'][path][method]
            self.assertEqual(operation['security'], [{'jwtAuth': []}])
            self.assertIn('401', operation['responses'])
            self.assertIn('503', operation['responses'])
        self.assertNotIn('/api/notifications/reminders/', schema['paths'])
        response = schema['paths']['/api/notifications/devices/']['post']['responses']['200']
        name = response['content']['application/json']['schema']['$ref'].split('/')[-1]
        self.assertNotIn('registrationId', schema['components']['schemas'][name]['properties'])

    def test_admin_is_superuser_read_only_and_never_shows_raw_fid_or_hash(self):
        """두 관리 화면이 최고 관리자 조회 전용이며 FID·비교 해시를 표시하지 않는지 검증한다."""
        for model in (PushDevice, NotificationPreference):
            model_admin = admin.site._registry[model]
            request = RequestFactory().get('/admin/')
            request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=False)
            self.assertFalse(model_admin.has_view_permission(request))
            request.user.is_superuser = True
            self.assertTrue(model_admin.has_view_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
            for field in ('registration_id', 'registration_hash'):
                self.assertNotIn(field, model_admin.fields)
                self.assertNotIn(field, model_admin.list_display)
