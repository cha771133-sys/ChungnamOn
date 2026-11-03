import json
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.urls import resolve
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema
from rest_framework.test import APISimpleTestCase


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
            '/api/documents/{pk}/result/', '/api/auth/register/', '/api/auth/login/',
            '/api/auth/token/refresh/', '/api/auth/logout/', '/api/auth/me/',
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
        self.assertEqual(set(response['properties']), {'documentId', 'status', 'step', 'failReason'})
        self.assertEqual(set(response['required']), {'documentId', 'status', 'step'})
        result = self.schema['paths']['/api/documents/{pk}/result/']['get']['responses']
        for responses, codes in (
            (upload, ('400', '401', '403', '405', '415')),
            (status, ('401', '403', '404', '405')),
            (result, ('401', '403', '404', '405')),
        ):
            for code in codes:
                error = self.dereference(responses[code]['content']['application/json']['schema'])
                self.assertEqual(set(error['properties']), {'code', 'message', 'details'})

    def test_health_is_public_in_schema_and_with_invalid_authorization(self):
        health = self.schema['paths']['/api/health/']['get']
        self.assertFalse(health.get('security'))
        self.assertIn('200', health['responses'])
        response = self.client.get('/api/health/', HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_protected_operations_require_real_jwt_authentication(self):
        schemes = self.schema['components']['securitySchemes']
        self.assertEqual(schemes['jwtAuth'], {
            'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT',
        })
        for path, method in (
            ('/api/documents/', 'post'), ('/api/documents/{pk}/status/', 'get'),
            ('/api/documents/{pk}/result/', 'get'), ('/api/auth/me/', 'get'),
            ('/api/auth/me/', 'patch'),
        ):
            security = self.schema['paths'][path][method]['security']
            self.assertEqual(security, [{'jwtAuth': []}])
            self.assertIn('401', self.schema['paths'][path][method]['responses'])

    def test_public_authentication_requests_and_responses_are_documented(self):
        for path, fields, success, output in (
            ('/api/auth/register/', {'email', 'password'}, '201', {'id', 'email'}),
            ('/api/auth/login/', {'email', 'password'}, '200', {'access', 'refresh'}),
            ('/api/auth/token/refresh/', {'refresh'}, '200', {'access', 'refresh'}),
            ('/api/auth/logout/', {'refresh'}, '200', set()),
        ):
            with self.subTest(path=path):
                operation = self.schema['paths'][path]['post']
                self.assertFalse(operation.get('security'))
                request = self.dereference(operation['requestBody']['content']['application/json']['schema'])
                self.assertEqual(set(request['properties']), fields)
                response = self.dereference(operation['responses'][success]['content']['application/json']['schema'])
                self.assertEqual(set(response.get('properties', {})), output)
                for code in (('400',) if success == '201' else ('400', '401')):
                    error = self.dereference(operation['responses'][code]['content']['application/json']['schema'])
                    self.assertEqual(set(error['properties']), {'code', 'message', 'details'})

    def test_profile_patch_documents_only_editable_fields(self):
        operation = self.schema['paths']['/api/auth/me/']['patch']
        request = self.dereference(operation['requestBody']['content']['application/json']['schema'])
        self.assertEqual(set(request['properties']), {'language', 'nationality', 'visaType'})
        response = self.dereference(operation['responses']['200']['content']['application/json']['schema'])
        self.assertEqual(set(response['properties']), {
            'id', 'email', 'language', 'nationality', 'visaType', 'onboardingCompleted',
        })
        self.assertEqual(response['properties']['onboardingCompleted']['type'], 'boolean')
        for field in ('language', 'nationality', 'visaType'):
            self.assertTrue(response['properties'][field]['nullable'])
        for method in ('get', 'patch'):
            operation = self.schema['paths']['/api/auth/me/'][method]
            for code in (('401',) if method == 'get' else ('400', '401')):
                error = self.dereference(operation['responses'][code]['content']['application/json']['schema'])
                self.assertEqual(set(error['properties']), {'code', 'message', 'details'})

    def test_document_result_includes_business_409_and_optional_failure_reason(self):
        responses = self.schema['paths']['/api/documents/{pk}/result/']['get']['responses']
        result = self.dereference(responses['200']['content']['application/json']['schema'])
        self.assertEqual(set(result['properties']), {
            'documentId', 'docType', 'title', 'originalText', 'translatedText',
            'summary', 'terms', 'deadlines', 'requiredDocs', 'agency', 'verification',
            'maskedCount', 'lowConfidence',
        })
        pending = self.dereference(responses['409']['content']['application/json']['schema'])
        self.assertEqual(set(pending['properties']), {'detail', 'status', 'step', 'failReason'})
        self.assertEqual(set(pending['required']), {'detail', 'status', 'step'})

    def test_schema_contains_no_secret_configuration(self):
        self.assertNotIn(settings.SECRET_KEY, json.dumps(self.schema))
