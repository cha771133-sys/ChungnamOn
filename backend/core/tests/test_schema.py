import json
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.urls import resolve
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema
from rest_framework.test import APISimpleTestCase
from rest_framework_simplejwt.authentication import JWTAuthentication

from documents.views import DocumentStatusView, DocumentUploadView


class OpenAPITests(APISimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def dereference(self, schema):
        if '$ref' in schema:
            return self.schema['components']['schemas'][schema['$ref'].split('/')[-1]]
        return schema

    def test_schema_endpoint_is_public_and_valid(self):
        response = self.client.get('/api/schema/', HTTP_ACCEPT='application/vnd.oai.openapi+json')
        self.assertEqual(response.status_code, 200)
        schema = json.loads(response.content)
        validate_schema(schema)
        self.assertEqual(schema['info'], {
            'title': 'ChungnamOn API', 'version': '1.0.0',
            'description': 'ChungnamOn backend REST API',
        })

    def test_swagger_html_loads_and_uses_real_schema_url(self):
        response = self.client.get('/api/docs/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SwaggerUIBundle')
        self.assertContains(response, '/api/schema/')
        self.assertContains(response, 'ChungnamOn API')

    def test_management_command_validates_without_warnings(self):
        output = StringIO()
        call_command('spectacular', format='openapi-json', validate=True, fail_on_warn=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['openapi'], '3.0.3')

    def test_only_actual_api_paths_are_documented(self):
        self.assertTrue({
            '/api/health/', '/api/documents/', '/api/documents/{pk}/status/',
        }.issubset(self.schema['paths']))
        for path in self.schema['paths']:
            with self.subTest(path=path):
                self.assertIsNotNone(resolve(path.replace('{pk}', '1')))
        status = self.schema['paths']['/api/documents/{pk}/status/']['get']
        self.assertIn({'in': 'path', 'name': 'pk', 'schema': {'type': 'integer'},
                       'description': '문서 ID', 'required': True}, status['parameters'])

    def test_multipart_binary_file_and_limits_are_documented(self):
        operation = self.schema['paths']['/api/documents/']['post']
        content = operation['requestBody']['content']
        self.assertEqual(set(content), {'multipart/form-data'})
        upload = self.dereference(content['multipart/form-data']['schema'])
        self.assertEqual(upload['required'], ['file'])
        file = upload['properties']['file']
        self.assertEqual(file['type'], 'string')
        self.assertEqual(file['format'], 'binary')
        for term in ('jpg', 'jpeg', 'png', 'pdf', '20MB'):
            self.assertIn(term, file['description'])

    def test_success_and_error_schemas_match_existing_contracts(self):
        upload = self.schema['paths']['/api/documents/']['post']['responses']
        response = self.dereference(upload['201']['content']['application/json']['schema'])
        self.assertEqual(set(response['properties']), {'documentId', 'status'})
        status = self.schema['paths']['/api/documents/{pk}/status/']['get']['responses']
        response = self.dereference(status['200']['content']['application/json']['schema'])
        self.assertEqual(set(response['properties']), {'documentId', 'status', 'step'})
        self.assertEqual(set(response['required']), {'documentId', 'status', 'step'})
        for responses, codes in ((upload, ('400', '403', '405', '415')), (status, ('403', '404', '405'))):
            for code in codes:
                error = self.dereference(responses[code]['content']['application/json']['schema'])
                self.assertEqual(set(error['properties']), {'code', 'message', 'details'})

    def test_health_is_public_in_schema_and_with_invalid_authorization(self):
        health = self.schema['paths']['/api/health/']['get']
        self.assertFalse(health.get('security'))
        self.assertIn('200', health['responses'])
        response = self.client.get('/api/health/', HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_current_authentication_is_documented_without_fake_jwt_login(self):
        schemes = self.schema['components']['securitySchemes']
        for path, method in (('/api/documents/', 'post'), ('/api/documents/{pk}/status/', 'get')):
            security = self.schema['paths'][path][method]['security']
            self.assertTrue(security)
            self.assertNotIn({}, security)
            for requirement in security:
                self.assertTrue(set(requirement).issubset(schemes))
        if not apps.is_installed('accounts'):
            self.assertNotIn('/api/auth/login/', self.schema['paths'])

    def test_simplejwt_schema_integration_when_authentication_is_enabled(self):
        # P0-1 미병합 상태에서는 운영 인증 정책을 변경하지 않고 전환 호환성만 확인한다.
        with patch.object(DocumentUploadView, 'authentication_classes', [JWTAuthentication]), \
                patch.object(DocumentStatusView, 'authentication_classes', [JWTAuthentication]):
            schema = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(schema)
        self.assertEqual(schema['components']['securitySchemes']['jwtAuth'], {
            'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT',
        })
        for path, method in (('/api/documents/', 'post'), ('/api/documents/{pk}/status/', 'get')):
            self.assertEqual(schema['paths'][path][method]['security'], [{'jwtAuth': []}])
        self.assertFalse(schema['paths']['/api/health/']['get'].get('security'))

    def test_schema_contains_no_secret_configuration(self):
        self.assertNotIn(settings.SECRET_KEY, json.dumps(self.schema))
