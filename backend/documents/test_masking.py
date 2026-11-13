"""개인정보 마스킹(W3) 테스트. 모든 이름·번호·주소는 가짜다.

W1 샘플(~/ocr-test-output)에서 확인한 형태를 필요한 줄만 남겨 옮겼다.
"""

import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from chatbot.services import ai_client

from .models import Document
from .services import masking, pipeline
from .services.pipeline import mask_personal_info

# sample1·sample3을 줄여 옮긴 고지서. 위는 가릴 것, 아래는 남길 것이다.
NOTICE = '\n'.join([
    '아산시',
    '2026년도 주민세(개인분) 납세고지서',
    '납세자',
    '응우옌 반 안 (NGUYEN VAN AN)',
    '외국인등록번호',
    '900101-5123456',
    '세목',
    '주민세(개인분)',
    '세액',
    '6,250원 (주민세 5,000원 + 지방교육세 1,250원)',
    '납부기한',
    '2026년 8월 31일',
    '전자납부번호',
    '4420012026080001234',
    '가상계좌',
    '농협 790-1234-5678-90 (예금주: 아산시)',
    '주소: 충청남도 아산시 배방읍 가상로 123, 101동 1001호',
    '문의: 아산시청 세무과 041-000-1234',
    '제출처: 국민건강보험공단 천안지사 민원실',
])

# sample3 3쪽(자동이체 신청서)을 줄여 옮겼다.
APPLICATION = '\n'.join([
    '보험료 자동이체 신청서',
    '신청인 정보',
    '성명',
    '스리 라하유',
    '외국인등록번호',
    '950315-6234567',
    '연락처',
    '010-0000-4321',
    '출금 은행',
    '하나은행',
    '출금 계좌',
    '123-456789-01234',
    '신청일',
    '2026년 11월 20일',
    '주소 충청남도 천안시 서북구 예시로 77, 202호',
])


def masked(text):
    return mask_personal_info(text)['masked_text']


class ContractTests(SimpleTestCase):
    def test_returns_masked_text_and_count(self):
        result = mask_personal_info(NOTICE)

        self.assertEqual(set(result), {'masked_text', 'masked_count'})
        self.assertIsInstance(result['masked_text'], str)
        # 이름 1, 등록번호 1, 주소 1
        self.assertEqual(result['masked_count'], 3)

    def test_application_count(self):
        # 이름 1, 등록번호 1, 연락처 1, 계좌 1, 주소 1
        self.assertEqual(mask_personal_info(APPLICATION)['masked_count'], 5)

    def test_non_string_raises(self):
        for value in (None, b'bytes', 123):
            with self.subTest(value=value), self.assertRaises(TypeError):
                mask_personal_info(value)

    def test_masking_twice_gives_same_result(self):
        """챗봇이 이미 가린 원문을 다시 넣어도 결과가 바뀌지 않는다."""
        for text in (NOTICE, APPLICATION):
            with self.subTest(text=text.split('\n')[0]):
                once = mask_personal_info(text)
                twice = mask_personal_info(once['masked_text'])

                self.assertEqual(twice['masked_text'], once['masked_text'])
                self.assertEqual(twice['masked_count'], 0)

    def test_empty_text(self):
        self.assertEqual(mask_personal_info(''), {'masked_text': '', 'masked_count': 0})

    def test_rules_are_listed_separately(self):
        """대상을 빼거나 더할 때 목록만 고치면 되도록 규칙이 나뉘어 있다."""
        self.assertEqual(
            [rule.__name__ for rule in masking.MASKING_RULES],
            ['mask_registration_numbers', 'mask_passport_numbers', 'mask_addresses',
             'mask_accounts', 'mask_phone_numbers', 'mask_names'],
        )


