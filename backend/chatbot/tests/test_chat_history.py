from django.utils import timezone
from rest_framework.test import APITestCase

from chatbot.models import ChatMessage
from documents.models import Document

from .helpers import ChatFixtures


class ChatHistoryTests(ChatFixtures, APITestCase):
    def setUp(self):
        self.authenticate()

    def test_empty_history_is_read_only(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'count': 0, 'next': None, 'previous': None, 'results': []})
        self.assertFalse(ChatMessage.objects.exists())

    def test_owner_filter_document_isolation_and_stable_tie_order(self):
        another = self.make_document(self.user)
        self.make_message(another, question='다른 문서')
        self.make_message(self.other_document, question='다른 사용자')
        messages = [self.make_message(self.document, question=f'질문 {i}') for i in range(3)]
        ChatMessage.objects.filter(document=self.document).update(created_at=timezone.now())
        count = ChatMessage.objects.count()
        response = self.client.get(self.url)
        self.assertEqual(response.data['count'], 3)
        self.assertEqual([row['chatId'] for row in response.data['results']], [m.pk for m in reversed(messages)])
        self.assertTrue(all(row['documentId'] == self.document.pk for row in response.data['results']))
        self.assertEqual(ChatMessage.objects.count(), count)
        for field in ('question', 'answer', 'sources', 'createdAt'):
            self.assertIn(field, response.data['results'][0])

    def test_authentication_and_same_404_for_other_or_missing_documents(self):
        bodies = []
        for pk in (self.other_document.pk, 999999):
            response = self.client.get(f'/api/documents/{pk}/chat/')
            self.assertEqual(response.status_code, 404)
            bodies.append(response.json())
        self.assertEqual(bodies[0], bodies[1])
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_history_remains_available_for_every_processing_state_without_result(self):
        message = self.make_message(self.document)
        self.result.delete()
        for state in Document.Status.values:
            with self.subTest(state=state):
                Document.objects.filter(pk=self.document.pk).update(status=state, low_confidence=True)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['results'][0]['chatId'], message.pk)

    def test_standard_pagination_default_cap_and_page_boundaries(self):
        messages = [self.make_message(self.document) for _ in range(105)]
        ChatMessage.objects.filter(document=self.document).update(created_at=timezone.now())
        response = self.client.get(self.url)
        self.assertEqual(response.data['count'], 105)
        self.assertEqual(len(response.data['results']), 20)
        self.assertIsNotNone(response.data['next'])
        first = self.client.get(self.url, {'page_size': 9999})
        second = self.client.get(self.url, {'page_size': 9999, 'page': 2})
        self.assertEqual(len(first.data['results']), 100)
        self.assertEqual(len(second.data['results']), 5)
        ids = [row['chatId'] for row in first.data['results'] + second.data['results']]
        self.assertEqual(ids, [m.pk for m in reversed(messages)])
        self.assertEqual(self.client.get(self.url, {'page': 999}).status_code, 404)
