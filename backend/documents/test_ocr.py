"""CLOVA OCR 연동 테스트. requests.post를 mock으로 대체해 실제 CLOVA를 호출하지 않는다.

가짜 응답은 W1에서 확인한 실제 응답 구조(images[].fields[])를 따른다.
"""

import base64
import json
import logging
import math
import tempfile
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from .models import Document
from .services import ocr, pipeline
from .services.ocr import (
    OCRError,
    PageLimitOCRError,
    build_text,
    calculate_confidence,
    normalize_registration_numbers,
    run_clova_ocr,
)
from .services.pipeline import mask_personal_info, process_document, run_ocr

INVOKE_URL = 'https://clova.invalid/custom/v1/1234/fake-url-token/general'
SECRET = 'fake-secret-key'
FILE_CONTENT = b'fake-file-content'
REGISTRATION_NUMBER = '900101-5123456'


def field(text, confidence=0.99, line_break=False):
    return {'inferText': text, 'inferConfidence': confidence, 'lineBreak': line_break, 'type': 'NORMAL'}


def page(*fields, result='SUCCESS', index=0):
    return {
        'inferResult': result,
        'message': result,
        'convertedImageInfo': {'pageIndex': index},
        'fields': list(fields),
    }


def clova_response(images=None, status=200, content=None):
    """requests.post가 돌려줄 가짜 응답을 만든다."""
    response =requests.Response()
    response.status_code = status
    if content is None:
        content = json.dumps({'version': 'V2', 'images': images or []}).encode()
    response._content = content
    return response


NOTICE_PAGE = page(
    field('아산시', line_break=True),
    field('외국인등록번호', line_break=True),
    field(REGISTRATION_NUMBER, line_break=True),
    field('주소:'), field('충청남도'), field('아산시'), field('배방읍'), field('가상로'), field('123,'),
    field('101동', line_break=True),
    field('세액', line_break=True),
    field('6,250원', 0.95, line_break=True),
    field('납부기한', line_break=True),
    field('2026년', 0.9), field('8월', 0.9), field('31일', 0.9, line_break=True),
    field('문의:'), field('아산시청', 0.85), field('세무과', line_break=True),
)


# 마스킹 결과는 더미 마스킹(MASK_MODE=dummy) 기준이다. 로컬 .env의 OCR_MODE·MASK_MODE 값에 따라 달라지지 않게 고정한다.
@override_settings(OCR_MODE='dummy', MASK_MODE='dummy')
class BuildTextTests(SimpleTestCase):
    def test_restores_lines_from_line_break(self):
        images = [page(field('납부기한:'), field('2026년'), field('8월', line_break=True), field('세액', line_break=True))]

        self.assertEqual(build_text(images), '납부기한: 2026년 8월\n세액')

    def test_keeps_last_words_without_line_break(self):
        images = [page(field('첫', line_break=True), field('마지막'), field('줄'))]

        self.assertEqual(build_text(images), '첫\n마지막 줄')

    def test_joins_pages_with_new_line(self):
        images = [
            page(field('1쪽'), index=0),
            page(field('2쪽', line_break=True), index=1),
        ]

        self.assertEqual(build_text(images), '1쪽\n2쪽')

    def test_joins_split_registration_number_in_text(self):
        images = [page(field('900101'), field('-'), field('5123456', line_break=True))]

        self.assertEqual(build_text(images), REGISTRATION_NUMBER)

    def test_address_line_is_masked_by_existing_pattern(self):
        masked = mask_personal_info(build_text([NOTICE_PAGE]))

        self.assertIn('주소: [주소 가림]\n세액', masked['masked_text'])
        for detail in ('배방읍', '가상로 123', '101동'):
            self.assertNotIn(detail, masked['masked_text'])


@override_settings(OCR_MODE='dummy')
class NormalizeRegistrationNumberTests(SimpleTestCase):
    def test_joins_split_numbers_and_masking_finds_them(self):
        """쪼개진 번호가 이어지고 마스킹 정규식에 걸린다."""
        for text in (
            '900101 - 5123456',
            '900101- 5123456',
            '900101 -5123456',
            '90 0101 -51 23456',
            '900101 5123456',
            '9001015123456',
            '900101–5123456',
        ):
            with self.subTest(text=text):
                normalized = normalize_registration_numbers(text)

                self.assertEqual(normalized, REGISTRATION_NUMBER)
                self.assertEqual(mask_personal_info(normalized)['masked_text'], '******-*******')

    def test_keeps_text_around_number(self):
        self.assertEqual(
            normalize_registration_numbers('번호:900101 5123456 확인'),
            f'번호:{REGISTRATION_NUMBER} 확인',
        )

    def test_keeps_other_numbers(self):
        """납부번호·계좌번호·전화번호는 바꾸지 않는다."""
        for text in (
            '4420012026080001234',
            '0020261112345678',
            '790-1234-5678-90',
            '123456-78-901234',
            '123-456789-01234',
            '010-0000-4321',
            '041-000-1234',
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_registration_numbers(text), text)

    def test_does_not_join_across_lines(self):
        text = '900101-\n5123456'

        self.assertEqual(normalize_registration_numbers(text), text)

    def test_thirteen_digits_without_other_label_are_treated_as_registration_number(self):
        # 이름표가 없거나 목록에 없는 이름표면 누락보다 과잉 마스킹을 택한다(F1-4).
        for text, expected in (
            ('2026083112345', '202608-3112345'),
            ('번호: 2026083112345', '번호: 202608-3112345'),
            ('외국인등록번호: 9001015123456', '외국인등록번호: 900101-5123456'),
            ('외국인등록번호\n9001015123456', '외국인등록번호\n900101-5123456'),
            ('고지번호 안내\n2026083112345', '고지번호 안내\n202608-3112345'),
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_registration_numbers(text), expected)

    def test_labeled_other_number_is_not_changed(self):
        """이름표로 보아 등록번호가 아닌 번호에는 하이픈을 넣지 않는다(masking.py와 같은 기준)."""
        for text in (
            '고지서 번호: 2026083112345', '고지번호 2026083112345', '납부번호: 2026083112345',
            '문서번호: 2026083112345', '관리번호: 2026083112345', '접수번호: 2026083112345',
            '고지서 번호\n2026083112345',
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_registration_numbers(text), text)
                with override_settings(MASK_MODE='rules'):
                    self.assertEqual(mask_personal_info(normalize_registration_numbers(text))['masked_count'], 0)

    def test_other_label_does_not_protect_hyphenated_number(self):
        self.assertEqual(normalize_registration_numbers('고지번호: 900101 - 5123456'), '고지번호: 900101-5123456')