class RegistrationNumberTests(SimpleTestCase):
    def test_masks_number_forms(self):
        for text in (
            '900101-5123456',
            '900101 - 5123456',
            '90 0101 -51 23456',
            '900101 5123456',
            '900101–5123456',
            '9001015123456',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), '******-*******')

    def test_masks_number_split_across_lines(self):
        for text in ('900101-\n5123456', '900101\n-5123456', '900101 -\n 5123456'):
            with self.subTest(text=text):
                self.assertEqual(masked(f'외국인등록번호: {text}\n과세연도'), '외국인등록번호: ******-*******\n과세연도')

    def test_does_not_join_numbers_on_separate_lines_without_dash(self):
        self.assertEqual(masked('123456\n7654321'), '123456\n7654321')

    def test_masks_value_on_next_line_of_label(self):
        """표 형식 문서: 라벨 다음 줄의 번호."""
        self.assertEqual(masked('외국인등록번호\n900101-5123456'), '외국인등록번호\n******-*******')

    def test_masks_misread_digits_after_label(self):
        """숫자 일부를 글자로 잘못 읽어 패턴에 맞지 않아도 라벨 뒤 값은 가린다."""
        for text in ('외국인등록번호\n9OO1O1-5123456', '외국인등록번호: 9OO1O1-512345б'):
            with self.subTest(text=text):
                self.assertIn('******-*******', masked(text))
                self.assertNotIn('5123456', masked(text))

    def test_thirteen_digit_notice_number_is_also_masked(self):
        # 등록번호와 구분할 수 없어 함께 가린다는 W2 결정을 고정한다.
        self.assertEqual(masked('고지서 번호: 2026083112345'), '고지서 번호: ******-*******')

    def test_keeps_other_numbers(self):
        for text in (
            '4420012026080001234',
            '0020261112345678',
            '790-1234-5678-90',
            '123456-78-901234',
            '041-000-1234',
            '2026년 8월 31일',
            '6,250원',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_next_line_label_is_not_masked(self):
        """라벨 다음 줄이 또 다른 라벨이면 값이 빈칸이다."""
        self.assertEqual(masked('외국인등록번호\n과세연도\n2026년'), '외국인등록번호\n과세연도\n2026년')


class PassportNumberTests(SimpleTestCase):
    def test_masks_labeled_passport_number(self):
        for text, expected in (
            ('여권번호: M12345678', '여권번호: [여권번호 가림]'),
            ('여권 번호 C01234567', '여권 번호 [여권번호 가림]'),
            ('Passport No. B1234567', 'Passport No. [여권번호 가림]'),
            ('여권번호\nM12345678', '여권번호\n[여권번호 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_keeps_passport_words_without_number(self):
        for text in ('2. 여권 사본', '여권번호를 어디에 적나요?'):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)


class AddressTests(SimpleTestCase):
    def test_keeps_region_and_masks_detail(self):
        for text, expected in (
            ('주소: 충청남도 아산시 배방읍 가상로 123, 101동 1001호', '주소: 충청남도 아산시 [주소 가림]'),
            ('주소: 충청남도 천안시 서북구 예시로 77, 202호', '주소: 충청남도 천안시 서북구 [주소 가림]'),
            ('주소: 아산시 배방읍 가상로 123', '주소: 아산시 [주소 가림]'),
            ('거주지: 부산 해운대구 우동 1234', '거주지: 부산 해운대구 [주소 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_masks_spaced_label(self):
        self.assertEqual(masked('주 소: 충청남도 천안시 서북구 예시로 77, 202호'), '주 소: 충청남도 천안시 서북구 [주소 가림]')

    def test_masks_misread_label(self):
        """sample5: "주소:"가 "족소:"로, 아산시가 마산시로 잘못 읽혔다. 잘못 읽힌 지명은 그대로 둔다."""
        self.assertEqual(
            masked('족소: 충청남도 마산시 배방읍 가상로 m. 2018 1001호'),
            '족소: 충청남도 마산시 [주소 가림]',
        )

    def test_masks_label_without_colon(self):
        """sample3 3쪽: 콜론 없는 "주소 충청남도…"."""
        self.assertEqual(
            masked('주소 충청남도 천안시 서북구 예시로 77, 202호'),
            '주소 충청남도 천안시 서북구 [주소 가림]',
        )

    def test_masks_value_on_next_line_of_label(self):
        self.assertEqual(
            masked('주소\n충청남도 아산시 배방읍 가상로 123\n세액'),
            '주소\n충청남도 아산시 [주소 가림]\n세액',
        )

    def test_masks_detail_on_next_line_after_region(self):
        """시·군·구 뒤에서 줄이 바뀌면 다음 줄의 상세주소를 가린다."""
        self.assertEqual(masked('주소: 충청남도 아산시\n배방읍 가상로 123\n세액'), '주소: 충청남도 아산시\n[주소 가림]\n세액')

    def test_masks_whole_value_when_region_not_found(self):
        for text in ('주소: 테스트 주소', '주소: 아산시배방읍 가상로 123', '주소: 배방읍 가상로 123'):
            with self.subTest(text=text):
                self.assertEqual(masked(text), '주소: [주소 가림]')

    def test_masks_unlabeled_address_line(self):
        self.assertEqual(
            masked('제주특별자치도 서귀포시 성산읍 일출로 284-12'),
            '제주특별자치도 서귀포시 [주소 가림]',
        )

    def test_keeps_sentences_about_address(self):
        for text in (
            '주소 변경은 어떻게 하나요?',
            '주소가 바뀌면 14일 안에 신고하세요.',
            '모든 이름·번호·주소는 가짜입니다.',
            '주소: 충청남도 아산시',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_keeps_agency_address(self):
        for text in (
            '제출처: 충청남도 아산시 번영로 224 아산시청 세무과',
            '문의: 천안시 서북구 번대로 123 국민건강보험공단 천안지사',
            '기관 주소: 충청남도 아산시 번영로 224',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_masks_personal_address_with_agency_word_in_road_name(self):
        """도로명(시청로)에 기관 단어가 있어도 개인 주소는 가린다."""
        for text, expected in (
            ('주소: 충청남도 아산시 시청로 123, 101동 1001호', '주소: 충청남도 아산시 [주소 가림]'),
            ('충청남도 아산시 시청로 123, 101동 1001호', '충청남도 아산시 [주소 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_masks_detail_after_region_on_next_value_line(self):
        """라벨 다음 줄이 시·군·구뿐이면 그다음 줄의 상세주소를 가린다."""
        result = mask_personal_info('주소\n충청남도 아산시\n배방읍 가상로 123')

        self.assertEqual(result['masked_text'], '주소\n충청남도 아산시\n[주소 가림]')
        self.assertEqual(result['masked_count'], 1)

    def test_masks_detail_continued_on_next_lines(self):
        """상세주소가 다음 줄로 이어지면 다음 필드 전까지 함께 가린다. 같은 주소라 1개로 센다."""
        result = mask_personal_info('주소: 충청남도 아산시 배방읍 가상로 123\n101동 1001호\n세액')

        self.assertEqual(result['masked_text'], '주소: 충청남도 아산시 [주소 가림]\n[주소 가림]\n세액')
        self.assertEqual(result['masked_count'], 1)

    def test_date_line_after_address_is_not_detail(self):
        text = '주소: 충청남도 아산시 배방읍 가상로 123\n2026년 8월 31일'

        self.assertEqual(masked(text), '주소: 충청남도 아산시 [주소 가림]\n2026년 8월 31일')


class NameTests(SimpleTestCase):
    def test_masks_name_on_next_line_with_english_name(self):
        """sample1: 라벨 다음 줄의 이름과 괄호 안 영문 이름."""
        self.assertEqual(masked('납세자\n응우옌 반 안 (NGUYEN VAN AN)'), '납세자\n[이름 가림]')

    def test_masks_name_after_labels(self):
        for label in ('납세자', '납부자', '가입자', '성명', '이름', '신청인', '신청인 성명'):
            with self.subTest(label=label):
                self.assertEqual(masked(f'{label}: 스리 라하유'), f'{label}: [이름 가림]')
                self.assertEqual(masked(f'{label}\n스리 라하유'), f'{label}\n[이름 가림]')

    def test_masks_misread_name(self):
        """sample5: 흐린 문서에서 이름이 잘못 읽혀도 라벨 다음 줄 값 전체를 가린다."""
        self.assertEqual(masked('납세자\n응우연변면 INGUYEN VAN AN)'), '납세자\n[이름 가림]')

    def test_masks_same_name_elsewhere(self):
        text = '가입자\n스리 라하유 (SRI RAHAYU)\n스리 라하유 님께 안내드립니다.\nDear SRI RAHAYU'

        self.assertEqual(masked(text), '가입자\n[이름 가림]\n[이름 가림] 님께 안내드립니다.\nDear [이름 가림]')

    def test_keeps_table_headings_and_sentences(self):
        for text in (
            '신청인 정보',
            '이름 변경은 어떻게 하나요?',
            '납세자는 기한까지 납부해야 합니다.',
            '납세자 본인이 방문해야 합니다',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_next_line_label_is_not_treated_as_name(self):
        self.assertEqual(masked('납세자\n외국인등록번호\n900101-5123456'), '납세자\n외국인등록번호\n******-*******')

    def test_heading_then_label_then_name(self):
        """sample3 3쪽: "신청인 정보" 다음 "성명", 그다음 이름."""
        self.assertEqual(masked('신청인 정보\n성명\n스리 라하유'), '신청인 정보\n성명\n[이름 가림]')

    def test_masks_repeated_two_letter_name(self):
        text = '성명: 이안\n이안 님께 안내드립니다.\n이안님 확인 부탁드립니다.'

        self.assertEqual(masked(text), '성명: [이름 가림]\n[이름 가림] 님께 안내드립니다.\n[이름 가림]님 확인 부탁드립니다.')

    def test_two_letter_name_inside_other_word_is_kept(self):
        self.assertEqual(masked('성명: 이안\n보이안경을 챙기세요.'), '성명: [이름 가림]\n보이안경을 챙기세요.')

    def test_masks_name_followed_by_other_label_on_same_line(self):
        """같은 줄의 다른 항목이 먼저 가려져도 이름은 이름대로 가린다."""
        self.assertEqual(
            masked('성명: 홍길동 연락처: 010-0000-4321'),
            '성명: [이름 가림] 연락처: [전화번호 가림]',
        )


class PhoneNumberTests(SimpleTestCase):
    def test_masks_mobile_numbers_anywhere(self):
        for text in ('010-0000-4321', '01000004321', '010 0000 4321', '+82-10-0000-4321', '연락 주실 곳 010-0000-4321'):
            with self.subTest(text=text):
                self.assertIn('[전화번호 가림]', masked(text))
                self.assertNotIn('4321', masked(text))

    def test_masks_labeled_landline(self):
        for text, expected in (
            ('연락처: 041-555-1234', '연락처: [전화번호 가림]'),
            ('연락처\n041-555-1234', '연락처\n[전화번호 가림]'),
            ('전화번호 02-555-1234', '전화번호 [전화번호 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_keeps_agency_phone_numbers(self):
        for text in (
            '문의: 아산시청 세무과 041-000-1234',
            '문의: 국민건강보험공단 천안지사 041-000-5678',
            '연락처: 아산시청 세무과 041-000-1234',
            '1577-1000',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)


class AccountNumberTests(SimpleTestCase):
    def test_masks_personal_accounts(self):
        for text, expected in (
            ('출금 계좌\n123-456789-01234', '출금 계좌\n[계좌 가림]'),
            ('출금계좌: 하나은행 123-456789-01234', '출금계좌: 하나은행 [계좌 가림]'),
            ('환급 계좌: 국민은행 123456-78-901234', '환급 계좌: 국민은행 [계좌 가림]'),
            ('계좌번호 110-123-456789', '계좌번호 [계좌 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_masks_personal_account_with_holder_name(self):
        """예금주 표기만으로 기관 계좌로 보지 않는다."""
        for text in (
            '환급 계좌: 하나은행 123-456789-01234 (예금주: 홍길동)',
            '출금 계좌: 하나은행 123-456789-01234 예금주 홍길동',
            '계좌번호: 하나은행 123-456789-01234 (예금주: 홍길동)',
        ):
            with self.subTest(text=text):
                self.assertIn('[계좌 가림]', masked(text))
                self.assertNotIn('123-456789-01234', masked(text))

    def test_keeps_payment_accounts(self):
        for text in (
            '가상계좌\n국민은행 123456-78-901234',
            '가상계좌\n농협 790-1234-5678-90 (예금주: 아산시)',
            '계좌번호: 농협 790-1234-5678-90 (예금주: 아산시)',
            '출금 은행\n하나은행',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)


class KeepGuidanceInfoTests(SimpleTestCase):
    """해석·검증에 필요한 기관·납부 정보는 가리지 않는다."""

    def test_keeps_guidance_info_in_notice(self):
        result = masked(NOTICE)

        for kept in (
            '아산시\n',                                # 기관명
            '아산시청 세무과',                          # 기관·부서
            '041-000-1234',                            # 기관 문의 전화번호
            '국민건강보험공단 천안지사 민원실',          # 제출처
            '농협 790-1234-5678-90 (예금주: 아산시)',   # 납부용 가상계좌
            '4420012026080001234',                     # 전자납부번호
            '6,250원',                                 # 금액
            '2026년 8월 31일',                          # 납부기한
            '주민세(개인분)',                           # 세목
            '납세고지서',                               # 문서 종류
            '충청남도 아산시 [주소 가림]',               # 본인 확인용 시·군·구
        ):
            with self.subTest(kept=kept):
                self.assertIn(kept, result)

    def test_hides_personal_info_in_notice_and_application(self):
        result = masked(NOTICE + '\n' + APPLICATION)

        for hidden in (
            '응우옌', 'NGUYEN', '900101', '5123456', '배방읍', '가상로 123', '1001호',
            '라하유', '950315', '010-0000-4321', '123-456789-01234', '예시로 77', '202호',
        ):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, result)

    def test_keeps_application_dates_and_bank(self):
        result = masked(APPLICATION)

        for kept in ('2026년 11월 20일', '하나은행', '신청인 정보', '보험료 자동이체 신청서'):
            with self.subTest(kept=kept):
                self.assertIn(kept, result)


class ChatbotBoundaryTests(SimpleTestCase):
    """챗봇(prepare_ai_payload)도 같은 함수를 쓴다. 기존 계약과 질문 문장을 지킨다."""

    def test_existing_chatbot_expectations(self):
        prepared = ai_client.prepare_ai_payload({
            'question': '990101-5000000 / 010-0000-0000',
            'history': [{'answer': '주소: 테스트 주소'}],
            'original_text': '주소: [주소 가림]',
        })

        self.assertEqual(prepared['question'], '******-******* / [전화번호 가림]')
        self.assertEqual(prepared['history'][0]['answer'], '주소: [주소 가림]')
        self.assertEqual(prepared['original_text'], '주소: [주소 가림]')

    def test_keeps_ordinary_questions(self):
        for question in (
            '주소 변경은 어떻게 하나요?',
            '이름 변경은 어떻게 하나요?',
            '8월 31일까지 6,250원을 안 내면 어떻게 되나요?',
            '가상계좌로 내도 되나요?',
        ):
            with self.subTest(question=question):
                self.assertEqual(ai_client.prepare_ai_payload(question), question)

    def test_masks_personal_info_in_question(self):
        prepared = ai_client.prepare_ai_payload('제 주소는 주소: 충청남도 아산시 배방읍 가상로 123이에요\n성명: 스리 라하유')

        self.assertNotIn('가상로 123', prepared)
        self.assertNotIn('라하유', prepared)


@override_settings(DOCUMENTS_STEP_DELAY=0, OCR_MODE='dummy')
class PipelineMaskingTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(media.cleanup)
        overrides = override_settings(MEDIA_ROOT=media.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        User = get_user_model()
        user = User.objects.create_user(**{User.USERNAME_FIELD: 'owner@example.com'}, password='test-password')
        self.document = Document.objects.create(
            owner=user,
            file=SimpleUploadedFile('notice.png', b'fake-file-content'),
            file_name='notice.png',
            file_type=Document.FileType.IMAGE,
            step=Document.Step.QUEUED,
        )

    def test_saved_original_text_is_masked(self):
        pipeline.process_document(self.document.id)
        self.document.refresh_from_db()

        original_text = self.document.result.original_text
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertEqual(self.document.masked_count, 3)
        self.assertIn('납세자: [이름 가림]', original_text)
        self.assertIn('주소: 충청남도 아산시 [주소 가림]', original_text)
        self.assertIn('문의: 아산시청 세무과 041-000-0000', original_text)
        for hidden in ('응우옌', '900101-5123456', '배방읍'):
            self.assertNotIn(hidden, original_text)

    def test_masking_error_fails_document_at_masking_step(self):
        with (
            mock.patch.object(masking, 'mask_names', side_effect=RuntimeError('규칙 오류')),
            mock.patch.object(masking, 'MASKING_RULES', (masking.mask_names,)),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            pipeline.process_document(self.document.id)
        self.document.refresh_from_db()

        self.assertEqual(self.document.status, Document.Status.FAILED)
        self.assertEqual(self.document.step, Document.Step.MASKING)
        self.assertEqual(self.document.fail_reason, pipeline.FAIL_MESSAGES[Document.Step.MASKING])
        self.assertFalse(Document.objects.filter(id=self.document.id, result__isnull=False).exists())
