from unittest.mock import patch

from django.db import DatabaseError
from rest_framework.test import APITestCase

from chatbot.models import ChatMessage
from chatbot.services.ai_client import DUMMY_ANSWERS
from documents.models import Document

from .helpers import ChatFixtures


class ChatAPITests(ChatFixtures, APITestCase):
    def setUp(self):
        self.authenticate()

    def post(self, data=None, url=None):
        return self.client.post(url or self.url, {'message': '  서류가 없으면요?  '} if data is None else data, format='json')

    def test_success_exact_response_database_and_repeated_questions(self):
        for count in (1, 2):
            response = self.post()
            self.assertEqual(response.status_code, 201, response.data)
            body = response.json()
            self.assertEqual(set(body), {
                'chatId', 'documentId', 'question', 'answer', 'language', 'grounded',
                'status', 'sources', 'suggestedQuestions', 'createdAt',
            })
            self.assertEqual(body['documentId'], self.document.pk)
            self.assertEqual(body['question'], '서류가 없으면요?')
            self.assertEqual(body['answer'], DUMMY_ANSWERS['ko'])
            self.assertEqual(body['language'], 'ko')
            self.assertEqual(body['status'], 'UNVERIFIED')
            self.assertFalse(body['grounded'])
            self.assertEqual(body['sources'], [])
            self.assertEqual(ChatMessage.objects.count(), count)
            self.assertEqual(ChatMessage.objects.get(pk=body['chatId']).answer, body['answer'])

    def test_unicode_and_maximum_length(self):
        for message in ('你好？ ภาษาไทย Tiếng Việt नेपाली', '가' * 2000):
            with self.subTest(message_length=len(message)):
                response = self.post({'message': message})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data['question'], message)

    def test_unauthenticated_and_invalid_jwt(self):
        self.client.credentials()
        response = self.post()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['code'], 'NOT_AUTHENTICATED')
        self.assertIn('WWW-Authenticate', response)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual(self.post().status_code, 401)
        self.assertFalse(ChatMessage.objects.exists())

    @patch('chatbot.services.ai_client.generate_answer')
    def test_other_and_missing_documents_are_indistinguishable_even_for_staff(self, ai):
        bodies = []
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        for pk in (self.other_document.pk, 999999):
            response = self.post(url=f'/api/documents/{pk}/chat/')
            self.assertEqual(response.status_code, 404)
            bodies.append(response.json())
        self.assertEqual(bodies[0], bodies[1])
        ai.assert_not_called()

    @patch('chatbot.services.ai_client.generate_answer')
    def test_unfinished_failed_and_low_confidence_return_conflict_without_ai(self, ai):
        for state in (Document.Status.PENDING, Document.Status.PROCESSING, Document.Status.FAILED):
            Document.objects.filter(pk=self.document.pk).update(status=state)
            response = self.post()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'DOCUMENT_NOT_READY')
        Document.objects.filter(pk=self.document.pk).update(status=Document.Status.DONE, low_confidence=True)
        self.assertEqual(self.post().status_code, 409)
        ai.assert_not_called()
        self.assertFalse(ChatMessage.objects.exists())

    @patch('chatbot.services.ai_client.generate_answer')
    def test_done_missing_result_returns_retryable_failure(self, ai):
        self.result.delete()
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['code'], 'CHAT_SERVICE_UNAVAILABLE')
        ai.assert_not_called()
        self.assertFalse(ChatMessage.objects.exists())

    def test_invalid_inputs_and_injected_fields_do_not_save(self):
        for data in ({}, {'message': ''}, {'message': ' \n\t '}, {'message': 'x' * 2001},
                     {'message': 123}, {'message': True}, {'message': None}, {'message': []}, [], 'text'):
            with self.subTest(data_type=type(data).__name__):
                response = self.post(data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'VALIDATION_ERROR')
        for field in ('owner', 'user', 'document', 'documentId', 'grounded', 'sources', 'status'):
            with self.subTest(field=field):
                response = self.post({'message': '질문', field: True})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data['details'])
        self.assertFalse(ChatMessage.objects.exists())

    def test_ai_failures_are_sanitized_leave_existing_history_and_no_success_record(self):
        existing = self.make_message(self.document)
        for failure in (TimeoutError('private question'), RuntimeError('/private/server/path')):
            with patch('chatbot.services.ai_client.generate_answer', side_effect=failure):
                response = self.post()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {
                    'code': 'CHAT_SERVICE_UNAVAILABLE',
                    'message': '답변을 생성할 수 없습니다. 잠시 후 다시 시도해 주세요.', 'details': {},
                })
        self.assertEqual(list(ChatMessage.objects.all()), [existing])

    def test_database_failure_returns_service_error(self):
        with patch('chatbot.models.ChatMessage.objects.create', side_effect=DatabaseError('secret')):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', str(response.data))
        self.assertFalse(ChatMessage.objects.exists())

    def test_unsupported_methods_and_bad_json(self):
        self.assertEqual(self.client.delete(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, '{', content_type='application/json').status_code, 400)