def cell(text, column, row, angle=0.0, confidence=0.99, line_break=True):
    """표의 (열, 행) 자리에 놓인 단어. 종이를 angle도 돌려 찍은 것처럼 상자 좌표를 돌려서 붙인다."""
    left, top = 200 + column * 500, 200 + row * 60
    corners = [(left, top), (left + 200, top), (left + 200, top + 30), (left, top + 30)]
    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    item = field(text, confidence, line_break)
    item['boundingPoly'] = {'vertices': [{'x': x * cos - y * sin, 'y': x * sin + y * cos} for x, y in corners]}
    return item


def table(angle, order=None):
    """이름표(왼쪽 열)와 값(오른쪽 열)이 네 행으로 놓인 표. order는 CLOVA가 돌려준 줄 순서다."""
    cells = [
        cell('성명', 0, 0, angle), cell('홍길동', 1, 0, angle),
        cell('주소', 0, 1, angle), cell('아산시 가상로 1', 1, 1, angle),
        cell('연락처', 0, 2, angle), cell('041-000-0000', 1, 2, angle),
        cell('입국일', 0, 3, angle), cell('2026년 10월 5일', 1, 3, angle),
    ]
    return [page(*(cells[index] for index in order or range(len(cells))))]


TABLE_TEXT = '성명\n홍길동\n주소\n아산시 가상로 1\n연락처\n041-000-0000\n입국일\n2026년 10월 5일'
# 실제 응답에서 본 모양: 값이 이름표에서 떨어지고 다른 이름표가 사이에 낀다.
SCRAMBLED = (2, 0, 1, 6, 4, 5, 7, 3)
SCRAMBLED_TEXT = '주소\n성명\n홍길동\n입국일\n연락처\n041-000-0000\n2026년 10월 5일\n아산시 가상로 1'


