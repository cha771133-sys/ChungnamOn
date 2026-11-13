"""CLOVA OCR 연동 테스트. requests.post를 mock으로 대체해 실제 CLOVA를 호출하지 않는다.

가짜 응답은 W1에서 확인한 실제 응답 구조(images[].fields[])를 따른다.
"""

import base64
import json
import logging
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


# 마스킹 결과는 기본값(MASK_MODE=dummy) 기준이다. 로컬 .env의 OCR_MODE=clova에 막히지 않게 고정한다.
@override_settings(OCR_MODE='dummy')
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

    def test_thirteen_digits_are_treated_as_registration_number(self):
        # 누락보다 과잉 마스킹을 택한 결정(F1-4)을 고정한다.
        self.assertEqual(normalize_registration_numbers('고지서 번호: 2026083112345'), '고지서 번호: 202608-3112345')


class CalculateConfidenceTests(SimpleTestCase):
    def test_uses_lowest_key_word(self):
        self.assertEqual(calculate_confidence([NOTICE_PAGE]), 0.85)

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
        self.assertEqual(result['confidence'], 0.85)

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
