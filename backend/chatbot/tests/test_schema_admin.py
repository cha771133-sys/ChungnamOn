from django.contrib import admin
from django.test import RequestFactory
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase

from chatbot.models import ChatMessage

from .helpers import ChatFixtures


class SchemaAndAdminTests(ChatFixtures, APITestCase):
    def test_schema_request_responses_jwt_pagination_and_examples(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operations = schema['paths']['/api/documents/{pk}/chat/']
        for method in ('get', 'post'):
            operation = operations[method]
            self.assertEqual(operation['security'], [{'jwtAuth': []}])
            self.assertTrue(any(p['name'] == 'pk' and p['in'] == 'path' for p in operation['parameters']))
            self.assertTrue({'401', '404', '503'}.issubset(operation['responses']))
        post = operations['post']
        ref = post['requestBody']['content']['application/json']['schema']['$ref']
        request = schema['components']['schemas'][ref.split('/')[-1]]
        self.assertEqual(set(request['properties']), {'message'})
        self.assertEqual(request['properties']['message']['maxLength'], 2000)
        self.assertTrue({'201', '400', '409'}.issubset(post['responses']))
        self.assertIn('examples', post['responses']['201']['content']['application/json'])
        self.assertIn('examples', post['responses']['503']['content']['application/json'])
        history = operations['get']['responses']['200']['content']['application/json']
        ref = history['schema']['$ref'].split('/')[-1]
        self.assertEqual(set(schema['components']['schemas'][ref]['properties']), {'count', 'next', 'previous', 'results'})
        for example in history['examples'].values():
            self.assertEqual(example['value']['count'], len(example['value']['results']))
        source = schema['components']['schemas']['ChatSource']['properties']
        self.assertTrue({'sourceDocumentId', 'title', 'url', 'checkedAt'}.issubset(source))

    def test_admin_registered_read_only_and_personal_text_hidden(self):
        model_admin = admin.site._registry[ChatMessage]
        self.assertEqual(model_admin.list_display, ('id', 'document_id', 'language', 'grounded', 'status', 'created_at'))
        self.assertFalse(model_admin.search_fields)
        self.assertFalse({'question', 'answer'}.intersection(model_admin.fields))
        self.assertEqual(model_admin.fields, model_admin.readonly_fields)
        request = RequestFactory().get('/admin/')
        request.user = self.user
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/admin/chatbot/chatmessage/').status_code, 302)