@override_settings(OCR_MODE='dummy', MASK_MODE='dummy')
class SkewedLineOrderTests(SimpleTestCase):
    """기울여 찍은 사진의 줄 순서. 기울기가 기준 이상일 때만 좌표로 다시 맞춘다."""

    def test_skewed_page_is_reordered_by_position(self):
        for angle in (6, 10.5, 14.9, 29.1, -30, 90, 180):
            with self.subTest(angle=angle):
                self.assertEqual(build_text(table(angle, SCRAMBLED)), TABLE_TEXT)

    def test_upright_page_keeps_clova_order(self):
        # 정면 사진과 PDF는 CLOVA가 준 순서를 그대로 쓴다. 순서가 달라 보여도 손대지 않는다.
        for angle in (0, 2, -4.9):
            with self.subTest(angle=angle):
                self.assertEqual(build_text(table(angle, SCRAMBLED)), SCRAMBLED_TEXT)

    def test_page_without_boxes_keeps_clova_order(self):
        images = table(30, SCRAMBLED)
        for item in images[0]['fields']:
            del item['boundingPoly']

        self.assertEqual(build_text(images), SCRAMBLED_TEXT)

    def test_line_without_box_keeps_clova_order(self):
        # 한 줄이라도 좌표가 없으면 그 줄을 어디에 둘지 알 수 없어 다시 맞추지 않는다.
        images = table(30, SCRAMBLED)
        del images[0]['fields'][3]['boundingPoly']

        self.assertEqual(build_text(images), SCRAMBLED_TEXT)

    def test_word_without_box_in_a_line_keeps_clova_order(self):
        # 한 줄의 단어 가운데 일부만 좌표가 없어도 다시 맞추지 않는다(CodeRabbit).
        fields = [
            cell('주소', 0, 1, 30), cell('성명', 0, 0, 30), cell('홍길동', 1, 0, 30),
            cell('연락처', 0, 2, 30), cell('041-000-0000', 1, 2, 30),
            cell('아산시', 1, 1, 30, line_break=False), cell('가상로 1', 1.5, 1, 30),
        ]
        del fields[5]['boundingPoly']

        self.assertEqual(build_text([page(*fields)]), '주소\n성명\n홍길동\n연락처\n041-000-0000\n아산시 가상로 1')

    def test_malformed_boxes_are_ignored(self):
        for broken in (
            None, 'box', {}, {'vertices': None}, {'vertices': [{'x': 1, 'y': 1}]},
            {'vertices': [{'x': 1, 'y': 1}, {'x': 2}, {'x': 2, 'y': 2}, {'x': 1, 'y': 2}]},
            {'vertices': [{'x': '1', 'y': 1}, {'x': 2, 'y': 1}, {'x': 2, 'y': 2}, {'x': 1, 'y': 2}]},
            {'vertices': [{'x': float('nan'), 'y': 1}, {'x': 2, 'y': 1}, {'x': 2, 'y': 2}, {'x': 1, 'y': 2}]},
            {'vertices': [{'x': True, 'y': 1}, {'x': 2, 'y': 1}, {'x': 2, 'y': 2}, {'x': 1, 'y': 2}]},
            {'vertices': ['a', 'b', 'c', 'd']},
        ):
            with self.subTest(broken=broken):
                images = table(30, SCRAMBLED)
                images[0]['fields'][0]['boundingPoly'] = broken

                self.assertEqual(build_text(images), SCRAMBLED_TEXT)

    def test_too_few_boxes_keep_clova_order(self):
        # 상자가 몇 개뿐이면 기울기를 믿을 수 없다.
        images = [page(cell('주소', 0, 1, 30), cell('성명', 0, 0, 30), cell('홍길동', 1, 0, 30))]

        self.assertEqual(build_text(images), '주소\n성명\n홍길동')

    def test_words_of_a_line_stay_together(self):
        # CLOVA가 한 줄로 묶어 준 단어들은 나누지 않고 순서만 옮긴다.
        images = [page(
            cell('주소', 0, 1, 30), cell('성명', 0, 0, 30), cell('홍길동', 1, 0, 30),
            cell('연락처', 0, 2, 30), cell('041-000-0000', 1, 2, 30),
            cell('아산시', 1, 1, 30, line_break=False), cell('가상로 1', 1.5, 1, 30),
        )]

        self.assertEqual(build_text(images), '성명\n홍길동\n주소\n아산시 가상로 1\n연락처\n041-000-0000')

    def test_each_page_is_judged_separately(self):
        images = table(30, SCRAMBLED) + table(0, SCRAMBLED)

        self.assertEqual(build_text(images), f'{TABLE_TEXT}\n{SCRAMBLED_TEXT}')

    def test_reordered_text_is_masked_like_upright_text(self):
        # 이름표와 값이 다시 붙어야 마스킹이 값을 찾는다. 주소가 이름표에서 떨어진 채로는 가려지지 않는다.
        with override_settings(MASK_MODE='rules'):
            upright = mask_personal_info(build_text(table(0)))
            skewed = mask_personal_info(build_text(table(29.1, SCRAMBLED)))

        self.assertEqual(skewed, upright)
        self.assertIn('주소\n아산시 [주소 가림]', skewed['masked_text'])

    def test_confidence_uses_the_same_line_order(self):
        # 기한 이름표 다음 줄의 날짜를 찾는 계산도 다시 맞춘 순서를 본다.
        def notice(angle, order):
            cells = [
                cell('납부기한', 0, 0, angle), cell('2026년 8월 11일', 1, 0, angle, confidence=0.3),
                cell('세액', 0, 1, angle), cell('6,250원', 1, 1, angle),
                cell('발행일', 0, 2, angle), cell('2026년 8월 1일', 1, 2, angle),
            ]
            return [page(*(cells[index] for index in order))]

        # CLOVA가 준 순서에서는 "납부기한" 다음 줄이 "세액"이라 기한 날짜를 찾지 못한다.
        self.assertEqual(calculate_confidence(notice(20, (0, 2, 3, 1, 4, 5))), 0.3)
        self.assertEqual(calculate_confidence(notice(0, (0, 1, 2, 3, 4, 5))), 0.3)


