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


# 아래 테스트는 실제 규칙(masking.py)을 확인한다. 기본값 dummy는 MaskModeTests에서 확인한다.
rules_mode = override_settings(MASK_MODE='rules')


# 로컬 .env에 OCR_MODE=clova가 있어도 dummy 마스킹 확인이 안전장치에 막히지 않게 고정한다.
# 메서드에 지정한 OCR_MODE='clova'가 이 설정보다 우선한다.
@override_settings(OCR_MODE='dummy')
class MaskModeTests(SimpleTestCase):
    def test_default_is_dummy_regex(self):
        """MASK_MODE가 없으면 dummy: 등록번호와 "주소:" 줄만 가린다(기존 동작)."""
        with override_settings():
            from django.conf import settings

            del settings.MASK_MODE
            result = mask_personal_info(NOTICE)

        self.assertIn('******-*******', result['masked_text'])
        self.assertIn('주소: [주소 가림]', result['masked_text'])
        self.assertIn('응우옌 반 안', result['masked_text'])
        self.assertEqual(result['masked_count'], 2)

    @override_settings(MASK_MODE='dummy')
    def test_dummy_mode(self):
        self.assertEqual(mask_personal_info(NOTICE)['masked_count'], 2)

    @rules_mode
    def test_rules_mode_uses_masking_module(self):
        self.assertEqual(mask_personal_info(NOTICE), masking.mask_text(NOTICE))

    @override_settings(MASK_MODE='rule')
    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            mask_personal_info(NOTICE)

    @override_settings(MASK_MODE='dummy', OCR_MODE='clova')
    def test_dummy_masking_with_real_ocr_raises(self):
        """실제 문서를 읽는데 더미 마스킹이면 설정 누락으로 보고 처리하지 않는다."""
        with self.assertRaises(ValueError):
            mask_personal_info(NOTICE)

    @override_settings(MASK_MODE='rules', OCR_MODE='clova')
    def test_rules_masking_with_real_ocr_is_allowed(self):
        self.assertEqual(mask_personal_info(NOTICE)['masked_count'], 3)


@rules_mode
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


