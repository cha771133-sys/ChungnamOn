"""프롬프트의 마스킹 안내: 가림 표시 목록이 빠짐없이 들어가 있는지 (#76 새 가림 표시)."""
import unittest

from ai.chatbot import prompts

# 프롬프트에는 표시를 직접 적는다 (마스킹 코드에 의존하지 않음). 여기 목록도 직접 적어 둔다.
MASK_MARKS = ["***", "******-*******", "[이름 가림]", "[주소 가림]", "[전화번호 가림]", "[계좌 가림]", "[여권번호 가림]"]


class MaskingGuidanceTests(unittest.TestCase):
    def test_answer_and_verify_prompts_list_every_mask_mark(self):
        answer = prompts.ANSWER_SYSTEM.format(lang="Tiếng Việt")  # format이 깨지지 않는지도 확인
        for name, text in (("ANSWER_SYSTEM", answer), ("VERIFY_SYSTEM", prompts.VERIFY_SYSTEM)):
            for mark in MASK_MARKS:
                with self.subTest(prompt=name, mark=mark):
                    self.assertIn(mark, text)
            self.assertIn("추측", text)

    def test_matches_masking_module_marks(self):
        """마스킹 코드(우진님 #76)에 표시가 새로 생기면 프롬프트도 맞추라고 알려주는 확인용 테스트."""
        try:
            from documents.services import masking
        except Exception:  # Django 밖에서 돌릴 때
            self.skipTest("documents 앱을 불러올 수 없음 (manage.py test로 실행)")
        marks = {v for k, v in vars(masking).items() if k.endswith("_MARK") and isinstance(v, str)}
        self.assertTrue(marks)
        self.assertLessEqual(marks, set(MASK_MARKS), "마스킹 코드에 새 가림 표시가 생김 → prompts.py 안내에 추가 필요")


if __name__ == "__main__":
    unittest.main()
