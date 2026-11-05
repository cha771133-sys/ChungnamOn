from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from chatbot.models import ChatMessage

from .helpers import ChatFixtures


class ChatMessageTests(ChatFixtures, TestCase):
    def test_pair_fields_defaults_relations_and_private_string(self):
        message = self.make_message(self.document, question='  서류가 없어요?  ')
        message.refresh_from_db()
        self.assertEqual(message.question, '서류가 없어요?')
        self.assertEqual(message.answer, '공식 근거 확인 필요')
        self.assertEqual(message.language, 'ko')
        self.assertEqual(message.status, ChatMessage.Status.UNVERIFIED)
        self.assertFalse(message.grounded)
        self.assertEqual(message.sources, [])
        self.assertEqual(message.suggested_questions, [])
        self.assertIsNotNone(message.created_at)
        self.assertEqual(self.document.chat_messages.get(), message)
        self.assertNotIn(message.question, str(message))
        self.assertNotIn('user', {field.name for field in ChatMessage._meta.fields})

    def test_json_and_historical_source_snapshot(self):
        source = {'sourceDocumentId': 123, 'title': '테스트 출처', 'url': 'https://www.asan.go.kr/',
                  'checkedAt': '2026-11-01T00:00:00+00:00', 'contentHash': 'test-hash'}
        message = self.make_message(self.document, sources=[source], suggested_questions=['다음 질문?'])
        message.refresh_from_db()
        self.assertEqual(message.sources, [source])
        self.assertEqual(message.suggested_questions, ['다음 질문?'])

    def test_multiple_documents_stable_order_and_cascade(self):
        first = self.make_message(self.document)
        second = self.make_message(self.document)
        other = self.make_message(self.other_document)
        ChatMessage.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=timezone.now())
        self.assertEqual(list(self.document.chat_messages.values_list('id', flat=True)), [second.pk, first.pk])
        self.document.delete()
        self.assertEqual(list(ChatMessage.objects.all()), [other])

    def test_blank_values_and_invalid_json_rejected(self):
        for values in (
            {'question': '   '}, {'answer': ''}, {'sources': {}}, {'suggested_questions': [1]},
            {'suggested_questions': ['a', 'a']}, {'suggested_questions': ['a' * 201]},
            {'suggested_questions': ['a', 'b', 'c', 'd', 'e', 'f']},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_message(self.document, **values)

    def test_status_consistency_model_and_database(self):
        for status, grounded in [('UNVERIFIED', True), ('MISMATCH', True), ('GROUNDED', False)]:
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.make_message(self.document, status=status, grounded=grounded)
        message = self.make_message(self.document)
        for values in ({'status': 'INVALID'}, {'status': 'GROUNDED'}, {'answer': ''}, {'question': ''}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                ChatMessage.objects.filter(pk=message.pk).update(**values)
