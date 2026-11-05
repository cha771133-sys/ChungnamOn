from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.http import Http404
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import NotAuthenticated

from accounts.models import UserProfile
from chatbot.models import ChatMessage
from chatbot.services import ai_client, chat_service
from documents.models import Document, InterpretationResult
from sources.models import SourceDocument
from sources.tests.helpers import create_document as create_source

from .helpers import ChatFixtures


class ChatServiceTests(ChatFixtures, TestCase):
    def submit(self):
        return chat_service.submit_question(self.user, self.document.pk, {'message': '질문'})

    def source(self, verification_status='MATCHED', **overrides):
        source = create_source(**{
            'status': SourceDocument.Status.ACTIVE, 'collected_at': timezone.now(),
            'content_hash': 'test-content-hash', **overrides,
        })
        self.result.verification = {
            'status': verification_status, 'sources': [{'sourceDocumentId': source.pk}],
        }
        self.result.save(update_fields=['verification'])
        return source

    @staticmethod
    def output(**overrides):
        return {
            'answer': '기관 확인이 필요합니다.', 'sources': [], 'grounded': False,
            'status': 'UNVERIFIED', 'suggestedQuestions': [], **overrides,
        }

    def test_service_rejects_anonymous_user(self):
        with self.assertRaises(NotAuthenticated):
            chat_service.submit_question(AnonymousUser(), self.document.pk, {'message': '질문'})

    @patch('chatbot.services.ai_client.generate_answer')
    def test_adapter_context_uses_only_recent_same_document_history_in_chronological_order(self, ai):
        self.make_message(self.other_document, question='다른 사용자')
        self.make_message(self.make_document(self.user), question='다른 문서')
        messages = [self.make_message(self.document, question=f'질문 {i}') for i in range(13)]
        ChatMessage.objects.filter(document=self.document).update(created_at=timezone.now())
        ai.return_value = self.output()
        self.submit()
        payload = ai.call_args.kwargs
        self.assertEqual([row['question'] for row in payload['history']], [m.question for m in messages[-10:]])
        self.assertTrue(all(row['grounded'] is False for row in payload['history']))
        self.assertEqual(payload['official_sources'], [])
        self.assertEqual(set(payload), {'question', 'interpretation', 'history', 'language', 'official_sources'})
        self.assertEqual(set(payload['interpretation']), set(chat_service.INTERPRETATION_FIELDS))
        self.assertEqual(payload['interpretation']['original_text'], '주소: [주소 가림]')
        self.assertEqual(payload['interpretation']['required_docs'], ['문서'])
        self.assertNotIn('file', str(payload.keys()))
        self.assertNotIn(self.document.file.name, str(payload))

    def test_profile_precedes_interpretation_and_default_language(self):
        self.result.language = 'vi'
        self.result.save(update_fields=['language'])
        profile = UserProfile.objects.create(user=self.user, language='en')
        message = self.submit()
        self.assertEqual(message.language, 'en')
        self.assertEqual(message.answer, ai_client.DUMMY_ANSWERS['en'])
        profile.delete()
        self.assertEqual(self.submit().language, 'vi')
        self.result.language = ''
        self.result.save(update_fields=['language'])
        with override_settings(LANGUAGE_CODE='ko-kr'):
            self.assertEqual(self.submit().language, 'ko')
        with override_settings(LANGUAGE_CODE='fr'):
            self.assertEqual(self.submit().language, 'ko')
        self.assertFalse(UserProfile.objects.exists())

    @patch('chatbot.services.ai_client.generate_answer')
    def test_sensitive_question_is_stored_but_masked_at_ai_boundary(self, ai):
        ai.return_value = self.output()
        question = '990101-5000000 010-0000-0000'
        message = chat_service.submit_question(self.user, self.document.pk, {'message': question})
        self.assertEqual(message.question, question)
        self.assertEqual(ai.call_args.kwargs['question'], '******-******* [전화번호 가림]')

    def test_bad_ai_contract_never_creates_a_success_record(self):
        cases = [None, [], {}, self.output(answer=' '), self.output(answer=123),
                 self.output(grounded='false'), self.output(sources={}),
                 self.output(sources=[{'url': 'https://www.asan.go.kr/'}]),
                 self.output(sources=[{'sourceDocumentId': 999999}]),
                 self.output(sources=[{'sourceDocumentId': True}]),
                 self.output(grounded=True), self.output(grounded=True, status='MISMATCH'),
                 self.output(grounded=False, status='GROUNDED'), self.output(status='INVALID'),
                 self.output(status='MISMATCH'), self.output(language='vi'),
                 self.output(suggestedQuestions='question'), self.output(suggestedQuestions=[1]),
                 self.output(suggestedQuestions=['x' * 201]), self.output(suggestedQuestions=[' ']),
                 self.output(suggestedQuestions=['1', '2', '3', '4', '5', '6'])]
        for missing in ('answer', 'sources', 'grounded', 'suggestedQuestions'):
            cases.append({key: value for key, value in self.output().items() if key != missing})
        for payload in cases:
            with self.subTest(payload=payload), patch('chatbot.services.ai_client.generate_answer', return_value=payload):
                with self.assertRaises(chat_service.ChatServiceUnavailable):
                    self.submit()
        self.assertFalse(ChatMessage.objects.exists())

    def test_suggestions_are_trimmed_and_deduplicated(self):
        with patch('chatbot.services.ai_client.generate_answer', return_value=self.output(
                suggestedQuestions=[' 질문? ', '질문?', '다른 질문?'])):
            message = self.submit()
        self.assertEqual(message.suggested_questions, ['질문?', '다른 질문?'])

    def test_grounded_requires_explicit_ai_judgment_and_verified_source_snapshot(self):
        source = self.source()
        payload = self.output(grounded=True, status='GROUNDED', sources=[{'sourceDocumentId': source.pk}])
        with patch('chatbot.services.ai_client.generate_answer', return_value=payload) as ai:
            message = self.submit()
        self.assertTrue(message.grounded)
        self.assertEqual(message.status, 'GROUNDED')
        self.assertEqual(message.sources, [{
            'sourceDocumentId': source.pk, 'title': source.title, 'url': source.source_url,
            'checkedAt': source.collected_at.isoformat(), 'contentHash': source.content_hash,
        }])
        self.assertEqual(ai.call_args.kwargs['official_sources'], message.sources)
        original = message.sources
        source.title = '변경된 제목'
        source.collected_at = timezone.now()
        source.status = SourceDocument.Status.INACTIVE
        source.save()
        message.refresh_from_db()
        self.assertEqual(message.sources, original)
        self.assertFalse(self.submit().grounded)
        SourceDocument.objects.filter(pk=source.pk).update(status=SourceDocument.Status.ACTIVE)
        payload.pop('status')
        with patch('chatbot.services.ai_client.generate_answer', return_value=payload):
            with self.assertRaises(chat_service.ChatServiceUnavailable):
                self.submit()

    def test_pending_inactive_unchecked_nonofficial_or_unlinked_sources_cannot_ground_answer(self):
        source = self.source()
        payload = self.output(grounded=True, status='GROUNDED', sources=[{'sourceDocumentId': source.pk}])
        for changes in (
            {'status': SourceDocument.Status.PENDING}, {'status': SourceDocument.Status.INACTIVE},
            {'status': SourceDocument.Status.ACTIVE, 'collected_at': None},
            {'collected_at': timezone.now(), 'source_url': 'https://example.org/unofficial'},
        ):
            SourceDocument.objects.filter(pk=source.pk).update(**changes)
            with self.subTest(changes=changes), patch('chatbot.services.ai_client.generate_answer', return_value=payload):
                with self.assertRaises(chat_service.ChatServiceUnavailable):
                    self.submit()
        SourceDocument.objects.filter(pk=source.pk).update(source_url='https://www.asan.go.kr/test')
        self.result.verification = {'status': 'UNVERIFIED', 'sources': [{'sourceDocumentId': source.pk}]}
        self.result.save(update_fields=['verification'])
        with patch('chatbot.services.ai_client.generate_answer', return_value=payload):
            with self.assertRaises(chat_service.ChatServiceUnavailable):
                self.submit()
        self.assertFalse(ChatMessage.objects.exists())

    def test_unrelated_active_source_and_dummy_verification_are_not_evidence(self):
        source = create_source(status='ACTIVE', collected_at=timezone.now())
        payload = self.output(grounded=True, status='GROUNDED', sources=[{'sourceDocumentId': source.pk}])
        with patch('chatbot.services.ai_client.generate_answer', return_value=payload) as ai:
            with self.assertRaises(chat_service.ChatServiceUnavailable):
                self.submit()
        self.assertEqual(ai.call_args.kwargs['official_sources'], [])

    def test_ai_cannot_invent_source_metadata(self):
        source = self.source()
        for key, value in (('url', 'https://www.gov.kr/invented'), ('title', '발명한 제목'),
                           ('checkedAt', '2020-01-01'), ('contentHash', 'fabricated')):
            payload = self.output(grounded=True, status='GROUNDED', sources=[{'sourceDocumentId': source.pk, key: value}])
            with self.subTest(key=key), patch('chatbot.services.ai_client.generate_answer', return_value=payload):
                with self.assertRaises(chat_service.ChatServiceUnavailable):
                    self.submit()

    def test_mismatch_requires_ai_and_document_verification_to_explicitly_agree(self):
        source = self.source()
        payload = self.output(status='MISMATCH', sources=[{'sourceDocumentId': source.pk}])
        with patch('chatbot.services.ai_client.generate_answer', return_value=payload):
            with self.assertRaises(chat_service.ChatServiceUnavailable):
                self.submit()
            self.result.verification['status'] = 'MISMATCH'
            self.result.save(update_fields=['verification'])
            message = self.submit()
        self.assertEqual(message.status, 'MISMATCH')
        self.assertFalse(message.grounded)
        self.assertEqual(message.sources[0]['sourceDocumentId'], source.pk)

    def test_state_owner_interpretation_and_source_changes_during_ai_call_prevent_saving(self):
        for change in ('status', 'confidence', 'owner', 'result', 'missing_result', 'source'):
            with self.subTest(change=change):
                # 각 하위 테스트의 변경은 롤백한다.
                with transaction.atomic():
                    source = self.source()

                    def mutate(**kwargs):
                        if change == 'status':
                            Document.objects.filter(pk=self.document.pk).update(status='PROCESSING')
                        elif change == 'confidence':
                            Document.objects.filter(pk=self.document.pk).update(low_confidence=True)
                        elif change == 'owner':
                            Document.objects.filter(pk=self.document.pk).update(owner=self.other)
                        elif change == 'result':
                            InterpretationResult.objects.filter(pk=self.result.pk).update(summary=['새 결과'])
                        elif change == 'missing_result':
                            InterpretationResult.objects.filter(pk=self.result.pk).delete()
                        else:
                            SourceDocument.objects.filter(pk=source.pk).update(status='INACTIVE')
                        return self.output()

                    with patch('chatbot.services.ai_client.generate_answer', side_effect=mutate):
                        expected = {'owner': Http404, 'source': chat_service.ChatServiceUnavailable,
                                    'missing_result': chat_service.ChatServiceUnavailable}.get(change, chat_service.DocumentNotReady)
                        with self.assertRaises(expected):
                            self.submit()
                    self.assertFalse(ChatMessage.objects.exists())
                    transaction.set_rollback(True)

    @override_settings(CHATBOT_AI_MODE='real')
    def test_unimplemented_real_mode_is_service_failure_and_does_not_save(self):
        with self.assertRaises(chat_service.ChatServiceUnavailable):
            self.submit()
        self.assertFalse(ChatMessage.objects.exists())
