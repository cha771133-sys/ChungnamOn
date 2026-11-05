from django.test import SimpleTestCase, override_settings

from accounts.models import UserProfile
from chatbot.services import ai_client


class AIClientTests(SimpleTestCase):
    def generate(self, language='ko'):
        return ai_client.generate_answer('질문', {}, [], language, [])

    @override_settings(CHATBOT_AI_MODE='dummy')
    def test_dummy_is_explicit_local_unverified_for_all_profile_languages(self):
        """지원 언어 전체에서 실제 안내문 언어와 미확인 근거 상태가 일치하는지 확인한다."""
        self.assertEqual(ai_client.MODE, 'dummy')
        self.assertEqual(set(ai_client.DUMMY_ANSWERS), set(UserProfile.Language.values))
        for language in UserProfile.Language.values:
            with self.subTest(language=language):
                result = self.generate(language)
                self.assertEqual(result['language'], language)
                self.assertEqual(result['answer'], ai_client.DUMMY_ANSWERS[language])
                self.assertEqual(result['sources'], [])
                self.assertEqual(result['suggestedQuestions'], [])
                self.assertFalse(result['grounded'])
                self.assertEqual(result['status'], 'UNVERIFIED')

    @override_settings(CHATBOT_AI_MODE='real')
    def test_real_mode_never_falls_back_to_dummy(self):
        with self.assertRaises(ai_client.AIClientError):
            self.generate()

    def test_unsupported_language_is_not_falsely_labelled(self):
        with self.assertRaises(ai_client.AIClientError):
            self.generate('xx')

    def test_privacy_boundary_masks_nested_payload_without_mutation(self):
        # 합성 테스트 문자열이며 실제 개인정보가 아니다.
        raw = {'question': '990101-5000000 / 010-0000-0000',
               'history': [{'answer': '주소: 테스트 주소'}], 'count': 1}
        prepared = ai_client.prepare_ai_payload(raw)
        self.assertEqual(prepared['question'], '******-******* / [전화번호 가림]')
        self.assertEqual(prepared['history'][0]['answer'], '주소: [주소 가림]')
        self.assertIn('990101', raw['question'])
        self.assertEqual(prepared['count'], 1)
