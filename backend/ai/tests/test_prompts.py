"""프롬프트의 마스킹 안내: 가림 표시 목록이 빠짐없이 들어가 있는지 (#76 새 가림 표시)."""
import unittest

from ai.chatbot import prompts

def django_ready() -> bool:
    """Django 설정이 있고 앱이 준비됐는지. Django가 없거나 설정 전인 경우만 False (그 밖의 오류는 숨기지 않음)."""
    try:
        from django.apps import apps
        from django.conf import settings
    except ImportError:
        return False
    return settings.configured and apps.ready


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
        if not django_ready():
            self.skipTest("Django 설정이 없음 (manage.py test로 실행)")
        # Django가 준비됐으면 import 오류(SyntaxError·RuntimeError 등)는 건너뛰지 않고 그대로 실패시킨다
        from documents.services import masking

        marks = {v for k, v in vars(masking).items() if k.endswith("_MARK") and isinstance(v, str)}
        self.assertTrue(marks)
        self.assertLessEqual(marks, set(MASK_MARKS), "마스킹 코드에 새 가림 표시가 생김 → prompts.py 안내에 추가 필요")


    def test_masking_import_error_fails_instead_of_skipping(self):
        """Django가 준비된 상태에서 masking을 불러오다 난 진짜 오류는 skip으로 숨기지 않는다 (CodeRabbit)."""
        if not django_ready():
            self.skipTest("Django 설정이 없음 (manage.py test로 실행)")
        import builtins
        from unittest import mock

        real_import = builtins.__import__

        def broken_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "documents.services" and "masking" in (fromlist or ()):
                raise RuntimeError("masking 모듈 오류")
            return real_import(name, globals, locals, fromlist, level)

        result = unittest.TestResult()
        with mock.patch("builtins.__import__", side_effect=broken_import):
            MaskingGuidanceTests("test_matches_masking_module_marks").run(result)
        self.assertEqual((len(result.skipped), len(result.errors)), (0, 1))
        self.assertIn("masking 모듈 오류", result.errors[0][1])


if __name__ == "__main__":
    unittest.main()
