from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory
from django.urls import reverse
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


    def test_document_admin_deletion_cascades_without_chat_delete_permission(self):
        staff = get_user_model().objects.create_user('document-admin@example.com', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='documents',
            codename__in=['view_document', 'delete_document', 'delete_interpretationresult'],
        ))
        message = self.make_message(self.document)
        self.client.force_login(staff)
        url = reverse('admin:documents_document_delete', args=[self.document.pk])
        confirmation = self.client.get(url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertFalse(confirmation.context['perms_lacking'])
        self.assertNotContains(confirmation, message.question)
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assertFalse(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertFalse(type(self.document).objects.filter(pk=self.document.pk).exists())

    def test_user_admin_deletion_preserves_existing_document_cascade(self):
        root = get_user_model().objects.create_user('account-admin@example.com', is_staff=True, is_superuser=True)
        message = self.make_message(self.document)
        self.client.force_login(root)
        url = reverse('admin:accounts_user_delete', args=[self.user.pk])
        confirmation = self.client.get(url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertFalse(confirmation.context['perms_lacking'])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assertFalse(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_parent_delete_permission_is_still_required(self):
        staff = get_user_model().objects.create_user('view-admin@example.com', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(
            content_type__app_label='documents', codename='view_document',
        ))
        message = self.make_message(self.document)
        self.client.force_login(staff)
        url = reverse('admin:documents_document_delete', args=[self.document.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())

    def test_superuser_cannot_delete_chat_directly_or_via_bulk_action(self):
        root = get_user_model().objects.create_user('chat-admin@example.com', is_staff=True, is_superuser=True)
        message = self.make_message(self.document)
        self.client.force_login(root)
        url = reverse('admin:chatbot_chatmessage_delete', args=[message.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        request = RequestFactory().get('/admin/chatbot/chatmessage/')
        request.user = root
        self.assertNotIn('delete_selected', admin.site._registry[ChatMessage].get_actions(request))
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())


    def test_other_cascade_delete_permissions_are_not_bypassed(self):
        staff = get_user_model().objects.create_user('restricted-admin@example.com', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='documents', codename__in=['view_document', 'delete_document'],
        ))
        message = self.make_message(self.document)
        self.client.force_login(staff)
        url = reverse('admin:documents_document_delete', args=[self.document.pk])
        confirmation = self.client.get(url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertEqual(confirmation.context['perms_lacking'], {self.result._meta.verbose_name})
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())