@rules_mode
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

    def test_labeled_other_number_is_kept(self):
        """이름표로 보아 등록번호가 아닌 13자리 번호는 가리지 않는다(2026-11-14 결정)."""
        for text in (
            '고지서 번호: 2026083112345', '고지서 번호:2026083112345', '고지서번호 2026083112345',
            '고지번호: 2026083112345', '납부번호: 2026083112345', '전자납부번호: 2026083112345',
            '전자 납부 번호 2026083112345', '문서번호: 2026083112345', '관리번호: 2026083112345',
            '접수번호: 2026083112345', '■ 고지번호: 2026083112345',
            '2026년 8월 정기분 · 고지번호 2026083112345',
            # 표 형식: 이름표만 있는 줄의 다음 줄
            '고지서 번호\n2026083112345', '납부번호:\n2026083112345', '■ 문서번호\n2026083112345',
            # 숫자 사이가 띄어져 읽힌 경우
            '고지번호: 202608 3112345',
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], text)
                self.assertEqual(result['masked_count'], 0)

    def test_thirteen_digits_without_other_label_are_masked(self):
        """이름표가 없거나, 목록에 없는 이름표이거나, 이름표와 떨어져 있으면 지금처럼 가린다."""
        for text, expected in (
            ('2026083112345', '******-*******'),
            ('번호: 2026083112345', '번호: ******-*******'),
            ('등록번호: 2026083112345', '등록번호: ******-*******'),
            ('회원번호: 2026083112345', '회원번호: ******-*******'),
            ('고지번호는 아래와 같습니다 2026083112345', '고지번호는 아래와 같습니다 ******-*******'),
            ('고지번호 안내\n2026083112345', '고지번호 안내\n******-*******'),
            ('고지번호\n\n2026083112345', '고지번호\n\n******-*******'),
            ('고지번호\n납부기한\n2026083112345', '고지번호\n납부기한\n******-*******'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_registration_label_wins_over_other_label(self):
        for text, expected in (
            ('외국인등록번호: 9001015123456', '외국인등록번호: ******-*******'),
            ('외국인등록번호\n9001015123456', '외국인등록번호\n******-*******'),
            ('고지번호: 2026083112345 외국인등록번호: 9001015123456',
             '고지번호: 2026083112345 외국인등록번호: ******-*******'),
            ('고지번호 2026083112345 9001015123456', '고지번호 2026083112345 ******-*******'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_hyphenated_number_is_masked_even_after_other_label(self):
        """하이픈이 있거나 줄이 바뀐 6자리-7자리는 등록번호 모양이라 이름표와 관계없이 가린다."""
        for text, expected in (
            ('고지번호: 900101-5123456', '고지번호: ******-*******'),
            ('관리번호\n900101-5123456', '관리번호\n******-*******'),
            ('문서번호: 900101 - 5123456', '문서번호: ******-*******'),
            ('고지번호: 900101-\n5123456', '고지번호: ******-*******'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_kept_other_number_is_stable_when_masked_twice(self):
        text = '고지서 번호: 2026083112345\n외국인등록번호: 9001015123456\n수신: 홍길동'
        once = masked(text)

        self.assertEqual(once, '고지서 번호: 2026083112345\n외국인등록번호: ******-*******\n수신: [이름 가림]')
        self.assertEqual(masked(once), once)

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


@rules_mode
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


@rules_mode
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

    def test_building_name_line_after_address_is_masked(self):
        """주소 다음 줄이 건물 이름으로 시작해도 동·호·층 숫자가 있으면 상세주소다."""
        for detail in (
            '가상아파트 101동 1001호', '견본빌라 302호', '행복오피스텔 5층 501호', '가상아파트 101-1001호',
            # OCR이 띄어쓰기를 빼서 건물 이름과 숫자가 붙은 줄(CodeRabbit)
            '견본빌라302호', '가상아파트101동1001호', '가상아파트 101동1001호', '행복오피스텔5층 501호',
        ):
            for address in ('주소: 아산시 배방읍 가상로 123', '주소\n충청남도 아산시 배방읍 가상로 123'):
                with self.subTest(detail=detail, address=address):
                    result = mask_personal_info(f'{address}\n{detail}\n세액 6,250원')

                    self.assertNotIn(detail, result['masked_text'])
                    self.assertTrue(result['masked_text'].endswith('[주소 가림]\n[주소 가림]\n세액 6,250원'))
                    self.assertEqual(result['masked_count'], 1)
                    self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_building_line_after_unlabeled_address_is_masked(self):
        """주소 이름표 없이 찾은 주소도 다음 줄이 동·호·층 모양이면 함께 가린다."""
        for detail in ('견본빌라 302호', '견본빌라302호', '101동 1001호'):
            with self.subTest(detail=detail):
                result = mask_personal_info(f'충청남도 아산시 배방읍 가상로 123\n{detail}\n세액 6,250원')

                self.assertEqual(result['masked_text'], '충청남도 아산시 [주소 가림]\n[주소 가림]\n세액 6,250원')
                self.assertEqual(result['masked_count'], 1)
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_other_line_after_unlabeled_address_is_kept(self):
        # 이름표가 없는 주소 뒤에서는 줄 전체가 동·호·층 모양일 때만 가린다.
        for following in ('우리 시는 납부 편의를 위해 노력합니다.', '납부기한 2026년 8월 31일', '시청 본관 2층 세무과로 방문해 주세요.'):
            with self.subTest(following=following):
                text = f'충청남도 아산시 배방읍 가상로 123\n{following}'

                self.assertEqual(masked(text), f'충청남도 아산시 [주소 가림]\n{following}')

    def test_amount_or_other_field_line_after_address_is_not_detail(self):
        """동·호·층 숫자가 있어도 금액이나 다른 항목이 적힌 줄은 주소로 보지 않는다."""
        for following in (
            '민원실 1층 수수료 3,000원', '문의: 시청 2층 세무과', '2026년 8월 31일',
            # 층이 적힌 기한·안내 문장은 줄 전체가 주소 모양이 아니다(CodeRabbit).
            '제출기한 2026년 8월 31일 (본관 1층)', '시청 본관 2층 세무과로 방문해 주시기 바랍니다.',
            '접수 장소 시청 별관 3층 민원실',
        ):
            with self.subTest(following=following):
                text = f'주소: 충청남도 아산시 배방읍 가상로 123\n{following}'

                self.assertEqual(masked(text), f'주소: 충청남도 아산시 [주소 가림]\n{following}')


@rules_mode
class NameTests(SimpleTestCase):
    def test_name_followed_by_masked_number_is_masked(self):
        """이름 뒤에 붙은 번호가 먼저 가려져도 이름을 건너뛰지 않는다. 다른 곳에 다시 나온 이름도 가린다."""
        for text, expected in (
            ('성명: 홍길동 010-1234-5678', '성명: [이름 가림] [전화번호 가림]'),
            ('성명: 홍길동 900101-5234567', '성명: [이름 가림] ******-*******'),
            ('성명 홍길동 010-1234-5678', '성명 [이름 가림] [전화번호 가림]'),
            ('납세자\n홍길동 010-1234-5678', '납세자\n[이름 가림] [전화번호 가림]'),
            ('수신: 홍길동 010-1234-5678', '수신: [이름 가림] [전화번호 가림]'),
            ('수신\n홍길동 010-1234-5678', '수신\n[이름 가림] [전화번호 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(f'{text}\n홍길동 님께 안내드립니다.')

                self.assertEqual(result['masked_text'], f'{expected}\n[이름 가림] 님께 안내드립니다.')
                # 번호 하나, 이름표 뒤의 이름 하나, 아래 줄에 다시 나온 이름 하나
                self.assertEqual(result['masked_count'], 3)
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_latin_name_after_masked_number_is_masked(self):
        """가려진 번호 뒤에 괄호로 적힌 영문 이름도 가리고, 다른 곳에 다시 나오면 함께 가린다."""
        for text, expected in (
            ('성명: 홍길동 010-1234-5678 (HONG GILDONG)', '성명: [이름 가림] [전화번호 가림]'),
            ('성명: 홍길동 900101-5234567 (HONG GILDONG)', '성명: [이름 가림] ******-*******'),
            ('납세자\n홍길동 010-1234-5678 (HONG GILDONG)', '납세자\n[이름 가림] [전화번호 가림]'),
            ('수신: 홍길동 010-1234-5678 (HONG GILDONG)', '수신: [이름 가림] [전화번호 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(f'{text}\nHONG GILDONG 님께 안내드립니다.')

                self.assertEqual(result['masked_text'], f'{expected}\n[이름 가림] 님께 안내드립니다.')
                self.assertNotIn('HONG', result['masked_text'])
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_name_on_next_line_followed_by_other_field_is_masked(self):
        """이름표 다음 줄에 이름과 다른 항목이 함께 있어도 이름만 가리고, 다시 나온 이름도 가린다(CodeRabbit)."""
        for text, expected in (
            ('성명\n홍길동 연락처: 010-1234-5678', '성명\n[이름 가림] 연락처: [전화번호 가림]'),
            ('납세자\n홍길동 여권번호: M12345678', '납세자\n[이름 가림] 여권번호: [여권번호 가림]'),
            ('수신\n홍길동 연락처: 010-1234-5678', '수신\n[이름 가림] 연락처: [전화번호 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(f'{text}\n홍길동 귀하')

                self.assertEqual(result['masked_text'], f'{expected}\n[이름 가림] 귀하')
                self.assertEqual(result['masked_count'], 3)
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_separate_english_name_field_is_masked(self):
        """영문명처럼 이름을 따로 적는 칸도 이름이다. 이름 뒤에서 나뉜 항목이어도 가린다(CodeRabbit)."""
        for text, expected in (
            ('성명\n홍길동 영문명: HONG GILDONG', '성명\n[이름 가림] 영문명: [이름 가림]'),
            ('성명: 홍길동 영문명: HONG GILDONG', '성명: [이름 가림] 영문명: [이름 가림]'),
            ('성명: 홍길동\n영문명: HONG GILDONG', '성명: [이름 가림]\n영문명: [이름 가림]'),
            ('성명\n홍길동\n영문 성명\nHONG GILDONG', '성명\n[이름 가림]\n영문 성명\n[이름 가림]'),
            ('성명(한글): 홍길동\n성명(영문): HONG GILDONG', '성명(한글): [이름 가림]\n성명(영문): [이름 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(f'{text}\nHONG GILDONG 님, 홍길동 님께 안내드립니다.')

                self.assertEqual(result['masked_text'], f'{expected}\n[이름 가림] 님, [이름 가림] 님께 안내드립니다.')
                self.assertNotIn('HONG', result['masked_text'])
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_two_name_fields_on_one_line_are_split(self):
        """영문 성명과 한글 성명이 한 줄에 있어도 이름표에서 나눠 각각 가리고, 다시 나온 이름도 가린다(CodeRabbit)."""
        for text, expected in (
            ('영문 성명: HONG GILDONG 한글 성명: 홍길동', '영문 성명: [이름 가림] 한글 성명: [이름 가림]'),
            ('한글 이름: 홍길동 영문 이름: HONG GILDONG', '한글 이름: [이름 가림] 영문 이름: [이름 가림]'),
            ('성명(한글): 홍길동 성명(영문): HONG GILDONG', '성명(한글): [이름 가림] 성명(영문): [이름 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(f'{text}\nHONG GILDONG 님, 홍길동 님께 안내드립니다.')

                self.assertEqual(result['masked_text'], f'{expected}\n[이름 가림] 님, [이름 가림] 님께 안내드립니다.')
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_name_on_next_line_followed_by_unmasked_personal_field_masks_whole_line(self):
        """이름 다음 줄에 가릴 규칙이 없는 개인 항목(이메일·생년월일·국적)이 이어지면 줄 전체를 가린다."""
        for following in (
            '홍길동 이메일: gildong@example.com', '홍길동 생년월일: 1990년 1월 1일', '홍길동 국적: 베트남',
            '홍길동 연락처: 010-1234-5678 이메일: gildong@example.com',
        ):
            with self.subTest(following=following):
                result = mask_personal_info(f'성명\n{following}\n홍길동 귀하')

                self.assertEqual(result['masked_text'], '성명\n[이름 가림]\n[이름 가림] 귀하')
                self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_names_in_dropped_fields_are_still_masked_elsewhere(self):
        """줄 전체를 가릴 때 버리는 뒤 항목 안의 이름(영문명)도 다른 곳에서 가린다(CodeRabbit)."""
        text = '성명\n홍길동 영문명: HONG GILDONG 이메일: gildong@example.com\nHONG GILDONG 귀하'
        result = mask_personal_info(text)

        self.assertEqual(result['masked_text'], '성명\n[이름 가림]\n[이름 가림] 귀하')
        self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_agency_recipient_with_unmasked_personal_field_drops_the_field(self):
        """받는 사람이 기관이어도 뒤에 이어진 영문명·이메일은 남기지 않는다(CodeRabbit)."""
        text = '수신\n아산시장 영문명: HONG GILDONG 이메일: gildong@example.com'
        result = mask_personal_info(text)

        self.assertEqual(result['masked_text'], '수신\n아산시장 [이름 가림]')
        self.assertEqual(result['masked_count'], 1)
        self.assertEqual(mask_personal_info(result['masked_text'])['masked_text'], result['masked_text'])

    def test_agency_recipient_on_next_line_keeps_name_and_masks_other_field(self):
        text = '수신\n아산시장 연락처: 010-1234-5678'

        self.assertEqual(masked(text), '수신\n아산시장 연락처: [전화번호 가림]')

    def test_agency_recipient_followed_by_masked_number_is_kept(self):
        """받는 사람이 기관이면 뒤의 번호만 가려지고 기관 이름은 남는다."""
        for text, expected in (
            ('수신: 아산시장 010-1234-5678', '수신: 아산시장 [전화번호 가림]'),
            ('수신\n아산시장 010-1234-5678', '수신\n아산시장 [전화번호 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_masks_name_on_next_line_with_english_name(self):
        """sample1: 라벨 다음 줄의 이름과 괄호 안 영문 이름."""
        self.assertEqual(masked('납세자\n응우옌 반 안 (NGUYEN VAN AN)'), '납세자\n[이름 가림]')

    def test_recipient_label_name_is_masked(self):
        """수신·받는 사람·대상자 라벨 뒤의 이름을 가린다."""
        for text, expected in (
            ('수신: 홍길동', '수신: [이름 가림]'),
            ('수신 : 홍길동', '수신 : [이름 가림]'),
            ('수신인: 홍길동', '수신인: [이름 가림]'),
            ('수신자: 응우옌 반 안 (NGUYEN VAN AN)', '수신자: [이름 가림]'),
            ('받는 사람: 홍길동', '받는 사람: [이름 가림]'),
            ('받는사람: 홍길동', '받는사람: [이름 가림]'),
            ('받는 분: 홍길동', '받는 분: [이름 가림]'),
            ('대상자: 홍길동', '대상자: [이름 가림]'),
            ('■ 수신: 홍길동 귀하', '■ 수신: [이름 가림] 귀하'),
            ('수신: 홍길동 납부기한: 2026년 8월 31일', '수신: [이름 가림] 납부기한: 2026년 8월 31일'),
            # 표 형식: 라벨만 있는 줄의 다음 줄
            ('수신\n홍길동', '수신\n[이름 가림]'),
            ('대상자\n홍길동', '대상자\n[이름 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_recipient_agency_or_title_is_kept(self):
        """받는 사람 라벨 뒤에 기관이나 직위가 오면 가리지 않는다."""
        for text in (
            '수신: 아산시청', '수신: 아산시청 세무과', '수신: 국민건강보험공단 천안지사', '수신: 아산시장',
            '수신: 법무부장관', '수신: 대전출입국·외국인사무소장', '수신: 수신자 참조', '수신자: 천안세무서장 귀하',
            '수신\n아산시청', '받는 사람: 아산시 배방읍 행정복지센터', '수신: 충청남도 아산시청',
            # 직위 없이 기관 이름만 있는 경우
            '수신: 법무부', '수신: 고용노동부', '수신: 아산경찰서', '수신: 천안교육청', '수신\n법무부',
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], text)
                self.assertEqual(result['masked_count'], 0)

    def test_recipient_word_without_colon_is_not_a_name_label(self):
        """'수신 거부', '대상자 확인'처럼 콜론 없이 이어진 낱말은 이름으로 보지 않는다."""
        for text in (
            '수신 거부', '수신 동의 여부', '수신자 부담', '대상자 확인', '대상자 선정 기준', '받는 사람 주소',
            '대상자는 기한 내에 신청해야 합니다.', '수신 홍길동',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_recipient_name_is_masked_elsewhere_without_honorific(self):
        """받는 사람 이름 뒤의 '귀하'는 떼고, 다른 곳에 나온 같은 이름도 가린다."""
        text = '수신: 홍길동 귀하\n홍길동 님께 안내드립니다.\n문의: 아산시청 세무과'

        self.assertEqual(masked(text), '수신: [이름 가림] 귀하\n[이름 가림] 님께 안내드립니다.\n문의: 아산시청 세무과')

    def test_recipient_name_with_title_or_agency_is_masked(self):
        """이름과 기관·직위가 함께 있으면 이름만 가리고 기관·직위는 남긴다."""
        for text, expected in (
            ('수신: 홍길동 센터장 귀하', '수신: [이름 가림] 센터장 귀하'),
            ('수신: 홍길동 소장', '수신: [이름 가림] 소장'),
            ('수신: 행정복지센터장 홍길동', '수신: 행정복지센터장 [이름 가림]'),
            ('수신: 아산시청 홍길동 주무관', '수신: 아산시청 [이름 가림] 주무관'),
            ('수신: 아산시청 홍길동 조사원', '수신: 아산시청 [이름 가림]'),
            ('수신: 아산시청 세무과 홍길동', '수신: 아산시청 세무과 [이름 가림]'),
            ('수신: 법무부 홍길동', '수신: 법무부 [이름 가림]'),
            ('수신: 홍길동 아산시청', '수신: [이름 가림] 아산시청'),
            ('대상자: 응우옌 반 안 (NGUYEN VAN AN) 귀하', '대상자: [이름 가림] 귀하'),
            # 표 형식: 라벨만 있는 줄의 다음 줄
            ('수신\n홍길동 센터장 귀하', '수신\n[이름 가림] 센터장 귀하'),
            ('수신\n아산시청 홍길동', '수신\n아산시청 [이름 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], expected)
                self.assertEqual(result['masked_count'], 1)
                self.assertEqual(masked(expected), expected)

    def test_recipient_name_with_title_is_masked_elsewhere(self):
        text = '수신: 홍길동 센터장 귀하\n홍길동 님께 안내드립니다.'

        self.assertEqual(masked(text), '수신: [이름 가림] 센터장 귀하\n[이름 가림] 님께 안내드립니다.')

    def test_recipient_name_attached_to_title_is_masked(self):
        """이름과 직위를 붙여 쓴 낱말은 이름만 가린다. 기관의 직위는 그대로 둔다."""
        for text, expected in (
            ('수신: 홍길동주무관', '수신: [이름 가림] 주무관'),
            ('수신: 홍길동센터장', '수신: [이름 가림] 센터장'),
            ('수신: 홍길동센터장 귀하', '수신: [이름 가림] 센터장 귀하'),
            ('수신: 아산시청 홍길동과장', '수신: 아산시청 [이름 가림] 과장'),
            ('수신: 김과장', '수신: [이름 가림] 과장'),
            ('수신: 김진도지사', '수신: [이름 가림] 도지사'),
            # 부서 이름 목록에 없는 낱말이 직위 앞에 붙으면 이름으로 본다.
            ('수신: 지원팀장', '수신: [이름 가림] 팀장'),
            ('수신: 아산시청 민원팀 홍길동과장', '수신: 아산시청 민원팀 [이름 가림] 과장'),
            ('수신\n홍길동주무관', '수신\n[이름 가림] 주무관'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], expected)
                self.assertEqual(result['masked_count'], 1)
                self.assertEqual(masked(expected), expected)

    def test_agency_title_is_kept(self):
        for text in (
            '수신: 아산시장', '수신: 서울특별시장', '수신: 홍성군수', '수신: 강남구청장', '수신: 충청남도지사',
            '수신: 충남도지사', '수신: 법무부장관', '수신: 천안세무서장', '수신: 아산경찰서장', '수신: 아산소방서장',
            '수신: 행정복지센터장', '수신: 천안지사장', '수신: 국민건강보험공단이사장', '수신: 세무과장',
            '수신: 대전출입국·외국인사무소장', '수신: 센터장', '수신: 주무관',
            # 부서 이름에 직위나 과·팀이 붙은 낱말
            '수신: 민원팀장', '수신: 총무과장', '수신: 복지정책과장', '수신: 아산시청 민원팀장',
            '수신: 아산시청 민원팀', '수신: 법무부 이민정보과', '수신: 아산시청 총무과 민원팀장',
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], text)
                self.assertEqual(result['masked_count'], 0)

    def test_name_attached_to_title_is_masked_elsewhere(self):
        text = '수신: 홍길동주무관\n홍길동 님께 안내드립니다.'

        self.assertEqual(masked(text), '수신: [이름 가림] 주무관\n[이름 가림] 님께 안내드립니다.')

    def test_recipient_name_with_listed_title_is_masked_elsewhere(self):
        """직위는 남기고 이름만 기억해, 다른 곳에 나온 같은 이름도 가린다."""
        text = '수신: 아산시청 홍길동 주무관\n홍길동 님께 안내드립니다.'

        self.assertEqual(masked(text), '수신: 아산시청 [이름 가림] 주무관\n[이름 가림] 님께 안내드립니다.')

    def test_attached_honorific_is_removed_before_matching_elsewhere(self):
        """'홍길동님'처럼 높임말을 붙여 쓴 이름도 다른 곳의 같은 이름을 가린다."""
        for label in ('수신', '성명', '납세자'):
            for written in ('홍길동님', '홍길동씨', '홍길동귀하', '홍길동 님', '홍길동 귀하'):
                with self.subTest(label=label, written=written):
                    result = masked(f'{label}: {written}\n홍길동 님께 안내드립니다.\n문의: 아산시청')

                    self.assertNotIn('홍길동', result)
                    second_line = result.splitlines()[1]
                    self.assertIn('[이름 가림]', second_line)
                    self.assertIn('안내드립니다.', second_line)
                    self.assertTrue(result.endswith('문의: 아산시청'))
                    self.assertEqual(masked(result), result)

    def test_recipient_region_names_are_kept_before_agency(self):
        """실제 지역 이름은 뒤에 기관 이름이 이어지면 남긴다."""
        for text in (
            '수신: 충청남도 아산시청', '수신: 아산시 배방읍 행정복지센터', '수신: 천안시 동남구 보건소',
            '수신: 수원시 영통구청', '수신: 충남 홍성군 보건소', '수신: 서울특별시 강남구 보건소',
            '수신\n천안시 서북구 보건소',
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], text)
                self.assertEqual(result['masked_count'], 0)

    def test_recipient_name_that_looks_like_region_is_masked(self):
        """'구'·'시'·'동'으로 끝나는 이름은 지역 이름으로 보지 않는다. 지역은 뒤에 기관 이름이 올 때만 남긴다."""
        for text, expected in (
            ('수신: 박진구', '수신: [이름 가림]'),
            ('수신: 박진구 센터장', '수신: [이름 가림] 센터장'),
            ('수신: 김민구 귀하', '수신: [이름 가림] 귀하'),
            ('수신: 아산시 박진구', '수신: [이름 가림]'),
            ('수신: 아산시 홍길동 세무과', '수신: [이름 가림] 세무과'),
            # 지역 이름 목록에 없는 낱말은 '도'·'시'·'군'으로 끝나도 이름으로 본다.
            ('수신: 김진도 아산시청', '수신: [이름 가림] 아산시청'),
            ('수신: 남궁도 아산시청', '수신: [이름 가림] 아산시청'),
            ('수신: 이영시 보건소', '수신: [이름 가림] 보건소'),
            ('수신: 박성군 행정복지센터', '수신: [이름 가림] 행정복지센터'),
            # 지역 이름과 같은 낱말도 기관 이름이 이어지지 않으면 가린다(사람 이름일 수 있다).
            ('수신: 강동구', '수신: [이름 가림]'),
            ('수신: 강남구 센터장', '수신: [이름 가림] 센터장'),
            # 읍·면은 지역 이름 바로 뒤에 올 때만 지역으로 본다.
            ('수신: 배방읍 행정복지센터', '수신: [이름 가림] 행정복지센터'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_consecutive_labels_mask_the_value_not_the_label(self):
        """라벨이 연달아 나오면 라벨이 아니라 그 아래 이름을 가린다."""
        for text, expected in (
            ('대상자\n수신인\n홍길동', '대상자\n수신인\n[이름 가림]'),
            ('납세자\n수신인\n홍길동', '납세자\n수신인\n[이름 가림]'),
            ('성명\n대상자\n홍길동', '성명\n대상자\n[이름 가림]'),
            ('수신\n받는 사람\n홍길동', '수신\n받는 사람\n[이름 가림]'),
            ('주소\n수신자\n홍길동', '주소\n수신자\n[이름 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], expected)
                self.assertEqual(result['masked_count'], 1)

    def test_recipient_table_form_in_demo_document_is_unchanged(self):
        """데모 견본2의 모양: '대상자' 다음 줄이 '성명' 라벨이면 그 아래 이름만 가린다."""
        text = '대상자\n성명\n응우옌 반 안 (NGUYEN VAN AN)\n여권번호\nC0000000'

        self.assertEqual(masked(text), '대상자\n성명\n[이름 가림]\n여권번호\n[여권번호 가림]')

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

    def test_masks_other_fields_after_name_on_same_line(self):
        """이름 뒤에 남기는 같은 줄의 계좌·일반 전화·여권번호도 가린다."""
        for text, expected in (
            ('성명: 홍길동 계좌번호: 하나은행 123-456789-01234', '성명: [이름 가림] 계좌번호: 하나은행 [계좌 가림]'),
            ('성명: 홍길동 연락처: 041-555-1234', '성명: [이름 가림] 연락처: [전화번호 가림]'),
            ('성명: 홍길동 여권번호: M12345678', '성명: [이름 가림] 여권번호: [여권번호 가림]'),
        ):
            with self.subTest(text=text):
                result = mask_personal_info(text)

                self.assertEqual(result['masked_text'], expected)
                self.assertEqual(result['masked_count'], 2)
                self.assertEqual(mask_personal_info(expected)['masked_text'], expected)


@rules_mode
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


@rules_mode
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

    def test_agency_holder_of_another_account_does_not_keep_personal_account(self):
        """같은 줄 뒤쪽에 있는 다른 계좌(가상계좌)의 기관 예금주로 앞의 개인 계좌를 남기지 않는다."""
        text = '계좌번호: 하나은행 123-456789-01234 가상계좌: 농협 790-1234-5678-90 예금주: 아산시'

        self.assertEqual(masked(text), '계좌번호: 하나은행 [계좌 가림] 가상계좌: 농협 790-1234-5678-90 예금주: 아산시')

    def test_keeps_payment_accounts(self):
        for text in (
            '가상계좌\n국민은행 123456-78-901234',
            '가상계좌\n농협 790-1234-5678-90 (예금주: 아산시)',
            '계좌번호: 농협 790-1234-5678-90 (예금주: 아산시)',
            '계좌번호: 농협 790-1234-5678-90 예금주: 아산시',
            '계좌번호: 견본은행 000-0000-0000-00 예금주: 국민건강보험공단',
            '출금 은행\n하나은행',
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)


@rules_mode
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

    def test_keeps_following_fields_on_same_line(self):
        """개인정보 값만 가리고 같은 줄에 이어진 기한·금액은 남긴다."""
        for text, expected in (
            ('성명: 홍길동 여권번호: M12345678 납부기한: 2026년 8월 31일',
             '성명: [이름 가림] 여권번호: [여권번호 가림] 납부기한: 2026년 8월 31일'),
            ('여권번호: M12345678 납부기한: 2026년 8월 31일', '여권번호: [여권번호 가림] 납부기한: 2026년 8월 31일'),
            ('외국인등록번호: 9OO1O1-5123456 세액: 6,250원', '외국인등록번호: ******-******* 세액: 6,250원'),
            ('주소: 충청남도 아산시 배방읍 가상로 1 납부기한: 2026년 8월 31일',
             '주소: 충청남도 아산시 [주소 가림] 납부기한: 2026년 8월 31일'),
            ('충청남도 아산시 배방읍 가상로 1 세액: 6,250원', '충청남도 아산시 [주소 가림] 세액: 6,250원'),
            ('성명: 홍길동 납부기한: 2026년 8월 31일 금액: 11,000원', '성명: [이름 가림] 납부기한: 2026년 8월 31일 금액: 11,000원'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)

    def test_masks_personal_info_in_following_fields(self):
        """남기는 뒤 항목에 개인정보가 있으면 그것도 가린다. 기관 번호·기한은 남긴다."""
        for text, expected in (
            ('주소: 충청남도 아산시 배방읍 가상로 1 여권번호: M12345678',
             '주소: 충청남도 아산시 [주소 가림] 여권번호: [여권번호 가림]'),
            ('주소: 충청남도 아산시 배방읍 가상로 1 연락처: 041-555-1234',
             '주소: 충청남도 아산시 [주소 가림] 연락처: [전화번호 가림]'),
            ('주소: 충청남도 아산시 배방읍 가상로 1 성명: 홍길동', '주소: 충청남도 아산시 [주소 가림] 성명: [이름 가림]'),
            ('충청남도 아산시 배방읍 가상로 1 여권번호: M12345678', '충청남도 아산시 [주소 가림] 여권번호: [여권번호 가림]'),
            ('여권번호: M12345678 계좌번호: 하나은행 123-456789-01234',
             '여권번호: [여권번호 가림] 계좌번호: 하나은행 [계좌 가림]'),
            ('외국인등록번호: 9OO1O1-5123456 여권번호: M12345678',
             '외국인등록번호: ******-******* 여권번호: [여권번호 가림]'),
            ('연락처: 041-555-1234 여권번호: M12345678', '연락처: [전화번호 가림] 여권번호: [여권번호 가림]'),
            ('출금 계좌: 하나은행 123-456789-01234 성명: 홍길동', '출금 계좌: 하나은행 [계좌 가림] 성명: [이름 가림]'),
            ('성명: [이름 가림] 여권번호: M12345678', '성명: [이름 가림] 여권번호: [여권번호 가림]'),
            ('주소: 충청남도 아산시 배방읍 가상로 1 여권번호: M12345678 납부기한: 2026년 8월 31일',
             '주소: 충청남도 아산시 [주소 가림] 여권번호: [여권번호 가림] 납부기한: 2026년 8월 31일'),
            ('연락처: 아산시청 세무과 041-000-1234 여권번호: M12345678',
             '연락처: 아산시청 세무과 041-000-1234 여권번호: [여권번호 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)
                self.assertEqual(masked(expected), expected)

    def test_name_found_in_following_field_is_masked_in_other_lines(self):
        """줄 중간의 "성명:"에서 찾은 이름도 문서의 다른 줄에서 가린다."""
        for text, expected in (
            ('연락처: 041-555-1234 성명: 홍길동\n홍길동 님께 안내드립니다.',
             '연락처: [전화번호 가림] 성명: [이름 가림]\n[이름 가림] 님께 안내드립니다.'),
            ('홍길동 님께 안내드립니다.\n여권번호: M12345678 성명: 홍길동',
             '[이름 가림] 님께 안내드립니다.\n여권번호: [여권번호 가림] 성명: [이름 가림]'),
        ):
            with self.subTest(text=text):
                self.assertEqual(masked(text), expected)
                self.assertEqual(masked(expected), expected)

    def test_found_names_do_not_leak_between_calls(self):
        """앞 문서에서 찾은 이름을 다음 문서에 적용하지 않는다."""
        masked('성명: 홍길동')

        self.assertEqual(masked('홍길동 님께 안내드립니다.'), '홍길동 님께 안내드립니다.')

    def test_name_parts_are_not_mistaken_for_next_label(self):
        """이름 조각("반 안")을 다음 항목 라벨로 오해해 이름 일부를 남기지 않는다."""
        for text in ('성명: 응우옌 반 안 납부 기한: 2026년 8월 31일', '성명: 스리 라하유 납부기한: 2026년 8월 31일'):
            with self.subTest(text=text):
                result = masked(text)

                self.assertTrue(result.startswith('성명: [이름 가림] 납부'))
                self.assertIn('2026년 8월 31일', result)

    def test_keeps_application_dates_and_bank(self):
        result = masked(APPLICATION)

        for kept in ('2026년 11월 20일', '하나은행', '신청인 정보', '보험료 자동이체 신청서'):
            with self.subTest(kept=kept):
                self.assertIn(kept, result)


@rules_mode
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


@override_settings(DOCUMENTS_STEP_DELAY=0, OCR_MODE='dummy', MASK_MODE='rules')
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

    def test_dummy_masking_with_real_ocr_fails_document_at_masking_step(self):
        """배포에서 MASK_MODE를 빠뜨리면 약한 마스킹 결과를 저장하지 않고 문서를 실패시킨다."""
        with (
            override_settings(MASK_MODE='dummy', OCR_MODE='clova'),
            mock.patch.object(pipeline, 'run_ocr', return_value={'text': NOTICE, 'confidence': 0.9}),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            pipeline.process_document(self.document.id)
        self.document.refresh_from_db()

        self.assertEqual(self.document.status, Document.Status.FAILED)
        self.assertEqual(self.document.step, Document.Step.MASKING)
        self.assertFalse(Document.objects.filter(id=self.document.id, result__isnull=False).exists())

    def test_unknown_mask_mode_fails_document_at_masking_step(self):
        """MASK_MODE 오타는 약한 마스킹으로 넘어가지 않고 문서를 실패시킨다."""
        with override_settings(MASK_MODE='rule'), self.assertLogs(pipeline.logger, 'ERROR'):
            pipeline.process_document(self.document.id)
        self.document.refresh_from_db()

        self.assertEqual(self.document.status, Document.Status.FAILED)
        self.assertEqual(self.document.step, Document.Step.MASKING)