class CalculateConfidenceTests(SimpleTestCase):
    def test_uses_lowest_deadline_or_amount_word(self):
        # 기한 0.9, 금액 0.95, 기관은 가장 잘 읽힌 세무과 0.99(아산시청 0.85는 쓰지 않는다).
        self.assertEqual(calculate_confidence([NOTICE_PAGE]), 0.9)

    def test_ignores_date_outside_deadline_label(self):
        # W1 깨끗한 샘플: 납기 후 날짜의 낮은 신뢰도 때문에 재업로드 안내가 뜨면 안 된다.
        images = [page(
            field('납부기한', line_break=True),
            field('2026년', 0.99), field('8월', 0.99), field('31일', 0.99, line_break=True),
            field('납기'), field('후'), field('금액', line_break=True),
            field('(2026년', 0.44), field('9월', 0.44), field('1일부터)', 0.44, line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.99)

    def test_low_deadline_word_lowers_result(self):
        images = [page(
            field('납부기한', line_break=True),
            field('2026년', 0.99), field('8월', 0.99), field('11일', 0.3, line_break=True),
            field('6,250원', 0.99, line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.3)

    def test_finds_date_on_label_line(self):
        images = [page(field('납부기한:'), field('2026년', 0.8), field('8월', 0.7), field('31일', line_break=True))]

        self.assertEqual(calculate_confidence(images), 0.7)

    def test_word_with_label_counts_as_deadline(self):
        images = [page(field('2026년', 0.9), field('12월', 0.9), field('5일까지', 0.6, line_break=True))]

        self.assertEqual(calculate_confidence(images), 0.6)

    def test_falls_back_to_all_dates_without_label(self):
        images = [page(field('발행일:'), field('2026년', 0.6), field('8월', 0.8), field('1일', 0.8, line_break=True))]

        self.assertEqual(calculate_confidence(images), 0.6)

    def test_finds_label_on_other_page(self):
        images = [
            page(field('9월', 0.2, line_break=True), index=0),
            page(field('납부기한', line_break=True), field('8월', 0.9, line_break=True), index=1),
        ]

        self.assertEqual(calculate_confidence(images), 0.9)

    def test_label_on_last_line_does_not_fail(self):
        images = [page(field('납부기한', line_break=True))]

        self.assertEqual(calculate_confidence(images), 0.0)

    def test_no_key_word_returns_zero(self):
        images = [page(field('주민세'), field('안내문', line_break=True))]

        self.assertEqual(calculate_confidence(images), 0.0)

    def test_uses_only_found_categories(self):
        """일부 분류가 없으면 찾은 분류만으로 계산한다."""
        for fields, expected in (
            ([field('6,250원', 0.8, line_break=True)], 0.8),
            ([field('아산시청', 0.75, line_break=True)], 0.75),
            ([field('6,250원', 0.8), field('아산시청', 0.75, line_break=True)], 0.75),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(calculate_confidence([page(*fields)]), expected)

    def test_agency_uses_best_read_word(self):
        """기관 낱말이 여러 번 나오면 가장 잘 읽힌 것 하나를 쓴다(데모 견본2의 모양)."""
        images = [page(
            field('신청'), field('기한', line_break=True),
            field('2027년', 0.99), field('1월', 0.99), field('3일까지', 0.99, line_break=True),
            field('35,000원', 0.99, line_break=True),
            field('(체류지'), field('관할'), field('출입국·외국인관서)', 0.42, line_break=True),
            field('대전출입국', 0.63), field('·외국인사무소', 0.93, line_break=True),
            field('(외국인종합안내센터', 0.98), field('1345)', line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.98)

    def test_agency_lowers_result_when_every_agency_word_is_low(self):
        """기관 낱말을 하나도 또렷하게 읽지 못하면 낮게 나온다."""
        images = [page(
            field('납부기한', line_break=True),
            field('2026년', 0.99), field('8월', 0.99), field('31일', 0.99, line_break=True),
            field('6,250원', 0.99, line_break=True),
            field('문의:'), field('이산시청', 0.44), field('제우과', 0.44, line_break=True),
            field('아산시청', 0.5, line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.5)

    def test_low_date_or_amount_is_not_hidden_by_other_words(self):
        """기한·금액은 같은 항목이 여러 번 나와도 가장 낮은 값을 쓴다."""
        for low_field in (field('11일', 0.6, line_break=True), field('6,250원', 0.6, line_break=True)):
            with self.subTest(text=low_field['inferText']):
                images = [page(
                    field('납부기한', line_break=True),
                    field('2026년', 0.99), field('8월', 0.99, line_break=True),
                    field('납부기한', line_break=True),
                    low_field,
                    field('6,430원', 0.99, line_break=True),
                    field('아산시청', 0.99, line_break=True),
                )]

                self.assertEqual(calculate_confidence(images), 0.6)

    def test_law_name_is_not_agency(self):
        """법 이름은 기관 낱말이 들어 있어도 기관으로 치지 않는다."""
        for law in (
            '「출입국관리법」', '출입국관리법', '「출입국관리법」에', '출입국관리법 시행령', '출입국관리법시행령',
            # 괄호 없이 조사가 붙어 한 단어로 읽힌 경우
            '출입국관리법에', '출입국관리법에서', '출입국관리법은', '출입국관리법의', '출입국관리법상',
            '출입국관리법으로', '출입국관리법에,', '출입국관리법시행령에',
        ):
            with self.subTest(law=law):
                # 법 이름만 또렷하고 기관명은 흐리면 낮게 나와야 한다.
                images = [page(field(law, 0.99), field('대전출입국', 0.5, line_break=True))]

                self.assertEqual(calculate_confidence(images), 0.5)

    def test_law_name_alone_is_not_a_key_word(self):
        images = [page(field('「출입국관리법」', 0.99), field('에'), field('따라', line_break=True))]

        self.assertEqual(calculate_confidence(images), 0.0)

    def test_agency_name_containing_law_letter_is_kept(self):
        """'법'이 낱말 중간에 있는 기관명은 기관으로 남는다."""
        for agency in ('법무부출입국', '법의학센터', '법과대학행정센터', '법인세무서'):
            with self.subTest(agency=agency):
                images = [page(field(agency, 0.8, line_break=True))]

                self.assertEqual(calculate_confidence(images), 0.8)

    def test_next_line_is_used_only_after_label_line(self):
        """'기한'이 든 문장의 다음 줄 날짜(발행일)는 기한이 아니다(데모 견본2의 모양)."""
        images = [page(
            field('신청'), field('기한', line_break=True),
            field('2027년', 0.99), field('1월', 0.99), field('3일까지', 0.99, line_break=True),
            field('기한'), field('내에'), field('등록하지'), field('않으면'), field('과태료가'),
            field('부과됩니다.', line_break=True),
            field('2026년', 0.5), field('11월', 0.5), field('10일', 0.5, line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.99)

    def test_label_line_variants_use_next_line(self):
        for label_fields in (
            [field('납부기한', line_break=True)],
            [field('납부'), field('기한', line_break=True)],
            [field('납부기한:', line_break=True)],
            [field('신청'), field('마감', line_break=True)],
            [field('접수'), field('마감일', line_break=True)],
            [field('접수'), field('마감'), field('일', line_break=True)],
            [field('접수'), field('마감'), field('일:', line_break=True)],
        ):
            with self.subTest(label=[item['inferText'] for item in label_fields]):
                images = [page(
                    field('발행일:'), field('8월', 0.99), field('1일', 0.99, line_break=True),
                    *label_fields,
                    field('8월', 0.6), field('31일', 0.6, line_break=True),
                )]

                self.assertEqual(calculate_confidence(images), 0.6)

    def test_second_deadline_after_split_label_is_not_missed(self):
        """기한이 두 개일 때, 띄어 읽힌 이름표('마감 일') 다음 줄의 낮은 날짜를 놓치지 않는다."""
        images = [page(
            field('납부기한', line_break=True),
            field('8월', 0.99), field('31일', 0.99, line_break=True),
            field('접수'), field('마감'), field('일', line_break=True),
            field('9월', 0.3), field('5일', 0.3, line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.3)

    def test_sentence_with_label_falls_back_to_all_dates(self):
        """기한 이름표 줄이 없고 '기한'이 든 문장만 있으면 모든 날짜를 기한 후보로 본다."""
        images = [page(
            field('발행일:'), field('2026년', 0.6), field('8월', 0.9), field('1일', 0.9, line_break=True),
            field('기한'), field('내에'), field('납부하지'), field('않으면'), field('가산세가'),
            field('붙습니다.', line_break=True),
            field('아산시장', line_break=True),
        )]

        self.assertEqual(calculate_confidence(images), 0.6)

    def test_multi_page_uses_all_pages(self):
        """기한·금액은 모든 페이지의 최솟값, 기관은 모든 페이지에서 가장 잘 읽힌 값을 쓴다."""
        first = page(
            field('납부기한', line_break=True),
            field('2026년', 0.98), field('12월', 0.98), field('10일', 0.98, line_break=True),
            field('국민건강보험공단', 0.6, line_break=True),
            index=0,
        )
        second = page(field('152,330원', 0.97, line_break=True), field('천안지사', 0.99, line_break=True), index=1)
        low_deadline = page(field('12월', 0.4), field('5일까지', 0.4, line_break=True), index=2)

        self.assertEqual(calculate_confidence([first, second]), 0.97)
        self.assertEqual(calculate_confidence([first, second, low_deadline]), 0.4)

    def test_label_at_page_end_does_not_use_next_page(self):
        images = [
            page(field('6,250원', 0.9, line_break=True), field('납부기한', line_break=True), index=0),
            page(field('8월', 0.3), field('31일', 0.3, line_break=True), index=1),
        ]

        # 기한 라벨로 날짜를 찾지 못하면 모든 날짜를 기한 후보로 본다.
        self.assertEqual(calculate_confidence(images), 0.3)


class ClovaTestCase(TestCase):
    """실제 파일을 임시 폴더에 올리고 requests.post를 가짜로 바꾼다."""

    def setUp(self):
        media = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(media.cleanup)
        overrides = override_settings(
            MEDIA_ROOT=media.name,
            DOCUMENTS_STEP_DELAY=0,
            OCR_MODE='clova',
            # 실제 OCR에는 실제 마스킹 규칙이 필요하다(pipeline.mask_personal_info 안전장치).
            MASK_MODE='rules',
            CLOVA_OCR_INVOKE_URL=INVOKE_URL,
            CLOVA_OCR_SECRET=SECRET,
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        patcher = mock.patch('documents.services.ocr.requests.post')
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        User = get_user_model()
        self.user = User.objects.create_user(**{User.USERNAME_FIELD: 'owner@example.com'}, password='test-password')

    def create_document(self, name='notice.png', file_type=Document.FileType.IMAGE):
        return Document.objects.create(
            owner=self.user,
            file=SimpleUploadedFile(name, FILE_CONTENT),
            file_name=name,
            file_type=file_type,
            step=Document.Step.QUEUED,
        )

    def sent_body(self):
        return self.post.call_args.kwargs['json']


class RunClovaOcrTests(ClovaTestCase):
    def test_returns_text_and_confidence(self):
        self.post.return_value = clova_response([NOTICE_PAGE])

        result = run_clova_ocr(self.create_document())

        self.assertEqual(set(result), {'text', 'confidence'})
        self.assertEqual(result['text'].split('\n')[2], REGISTRATION_NUMBER)
        self.assertEqual(result['confidence'], 0.9)

    def test_sends_documented_request(self):
        self.post.return_value = clova_response([NOTICE_PAGE])

        run_clova_ocr(self.create_document())

        self.assertEqual(self.post.call_args.args, (INVOKE_URL,))
        kwargs = self.post.call_args.kwargs
        self.assertEqual(kwargs['headers']['X-OCR-SECRET'], SECRET)
        self.assertEqual(kwargs['timeout'], ocr.CLOVA_TIMEOUT)
        self.assertIs(kwargs['allow_redirects'], False)
        body = self.sent_body()
        self.assertEqual(body['version'], 'V2')
        self.assertTrue(body['requestId'])
        self.assertIsInstance(body['timestamp'], int)
        self.assertEqual(len(body['images']), 1)
        self.assertEqual(body['images'][0]['format'], 'png')
        self.assertEqual(base64.b64decode(body['images'][0]['data']), FILE_CONTENT)

    def test_does_not_send_file_name(self):
        self.post.return_value = clova_response([NOTICE_PAGE])

        run_clova_ocr(self.create_document('응우옌_고지서.png'))

        self.assertEqual(self.sent_body()['images'][0]['name'], ocr.CLOVA_IMAGE_NAME)

    def test_format_follows_file(self):
        """파일 종류에 맞는 format을 보낸다."""
        for name, file_type, expected in (
            ('notice.jpg', Document.FileType.IMAGE, 'jpg'),
            ('notice.JPEG', Document.FileType.IMAGE, 'jpeg'),
            ('notice.PNG', Document.FileType.IMAGE, 'png'),
            ('notice.pdf', Document.FileType.PDF, 'pdf'),
        ):
            with self.subTest(name=name):
                self.post.return_value = clova_response([NOTICE_PAGE])

                run_clova_ocr(self.create_document(name, file_type))

                self.assertEqual(self.sent_body()['images'][0]['format'], expected)

    def test_rejects_unsupported_format(self):
        with self.assertRaises(OCRError):
            run_clova_ocr(self.create_document('notice.gif'))

        self.post.assert_not_called()

    def test_pdf_pages_are_joined_and_all_used_for_confidence(self):
        self.post.return_value = clova_response([
            page(field('1쪽', line_break=True), field('6,250원', 0.9, line_break=True), index=0),
            page(field('2쪽', line_break=True), index=1),
            page(field('3쪽', line_break=True), field('아산시청', 0.8, line_break=True), index=2),
        ])

        result = run_clova_ocr(self.create_document('notice.pdf', Document.FileType.PDF))

        self.assertEqual(result['text'], '1쪽\n6,250원\n2쪽\n3쪽\n아산시청')
        self.assertEqual(result['confidence'], 0.8)

    def test_recognition_failure_raises(self):
        """inferResult가 SUCCESS가 아니면 실패한다."""
        for result in ('FAILURE', 'ERROR'):
            with self.subTest(result=result):
                self.post.return_value = clova_response([page(result=result)])

                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

    def test_one_failed_page_fails_document(self):
        self.post.return_value = clova_response([NOTICE_PAGE, page(result='FAILURE', index=1)])

        with self.assertRaises(OCRError):
            run_clova_ocr(self.create_document('notice.pdf', Document.FileType.PDF))

    def test_empty_text_raises(self):
        """인식한 글자가 없으면 실패한다."""
        for images in ([page()], [page(field('  ', line_break=True))]):
            with self.subTest(images=images):
                self.post.return_value = clova_response(images)

                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

    def test_http_error_raises(self):
        """본문이 정상 형식이어도 HTTP 상태만으로 실패한다. 리디렉션도 오류로 처리한다."""
        for status in (301, 302, 307, 308, 400, 401, 403, 500, 503):
            with self.subTest(status=status):
                self.post.return_value = clova_response([NOTICE_PAGE], status=status)

                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

    def test_page_limit_error_is_distinguished(self):
        """CLOVA가 페이지 수 제한으로 거부하면 PageLimitOCRError(OCRError의 하위)를 던진다."""
        content = json.dumps({'code': '0011', 'message': 'Request invalid: No more than 10 pages.', 'path': INVOKE_URL})
        self.post.return_value = clova_response(status=400, content=content.encode())

        with self.assertRaises(PageLimitOCRError) as caught:
            run_clova_ocr(self.create_document('notice.pdf', Document.FileType.PDF))

        self.assertIsInstance(caught.exception, OCRError)
        self.assertNotIn(INVOKE_URL, str(caught.exception))

    def test_other_error_responses_are_not_page_limit(self):
        for content in (
            b'{"code": "0001", "message": "URL is invalid."}', b'{"code": 11}', b'{"code": null}', b'{}',
            b'["0011"]', b'"0011"', b'not json', b'',
        ):
            with self.subTest(content=content):
                self.post.return_value = clova_response(status=400, content=content)

                with self.assertRaises(OCRError) as caught:
                    run_clova_ocr(self.create_document('notice.pdf', Document.FileType.PDF))

                self.assertNotIsInstance(caught.exception, PageLimitOCRError)

    def test_page_limit_code_in_success_response_is_ignored(self):
        """code 값은 오류 응답에서만 본다."""
        content = json.dumps({'code': '0011', 'images': [NOTICE_PAGE]}).encode()
        self.post.return_value = clova_response(content=content)

        self.assertEqual(run_clova_ocr(self.create_document())['confidence'], 0.9)

    def test_network_error_raises(self):
        """timeout과 연결 오류는 OCRError가 된다."""
        for error in (requests.Timeout, requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError):
            with self.subTest(error=error.__name__):
                self.post.side_effect = error()

                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

    def test_invalid_response_raises(self):
        """JSON이 아니거나 images가 없는 응답은 실패한다."""
        for content in (b'not json', b'{}', b'[]', b'{"images": []}', b'{"images": null}'):
            with self.subTest(content=content):
                self.post.return_value = clova_response(content=content)

                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

    def test_malformed_images_raise_ocr_error(self):
        """images 항목·fields·inferConfidence 형식이 틀리면 OCRError로 통일한다."""
        success = {'inferResult': 'SUCCESS'}
        for images in (
            ['문자열'],
            [None],
            [{**success, 'fields': None}],
            [{**success, 'fields': '문자열'}],
            [{**success, 'fields': ['문자열']}],
            [{**success, 'fields': [field('글자', None)]}],
            [{**success, 'fields': [field('글자', '0.9')]}],
            [{**success, 'fields': [{'inferText': '글자'}]}],
            [{**success, 'fields': [field('글자', True)]}],
            [{**success, 'fields': [field('글자', float('nan'))]}],
            [{**success, 'fields': [field(None)]}],
            [{**success, 'fields': [field(123)]}],
        ):
            with self.subTest(images=images):
                self.post.return_value = clova_response(images)

                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

    def test_rejects_non_https_url_without_calling_clova(self):
        """HTTPS가 아닌 Invoke URL로는 문서와 키를 보내지 않는다."""
        for url in ('http://clova.invalid/general', 'clova.invalid/general', 'ftp://clova.invalid/general'):
            with self.subTest(url=url), override_settings(CLOVA_OCR_INVOKE_URL=url):
                with self.assertRaises(OCRError) as caught:
                    run_clova_ocr(self.create_document())

                self.assertNotIn(url, str(caught.exception))

        self.post.assert_not_called()

    def test_missing_settings_raise_without_calling_clova(self):
        """설정이 없으면 CLOVA를 호출하지 않는다."""
        for name in ('CLOVA_OCR_INVOKE_URL', 'CLOVA_OCR_SECRET'):
            with self.subTest(name=name), override_settings(**{name: ''}):
                with self.assertRaises(OCRError):
                    run_clova_ocr(self.create_document())

        self.post.assert_not_called()

    def test_missing_file_raises(self):
        document = self.create_document()
        document.file.storage.delete(document.file.name)

        with self.assertRaises(OCRError):
            run_clova_ocr(document)

        self.post.assert_not_called()

    def test_error_does_not_expose_url_or_secret(self):
        self.post.side_effect = requests.ConnectionError(f'failed: {INVOKE_URL} {SECRET}')

        with self.assertRaises(OCRError) as caught:
            run_clova_ocr(self.create_document())

        self.assertNotIn(INVOKE_URL, str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)


class PipelineIntegrationTests(ClovaTestCase):
    def setUp(self):
        super().setUp()
        # 이 클래스는 OCR·마스킹 연동만 본다. 실제 OCR에 더미 해석을 붙이는 조합은 pipeline 안전장치가 막으므로
        # 테스트에서만 더미 해석 결과(original_text=마스킹된 원문)를 바꿔 끼운다.
        dummy_interpret = pipeline.interpret

        def interpret_as_dummy(masked_text, language):
            with override_settings(OCR_MODE='dummy'):
                return dummy_interpret(masked_text, language)

        patcher = mock.patch.object(pipeline, 'interpret', side_effect=interpret_as_dummy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, document):
        process_document(document.id)
        document.refresh_from_db()
        return document

    def process_expecting_failure(self, document):
        """처리 실패를 기다려 문서와 traceback까지 포함한 로그 내용을 돌려준다."""
        with self.assertLogs(pipeline.logger, 'ERROR') as logs:
            process_document(document.id)
        document.refresh_from_db()
        formatter = logging.Formatter()
        return document, '\n'.join(formatter.format(record) for record in logs.records)

    def assert_failed_at_ocr(self, document):
        self.assertEqual(document.status, Document.Status.FAILED)
        self.assertEqual(document.step, Document.Step.OCR)
        self.assertEqual(document.fail_reason, pipeline.FAIL_MESSAGES[Document.Step.OCR])

    def test_success_masks_personal_info(self):
        self.post.return_value = clova_response([NOTICE_PAGE])

        document = self.process(self.create_document())

        self.assertEqual(document.status, Document.Status.DONE)
        self.assertFalse(document.low_confidence)
        self.assertEqual(document.masked_count, 2)
        self.assertNotIn(REGISTRATION_NUMBER, document.result.original_text)
        for detail in ('배방읍', '가상로 123', '101동'):
            self.assertNotIn(detail, document.result.original_text)

    def test_split_registration_number_is_masked(self):
        self.post.return_value = clova_response([
            page(field('900101'), field('-'), field('51'), field('23456', line_break=True)),
        ])

        document = self.process(self.create_document())

        self.assertEqual(document.masked_count, 1)
        self.assertIn('******-*******', document.result.original_text)
        self.assertNotIn('5123456', document.result.original_text)
        self.assertNotIn('23456', document.result.original_text)

    def test_low_confidence_sets_flag(self):
        self.post.return_value = clova_response([page(
            field('납부기한', line_break=True),
            field('2026년', 0.61), field('8월', 0.61), field('11일', 0.6, line_break=True),
        )])

        document = self.process(self.create_document())

        self.assertEqual(document.status, Document.Status.DONE)
        self.assertTrue(document.low_confidence)

    def test_threshold_boundary(self):
        """기준값(0.7)과 같으면 낮음이 아니고, 그보다 작으면 낮음이다."""
        for confidence, expected in ((0.7, False), (0.71, False), (0.69, True)):
            with self.subTest(confidence=confidence):
                self.post.return_value = clova_response([page(
                    field('납부기한', line_break=True),
                    field('8월', confidence), field('31일', confidence, line_break=True),
                    field('6,250원', 0.99, line_break=True),
                    field('아산시청', 0.99, line_break=True),
                )])

                document = self.process(self.create_document())

                self.assertEqual(document.low_confidence, expected)

    def test_clean_document_with_one_low_agency_word_is_not_low_confidence(self):
        """깨끗한 문서는 기관 낱말 하나의 신뢰도가 낮아도 '신뢰도 낮음'이 아니다."""
        self.post.return_value = clova_response([page(
            field('신청'), field('기한', line_break=True),
            field('2027년', 0.99), field('1월', 0.99), field('3일까지', 0.99, line_break=True),
            field('35,000원', 0.99, line_break=True),
            field('출입국·외국인관서)', 0.42, line_break=True),
            field('대전출입국', 0.93), field('·외국인사무소', 0.93, line_break=True),
        )])

        document = self.process(self.create_document())

        self.assertEqual(document.status, Document.Status.DONE)
        self.assertFalse(document.low_confidence)

    def test_blurry_document_is_low_confidence(self):
        """흐린 문서는 기한·금액·기관이 모두 낮게 읽혀 '신뢰도 낮음'이 된다(W1 흐린 샘플의 모양)."""
        self.post.return_value = clova_response([page(
            field('납부기한', 0.8, line_break=True),
            field('2026년', 0.61), field('8월', 0.61), field('11일', 0.6, line_break=True),
            field('6,250원', 0.72, line_break=True),
            field('문의:', 0.44), field('이산시청', 0.44), field('제우과', 0.44, line_break=True),
        )])

        document = self.process(self.create_document())

        self.assertEqual(document.status, Document.Status.DONE)
        self.assertTrue(document.low_confidence)

    def test_page_limit_fails_at_ocr(self):
        """페이지 수 제한으로 거부된 문서도 FAILED(step=OCR)가 되고 응답 값이 로그에 남지 않는다."""
        content = json.dumps({'code': '0011', 'message': 'No more than 10 pages.', 'path': INVOKE_URL})
        self.post.return_value = clova_response(status=400, content=content.encode())

        document, logged = self.process_expecting_failure(self.create_document('notice.pdf', Document.FileType.PDF))

        self.assert_failed_at_ocr(document)
        self.assertIn('PageLimitOCRError', logged)
        self.assertNotIn(INVOKE_URL, logged)
        self.assertNotIn('No more than', logged)

    def test_no_key_word_sets_low_confidence(self):
        self.post.return_value = clova_response([page(field('안내문', line_break=True))])

        document = self.process(self.create_document())

        self.assertEqual(document.status, Document.Status.DONE)
        self.assertTrue(document.low_confidence)

    def test_failures_mark_document_failed_without_leaking(self):
        """실패하면 FAILED(step=OCR)가 되고 로그에 URL·키·응답 값이 남지 않는다."""
        original_text = '비밀원문'
        cases = {
            'timeout': {'side_effect': requests.Timeout(f'timed out: {INVOKE_URL}')},
            'connection': {'side_effect': requests.ConnectionError(f'failed: {INVOKE_URL} {SECRET}')},
            'http': {'return_value': clova_response(
                status=400, content=json.dumps({'code': '0011', 'path': INVOKE_URL}).encode(),
            )},
            'infer': {'return_value': clova_response([
                page(field(original_text, line_break=True), result='FAILURE'),
            ])},
            'infer_result_value': {'return_value': clova_response([page(result=original_text)])},
            'confidence_value': {'return_value': clova_response([page(field('글자', original_text))])},
        }
        for name, behavior in cases.items():
            with self.subTest(name=name):
                self.post.reset_mock(return_value=True, side_effect=True)
                self.post.configure_mock(**behavior)

                document, logged = self.process_expecting_failure(self.create_document())

                self.assert_failed_at_ocr(document)
                self.assertFalse(hasattr(document, 'result'))
                self.assertIn('OCRError', logged)
                for secret_value in (INVOKE_URL, SECRET, original_text):
                    self.assertNotIn(secret_value, logged)

    def test_success_logs_nothing(self):
        self.post.return_value = clova_response([NOTICE_PAGE])

        with self.assertNoLogs('documents', 'DEBUG'):
            self.process(self.create_document())


class OcrModeTests(ClovaTestCase):
    def test_dummy_mode_does_not_read_file_or_call_clova(self):
        document = Document(file='documents/missing.jpg', file_type=Document.FileType.IMAGE)

        with override_settings(OCR_MODE='dummy'):
            result = run_ocr(document)

        self.assertEqual(result, {'text': pipeline.DUMMY_OCR_TEXT, 'confidence': 0.92})
        self.post.assert_not_called()

    def test_default_is_dummy_when_setting_is_missing(self):
        document = Document(file='documents/missing.jpg', file_type=Document.FileType.IMAGE)

        with override_settings():
            from django.conf import settings

            del settings.OCR_MODE
            result = run_ocr(document)

        self.assertEqual(result['text'], pipeline.DUMMY_OCR_TEXT)
        self.post.assert_not_called()

    def test_clova_mode_calls_clova(self):
        self.post.return_value = clova_response([NOTICE_PAGE])

        result = run_ocr(self.create_document())

        self.post.assert_called_once()
        self.assertNotEqual(result['text'], pipeline.DUMMY_OCR_TEXT)

    def test_unknown_mode_fails_without_dummy_result(self):
        document = Document(file='documents/missing.jpg', file_type=Document.FileType.IMAGE)

        with override_settings(OCR_MODE='clvoa'), self.assertRaises(ValueError):
            run_ocr(document)

        self.post.assert_not_called()
