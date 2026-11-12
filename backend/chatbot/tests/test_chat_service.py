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
        """같은 문서의 최근 10개만 시간순으로 전달하고 파일 경로·다른 대화를 제외하는지 확인한다."""
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
        """필수 필드 누락·잘못된 타입·근거 상태 모순을 모두 저장 전 서비스 오류로 거부하는지 확인한다."""
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
        """상태·수집일·공식 URL·문서 검증 요건을 충족하지 않는 근거 주장을 거부하는지 확인한다."""
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
        """AI가 서버의 URL·제목·확인일·본문 해시를 바꾸면 근거 계약 오류로 거부하는지 확인한다."""
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
        """AI 호출 중 상태·신뢰도·소유권·해석·출처가 변하면 저장을 거부하는지 각각 확인한다."""
        for change in ('status', 'confidence', 'owner', 'result', 'missing_result', 'source'):
            with self.subTest(change=change):
                # 각 하위 테스트의 변경은 롤백한다.
                with transaction.atomic():
                    source = self.source()

                    def mutate(**kwargs):
                        """AI 호출 도중 한 가지 입력 조건을 바꿔 저장 직전 재검사를 유도한다."""
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


class ChatItemSourceTests(ChatFixtures, TestCase):
    """C1 항목별 출처 연결과 실제 질문 처리의 근거 경계를 검증한다."""

    @classmethod
    def setUpTestData(cls):
        """문서 검증에 연결할 기한·연락처 공식 자료를 준비한다."""
        super().setUpTestData()
        cls.deadline_source = create_source(
            status=SourceDocument.Status.ACTIVE, collected_at=timezone.now(),
            content_hash='deadline-hash',
        )
        cls.contact_source = create_source(
            source_url='https://www.asan.go.kr/notices/2', title='연락처 안내',
            status=SourceDocument.Status.ACTIVE, collected_at=timezone.now(),
            content_hash='contact-hash',
        )

    def save_verification(self, items, *, status='UNVERIFIED', sources=None):
        """지정한 항목과 출처 연결을 C1 검증 결과로 저장한다."""
        self.result.verification = {
            'overall': status, 'status': status, 'items': items,
            'sources': sources if sources is not None else [
                {'id': 's1', 'sourceDocumentId': self.deadline_source.pk},
                {'id': 's2', 'sourceDocumentId': self.contact_source.pk},
            ],
        }
        self.result.save(update_fields=['verification'])

    @staticmethod
    def snapshot(source):
        """AI 메타데이터 대신 DB 공식 자료에서 기대 스냅샷을 만든다."""
        return {
            'sourceDocumentId': source.pk, 'title': source.title, 'url': source.source_url,
            'checkedAt': source.collected_at.isoformat(), 'contentHash': source.content_hash,
        }

    @staticmethod
    def answer(sources, *, status=None):
        """지정 자료의 ID만 포함하는 AI 응답을 네트워크 없이 구성한다."""
        return {
            'answer': '공식 자료를 확인했습니다.',
            'sources': [{'sourceDocumentId': source.pk} for source in sources],
            'grounded': (status or ('GROUNDED' if sources else 'UNVERIFIED')) == 'GROUNDED',
            'status': status or ('GROUNDED' if sources else 'UNVERIFIED'),
            'suggestedQuestions': [],
        }

    def submit(self):
        """소유자 권한으로 실제 챗봇 질문 서비스의 전체 흐름을 실행한다."""
        return chat_service.submit_question(self.user, self.document.pk, {'message': '기한은 언제인가요?'})

    def assert_submission_sources(self, sources, *, status=None):
        """선택 함수, AI 입력, 실제 DB 출처 스냅샷이 모두 같은 공식 자료인지 확인한다."""
        expected = [self.snapshot(source) for source in sources]
        self.assertEqual(chat_service._official_sources({'verification': self.result.verification}), expected)
        payload = self.answer(sources, status=status)
        with patch('chatbot.services.ai_client.generate_answer', return_value=payload) as ai:
            message = self.submit()
        ai.assert_called_once()
        self.assertEqual(ai.call_args.kwargs['official_sources'], expected)
        message.refresh_from_db()
        self.assertEqual(message.sources, expected)
        self.assertEqual(message.status, payload['status'])
        self.assertEqual(message.grounded, payload['grounded'])

    def test_unverified_overall_uses_only_matched_item_source(self):
        """전체 미확인 상태에서도 일치한 기한의 출처만 전달하고 저장한다."""
        self.save_verification([
            {'key': 'DEADLINE', 'label': '납부 기한', 'docValue': '2026-12-01',
             'officialValue': '2026-12-01', 'reason': '공식 기한과 일치',
             'status': 'MATCHED', 'sourceIds': ['s1']},
            {'key': 'AGENCY_CONTACT', 'status': 'UNVERIFIED', 'sourceIds': ['s2']},
        ])
        self.assert_submission_sources([self.deadline_source])

    def test_mismatch_item_source_allows_mismatch_answer_when_overall_agrees(self):
        """항목과 전체 상태가 불일치이면 해당 출처를 포함한 불일치 답변을 허용한다."""
        self.save_verification([
            {'key': 'DEADLINE', 'status': 'MISMATCH', 'sourceIds': ['s1']},
            {'key': 'AGENCY_CONTACT', 'status': 'UNVERIFIED', 'sourceIds': ['s2']},
        ], status='MISMATCH')
        self.assert_submission_sources([self.deadline_source], status='MISMATCH')

    def test_multiple_items_and_reference_ids_share_one_source_snapshot(self):
        """여러 항목과 참조 ID가 같은 자료를 가리켜도 스냅샷은 하나만 저장한다."""
        self.save_verification([
            {'status': 'MATCHED', 'sourceIds': ['s1', 's1']},
            {'status': 'MATCHED', 'sourceIds': ['s1', 's2']},
        ], status='MATCHED', sources=[
            {'id': 's1', 'sourceDocumentId': self.deadline_source.pk},
            {'id': 's2', 'sourceDocumentId': self.deadline_source.pk},
        ])
        self.assert_submission_sources([self.deadline_source])

    def test_multiple_matched_items_select_all_linked_sources_in_database_order(self):
        """서로 다른 일치 항목에 연결된 자료를 DB ID 순으로 모두 선택한다."""
        self.save_verification([
            {'status': 'MATCHED', 'sourceIds': ['s2']},
            {'status': 'MATCHED', 'sourceIds': ['s1']},
        ], status='MATCHED')
        self.assert_submission_sources([self.deadline_source, self.contact_source])

    def test_unverified_items_never_authorize_their_sources(self):
        """전체 상태가 일치여도 미확인 항목의 출처는 근거로 인정하지 않는다."""
        self.save_verification([
            {'status': 'UNVERIFIED', 'sourceIds': ['s1', 's2']},
        ], status='MATCHED')
        self.assert_submission_sources([])

    def test_empty_items_never_fall_back_to_overall_status(self):
        """빈 항목 목록은 전체 상태와 무관하게 공식 출처를 만들지 않는다."""
        for status in ('MATCHED', 'MISMATCH', 'UNVERIFIED'):
            with self.subTest(status=status):
                self.save_verification([], status=status)
                self.assert_submission_sources([])

    def test_missing_items_preserves_legacy_overall_status_policy(self):
        """항목 키가 없으면 전체 상태에 따르는 기존 출처 선택을 유지한다."""
        for status in ('MATCHED', 'MISMATCH', 'UNVERIFIED'):
            with self.subTest(status=status):
                self.save_verification([], status=status)
                del self.result.verification['items']
                self.result.save(update_fields=['verification'])
                expected = [] if status == 'UNVERIFIED' else [self.deadline_source, self.contact_source]
                self.assert_submission_sources(expected)

    def test_unknown_and_invalid_reference_ids_are_ignored_without_guessing(self):
        """없는 ID·잘못된 타입·비슷한 URL과 제목으로는 자료를 연결하지 않는다."""
        self.save_verification([
            {'status': 'MATCHED', 'sourceIds': ['s1', 'missing', 'S2', '', None, True, 2, [], {}]},
        ], sources=[
            {'id': 's1', 'sourceDocumentId': self.deadline_source.pk},
            {'id': 's2', 'sourceDocumentId': self.contact_source.pk},
            {'id': '', 'sourceDocumentId': self.contact_source.pk},
            {'id': True, 'sourceDocumentId': self.contact_source.pk},
            {'id': [], 'sourceDocumentId': self.contact_source.pk},
            {'id': {}, 'sourceDocumentId': self.contact_source.pk},
            {'title': self.contact_source.title, 'url': self.contact_source.source_url,
             'sourceDocumentId': self.contact_source.pk},
            None,
        ])
        self.assert_submission_sources([self.deadline_source])

    def test_invalid_or_missing_source_document_ids_are_excluded(self):
        """불리언·비정수·0 이하·없는 자료 ID를 유효한 연결과 구분해 제외한다."""
        for value in (True, False, 0, -1, str(self.deadline_source.pk), float(self.deadline_source.pk),
                      None, [], {}, 999999):
            with self.subTest(value=value):
                self.save_verification([
                    {'status': 'MATCHED', 'sourceIds': ['s1', 'bad']},
                ], sources=[
                    {'id': 's1', 'sourceDocumentId': self.contact_source.pk},
                    {'id': 'bad', 'sourceDocumentId': value},
                ])
                self.assert_submission_sources([self.contact_source])

    def test_pending_inactive_uncollected_and_nonofficial_item_sources_are_excluded(self):
        """상태·수집일·공식 URL 조건을 충족하지 않는 항목 출처를 제외한다."""
        self.save_verification([{'status': 'MATCHED', 'sourceIds': ['s1']}])
        for changes in (
            {'status': SourceDocument.Status.PENDING}, {'status': SourceDocument.Status.INACTIVE},
            {'collected_at': None}, {'source_url': 'https://example.org/unofficial'},
        ):
            with self.subTest(changes=changes), transaction.atomic():
                SourceDocument.objects.filter(pk=self.deadline_source.pk).update(**changes)
                self.assert_submission_sources([])
                transaction.set_rollback(True)

    def test_malformed_items_do_not_fall_back_to_legacy_sources(self):
        """잘못된 항목 컨테이너를 전체 상태로 우회하여 신뢰하지 않는다."""
        for items in (None, {}, 's1', 1, True):
            with self.subTest(items=items):
                self.save_verification(items, status='MATCHED')
                self.assert_submission_sources([])

    def test_malformed_items_and_source_id_lists_cannot_authorize_sources(self):
        """손상된 항목은 무시하고 별도로 유효한 항목의 연결만 허용한다."""
        invalid_items = [
            None, [], 's1', {},
            {'status': 'MATCHED', 'sourceIds': 's1'},
            {'status': 'MATCHED', 'sourceIds': {'s1': True}},
            {'status': ['MATCHED'], 'sourceIds': ['s1']},
            {'status': 'matched', 'sourceIds': ['s1']},
            {'status': 'MATCHED'},
        ]
        self.save_verification(invalid_items, status='MATCHED')
        self.assert_submission_sources([])
        self.save_verification(invalid_items + [{'status': 'MATCHED', 'sourceIds': ['s2']}])
        self.assert_submission_sources([self.contact_source])

    def test_malformed_verification_sources_are_not_evidence(self):
        """출처 컨테이너가 목록이 아니면 항목 상태와 무관하게 근거를 제외한다."""
        for sources in (None, {}, 's1', True):
            with self.subTest(sources=sources):
                self.save_verification([{'status': 'MATCHED', 'sourceIds': ['s1']}])
                self.result.verification['sources'] = sources
                self.result.save(update_fields=['verification'])
                self.assert_submission_sources([])

    def test_ai_cannot_return_unselected_sources_or_fabricated_metadata(self):
        """AI가 선택되지 않은 출처나 조작된 메타데이터를 반환하면 저장을 거부한다."""
        self.save_verification([
            {'status': 'MATCHED', 'sourceIds': ['s1']},
            {'status': 'UNVERIFIED', 'sourceIds': ['s2']},
        ])
        sources = [{'sourceDocumentId': self.contact_source.pk}, {'sourceDocumentId': 999999}]
        for key, value in (('url', 'https://www.gov.kr/invented'), ('title', '임의 제목'),
                           ('checkedAt', '2020-01-01'), ('contentHash', 'fabricated')):
            sources.append({'sourceDocumentId': self.deadline_source.pk, key: value})
        for source in sources:
            payload = {**self.answer([self.deadline_source]), 'sources': [source]}
            with self.subTest(source=source), patch(
                    'chatbot.services.ai_client.generate_answer', return_value=payload) as ai:
                with self.assertRaises(chat_service.ChatServiceUnavailable):
                    self.submit()
            self.assertEqual(ai.call_args.kwargs['official_sources'], [self.snapshot(self.deadline_source)])
        self.assertFalse(ChatMessage.objects.exists())

    def test_item_source_state_and_snapshot_changes_during_ai_call_prevent_saving(self):
        """AI 호출 중 자료의 적격 조건이나 스냅샷이 달라지면 저장을 거부한다."""
        self.save_verification([{'status': 'MATCHED', 'sourceIds': ['s1']}])
        for changes in (
            {'status': SourceDocument.Status.INACTIVE}, {'collected_at': None},
            {'source_url': 'https://example.org/unofficial'}, {'content_hash': 'updated-hash'},
            {'title': '변경된 제목'}, {'collected_at': timezone.now()},
        ):
            with self.subTest(changes=changes), transaction.atomic():
                def mutate(**kwargs):
                    """응답 생성 중 DB 공식 자료를 바꿔 저장 직전 재검사를 유도한다."""
                    SourceDocument.objects.filter(pk=self.deadline_source.pk).update(**changes)
                    return self.answer([self.deadline_source])

                with patch('chatbot.services.ai_client.generate_answer', side_effect=mutate) as ai:
                    with self.assertRaises(chat_service.ChatServiceUnavailable):
                        self.submit()
                self.assertEqual(ai.call_args.kwargs['official_sources'], [self.snapshot(self.deadline_source)])
                self.assertFalse(ChatMessage.objects.exists())
                transaction.set_rollback(True)

    def test_item_mismatch_does_not_relax_overall_mismatch_answer_policy(self):
        """불일치 항목이 있어도 전체 상태가 불일치가 아니면 불일치 답변을 거부한다."""
        for status in ('MATCHED', 'UNVERIFIED'):
            with self.subTest(status=status):
                self.save_verification([
                    {'status': 'MISMATCH', 'sourceIds': ['s1']},
                ], status=status)
                with patch('chatbot.services.ai_client.generate_answer',
                           return_value=self.answer([self.deadline_source], status='MISMATCH')) as ai:
                    with self.assertRaises(chat_service.ChatServiceUnavailable):
                        self.submit()
                self.assertEqual(ai.call_args.kwargs['official_sources'], [self.snapshot(self.deadline_source)])
        self.assertFalse(ChatMessage.objects.exists())
