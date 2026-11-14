"""문서 해석(W4) 테스트. 실제 AI는 호출하지 않고 Anthropic 클라이언트를 mock으로 바꾼다.

모든 이름·번호·금액은 가짜다. 원문은 데모 견본(주민세 고지서, 외국인등록 안내문)을 마스킹한 모양으로 줄여 옮겼다.
"""

import json
from types import SimpleNamespace
from unittest import mock

import anthropic
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from .models import Document, InterpretationResult
from .services import interpret as interpret_module
from .services import pipeline
from .services.interpret import InterpretError, interpret_document
from .services.pipeline import process_document

RESULT_KEYS = {
    'doc_type', 'title', 'original_text', 'translated_text', 'language', 'summary', 'terms',
    'deadlines', 'required_docs', 'agency', 'amount', 'payment_methods', 'checklist',
}

# 납부 고지서(마스킹 후)
TAX_NOTICE = '\n'.join([
    '주민세(개인분) 납세고지서',
    '성명 [이름 가림]',
    '외국인등록번호 ******-*******',
    '주소 충청남도 아산시 [주소 가림]',
    '연락처 [전화번호 가림]',
    '납부할 금액 11,000원',
    '납부 기한 2026년 8월 31일까지',
    '납부 방법 인터넷 지방세',
    '과세 기관 아산시청',
    '문의 지방세 콜센터 041)537-3001~5',
    '2026년 8월 10일',
    '아산시장',
])
TAX_PAYLOAD = {
    'doc_type': '고지서',
    'title': '주민세(개인분) 납세고지서',
    'translated_text': 'Thông báo nộp thuế cư trú\nSố tiền: 11.000 won',
    'summary': ['Bạn phải nộp 11.000 won trước ngày 31/8/2026.'],
    'terms': [{'word': '주민세(개인분)', 'explain': 'Thuế cư trú cá nhân'}],
    'deadlines': [{'label': '납부 기한', 'date': '2026-08-31', 'kind': 'payment'}],
    'required_docs': [],
    'agency': {'name': '아산시청', 'phone': '041-537-3001'},
    'amount': '11,000원',
    'payment_methods': ['인터넷 지방세'],
}

# 신청 안내문(마스킹 후). 납부가 아니라 신청 기한과 수수료가 나온다.
REGISTRATION_NOTICE = '\n'.join([
    '외국인등록 안내문',
    '성명 [이름 가림]',
    '여권번호 [여권번호 가림]',
    '입국일 2026년 10월 5일',
    '신청 기한 2027년 1월 3일까지',
    '수수료 35,000원',
    '구비서류 여권, 컬러사진(3.5cm×4.5cm) 1장',
    '문의 법무부 이민정보과 02-2110-4094',
    '2026년 11월 10일',
    '대전출입국·외국인사무소 천안출장소장',
])
REGISTRATION_PAYLOAD = {
    'doc_type': '안내문',
    'title': '외국인등록 안내문',
    'translated_text': 'Alien registration notice',
    'summary': ['Apply for alien registration by January 3, 2027.'],
    'terms': [{'word': '구비서류', 'explain': 'Documents you must bring'}],
    'deadlines': [{'label': '신청 기한', 'date': '2027-01-03', 'kind': 'other'}],
    'required_docs': ['여권', '컬러사진(3.5cm×4.5cm) 1장'],
    'agency': {'name': '대전출입국·외국인사무소 천안출장소', 'phone': '02-2110-4094'},
    'amount': '35,000원',
    'payment_methods': [],
}

llm_settings = override_settings(INTERPRET_API_KEY='test-key', INTERPRET_MODEL='test-model')


def text_response(text, stop_reason='end_turn'):
    """SDK 응답 모양. 실제 응답처럼 글자 블록 앞에 생각 블록을 둔다."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=text)],
    )


def json_response(payload, **kwargs):
    return text_response(json.dumps(payload, ensure_ascii=False), **kwargs)


def sdk_error(error_class, **attrs):
    """SDK 예외를 만든다. 생성자가 HTTP 요청·응답 객체를 요구해서 건너뛰고 필요한 값만 넣는다."""
    error = error_class.__new__(error_class)
    for name, value in attrs.items():
        setattr(error, name, value)
    return error


class MockedAnthropic:
    """interpret 모듈의 anthropic.Anthropic을 가짜로 바꾼다. create로 호출 내용을 확인한다."""

    def __init__(self, response=None, error=None):
        self.patcher = mock.patch.object(interpret_module.anthropic, 'Anthropic')
        self.response, self.error = response, error

    def __enter__(self):
        self.client_class = self.patcher.start()
        self.create = self.client_class.return_value.messages.create
        if self.error is not None:
            self.create.side_effect = self.error
        else:
            self.create.return_value = self.response
        return self

    def __exit__(self, *exc_info):
        self.patcher.stop()


def run(payload, text=TAX_NOTICE, language='vi'):
    with MockedAnthropic(json_response(payload)):
        return interpret_document(text, language)


@llm_settings
class InterpretResultTests(SimpleTestCase):
    def test_returns_contract_keys(self):
        result = run(TAX_PAYLOAD)

        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result['doc_type'], '고지서')
        self.assertEqual(result['title'], '주민세(개인분) 납세고지서')
        self.assertEqual(result['summary'], TAX_PAYLOAD['summary'])
        self.assertEqual(result['terms'], TAX_PAYLOAD['terms'])
        self.assertEqual(result['payment_methods'], ['인터넷 지방세'])

    def test_original_text_is_input_not_ai_output(self):
        payload = {**TAX_PAYLOAD, 'original_text': 'AI가 바꾼 원문'}

        self.assertEqual(run(payload)['original_text'], TAX_NOTICE)

    def test_checklist_is_empty(self):
        # 체크리스트는 S4가 만든다. AI가 넣어 보내도 쓰지 않는다.
        payload = {**TAX_PAYLOAD, 'checklist': [{'text': '세금 내기'}]}

        self.assertEqual(run(payload)['checklist'], [])

    def test_language_follows_request(self):
        for language, name in interpret_module.LANGUAGE_NAMES.items():
            with self.subTest(language=language), MockedAnthropic(json_response(TAX_PAYLOAD)) as mocked:
                result = interpret_document(TAX_NOTICE, language)

                self.assertEqual(result['language'], language)
                self.assertIn(name, mocked.create.call_args.kwargs['system'])

    def test_extracts_amount_deadline_agency(self):
        result = run(TAX_PAYLOAD)

        self.assertEqual(result['amount'], '11,000원')
        self.assertEqual(result['deadlines'], [{'label': '납부 기한', 'date': '2026-08-31'}])
        self.assertEqual(
            result['agency'], {'name': '아산시청', 'phone': '041-537-3001', 'source': 'document'},
        )

    def test_long_title_and_doc_type_are_cut_to_model_limits(self):
        result = run({**TAX_PAYLOAD, 'doc_type': '가' * 80, 'title': '나' * 300})

        self.assertEqual(len(result['doc_type']), 50)
        self.assertEqual(len(result['title']), 200)


@llm_settings
class InterpretRequestTests(SimpleTestCase):
    def test_sends_only_masked_text_with_schema(self):
        with MockedAnthropic(json_response(TAX_PAYLOAD)) as mocked:
            interpret_document(TAX_NOTICE, 'vi')

        kwargs = mocked.create.call_args.kwargs
        self.assertEqual(kwargs['model'], 'test-model')
        self.assertEqual(kwargs['messages'], [
            {'role': 'user', 'content': f'<document>\n{TAX_NOTICE}\n</document>'},
        ])
        self.assertEqual(kwargs['output_config']['format']['type'], 'json_schema')
        self.assertEqual(kwargs['output_config']['format']['schema'], interpret_module.RESPONSE_SCHEMA)
        mocked.create.assert_called_once()

    def test_client_has_connect_and_read_timeout(self):
        with MockedAnthropic(json_response(TAX_PAYLOAD)) as mocked:
            interpret_document(TAX_NOTICE, 'vi')

        kwargs = mocked.client_class.call_args.kwargs
        self.assertEqual(kwargs['api_key'], 'test-key')
        self.assertEqual(kwargs['timeout'].connect, interpret_module.CONNECT_TIMEOUT)
        self.assertEqual(kwargs['timeout'].read, interpret_module.READ_TIMEOUT)

    def test_no_call_without_api_key(self):
        with override_settings(INTERPRET_API_KEY=''), MockedAnthropic() as mocked:
            with self.assertRaises(InterpretError):
                interpret_document(TAX_NOTICE, 'vi')

        mocked.client_class.assert_not_called()

    def test_no_call_for_unsupported_language_or_blank_text(self):
        for text, language in ((TAX_NOTICE, 'fr'), ('  \n ', 'vi'), (None, 'vi')):
            with self.subTest(language=language, text=text), MockedAnthropic() as mocked:
                with self.assertRaises(InterpretError):
                    interpret_document(text, language)

                mocked.client_class.assert_not_called()


@llm_settings
class InterpretDeadlineTests(SimpleTestCase):
    def deadlines(self, items, text=TAX_NOTICE):
        return run({**TAX_PAYLOAD, 'deadlines': items}, text=text)['deadlines']

    def test_payment_label_keeps_word(self):
        items = [{'label': '주민세 납부 마감', 'date': '2026-08-31', 'kind': 'payment'}]

        self.assertEqual(self.deadlines(items), [{'label': '주민세 납부 마감', 'date': '2026-08-31'}])

    def test_payment_label_without_word_is_replaced(self):
        # 사용자 언어로 온 label도 S4가 찾을 수 있게 한국어 "납부"로 맞춘다.
        for label in ('Hạn nộp', '마감일'):
            with self.subTest(label=label):
                items = [{'label': label, 'date': '2026-08-31', 'kind': 'payment'}]

                self.assertEqual(self.deadlines(items), [{'label': '납부 기한', 'date': '2026-08-31'}])

    def test_application_deadline_has_no_payment_word(self):
        result = run(REGISTRATION_PAYLOAD, text=REGISTRATION_NOTICE, language='en')

        self.assertEqual(result['deadlines'], [{'label': '신청 기한', 'date': '2027-01-03'}])
        self.assertEqual(result['amount'], '35,000원')
        self.assertEqual(result['required_docs'], ['여권', '컬러사진(3.5cm×4.5cm) 1장'])
        for word in interpret_module.PAYMENT_WORDS:
            self.assertNotIn(word, result['deadlines'][0]['label'])

    def test_other_deadline_with_payment_word_fails(self):
        for label in ('수수료 납부 후 신청', '돈을 내는 날', '납기'):
            with self.subTest(label=label), self.assertRaises(InterpretError):
                self.deadlines(
                    [{'label': label, 'date': '2027-01-03', 'kind': 'other'}], text=REGISTRATION_NOTICE,
                )

    def test_other_deadline_label_must_be_korean(self):
        with self.assertRaises(InterpretError):
            self.deadlines(
                [{'label': 'Application deadline', 'date': '2027-01-03', 'kind': 'other'}],
                text=REGISTRATION_NOTICE,
            )

    def test_date_not_written_in_document_is_dropped(self):
        items = [
            {'label': '납부 기한', 'date': '2026-08-31', 'kind': 'payment'},
            {'label': '이의 신청 기한', 'date': '2026-09-30', 'kind': 'other'},
        ]

        with self.assertLogs(interpret_module.logger, 'WARNING') as logs:
            self.assertEqual(self.deadlines(items), [{'label': '납부 기한', 'date': '2026-08-31'}])
        self.assertNotIn('2026-09-30', ''.join(logs.output))

    def test_date_formats_in_document(self):
        for line in ('납부기한 2026.08.31', '납부기한: 2026-08-31', '납부기한 2026/8/31', '납부기한 2026 년 8 월 31 일'):
            with self.subTest(line=line):
                items = [{'label': '납부 기한', 'date': '2026-08-31', 'kind': 'payment'}]

                self.assertEqual(len(self.deadlines(items, text=f'주민세 고지서\n{line}')), 1)

    def test_date_without_year_uses_year_written_elsewhere(self):
        items = [{'label': '납부 기한', 'date': '2026-08-31', 'kind': 'payment'}]

        self.assertEqual(len(self.deadlines(items, text='2026년도 주민세\n8월 31일까지 납부')), 1)
        with self.assertLogs(interpret_module.logger, 'WARNING'):
            self.assertEqual(self.deadlines(items, text='주민세\n8월 31일까지 납부'), [])

    def test_invalid_date_fails(self):
        for value in ('2026년 8월 31일', '2026-8-31', '2026-02-30', ''):
            with self.subTest(value=value), self.assertRaises(InterpretError):
                self.deadlines([{'label': '납부 기한', 'date': value, 'kind': 'payment'}])

    def test_unknown_kind_fails(self):
        with self.assertRaises(InterpretError):
            self.deadlines([{'label': '납부 기한', 'date': '2026-08-31', 'kind': 'tax'}])

    def test_no_deadline_is_empty_list(self):
        self.assertEqual(self.deadlines([]), [])


@llm_settings
class InterpretMissingValueTests(SimpleTestCase):
    def test_values_not_in_document_stay_empty(self):
        payload = {
            **TAX_PAYLOAD, 'amount': '', 'deadlines': [], 'required_docs': [], 'payment_methods': [],
            'agency': {'name': '', 'phone': ''},
        }

        result = run(payload, text='안내문\n가까운 주민센터로 문의하세요.')

        self.assertEqual(result['amount'], '')
        self.assertEqual(result['deadlines'], [])
        self.assertEqual(result['required_docs'], [])
        self.assertEqual(result['payment_methods'], [])
        self.assertEqual(result['agency'], {'name': '', 'phone': '', 'source': 'unknown'})

    def test_amount_not_written_in_document_is_emptied(self):
        self.assertEqual(run({**TAX_PAYLOAD, 'amount': '12,000원'})['amount'], '')

    def test_phone_not_written_in_document_is_emptied(self):
        payload = {**TAX_PAYLOAD, 'agency': {'name': '아산시청', 'phone': '041-540-2114'}}

        self.assertEqual(run(payload)['agency'], {'name': '아산시청', 'phone': '', 'source': 'document'})

    def test_masked_values_are_not_used(self):
        payload = {
            **TAX_PAYLOAD, 'amount': '[계좌 가림]',
            'agency': {'name': '[이름 가림]', 'phone': '[전화번호 가림]'},
            'terms': [{'word': '[이름 가림]', 'explain': 'x'}],
        }

        result = run(payload)

        self.assertEqual(result['amount'], '')
        self.assertEqual(result['agency'], {'name': '', 'phone': '', 'source': 'unknown'})
        self.assertEqual(result['terms'], [])

    def test_term_word_must_be_in_document(self):
        terms = [
            {'word': '주민세(개인분)', 'explain': 'a'},
            {'word': 'Thuế cư trú', 'explain': 'b'},
            {'word': '납부 기한', 'explain': ''},
        ]

        self.assertEqual(run({**TAX_PAYLOAD, 'terms': terms})['terms'], [{'word': '주민세(개인분)', 'explain': 'a'}])


@llm_settings
class InterpretFailureTests(SimpleTestCase):
    def assert_fails(self, **mocked):
        with MockedAnthropic(**mocked), self.assertRaises(InterpretError) as raised:
            interpret_document(TAX_NOTICE, 'vi')
        # 예외 메시지는 로그에 남는다. 문서 내용과 키가 들어가면 안 된다.
        message = str(raised.exception)
        for secret in ('아산시', '11,000', '041', 'test-key', 'Thông báo'):
            self.assertNotIn(secret, message)
        self.assertIsNone(raised.exception.__cause__)
        return message

    def test_timeout(self):
        self.assert_fails(error=sdk_error(anthropic.APITimeoutError))

    def test_connection_error(self):
        self.assert_fails(error=sdk_error(anthropic.APIConnectionError))

    def test_api_status_error(self):
        for error_class, status in (
            (anthropic.AuthenticationError, 401), (anthropic.RateLimitError, 429),
            (anthropic.InternalServerError, 500),
        ):
            with self.subTest(status=status):
                message = self.assert_fails(error=sdk_error(error_class, status_code=status))

                self.assertIn(str(status), message)

    def test_truncated_or_refused_response(self):
        for stop_reason in ('max_tokens', 'refusal'):
            with self.subTest(stop_reason=stop_reason):
                self.assert_fails(response=json_response(TAX_PAYLOAD, stop_reason=stop_reason))

    def test_response_is_not_json(self):
        for text in ('', '죄송합니다. 아산시 문서를 해석할 수 없습니다.', '{"doc_type": "고지서"', '["고지서"]'):
            with self.subTest(text=text):
                self.assert_fails(response=text_response(text))

    def test_response_without_text_block(self):
        self.assert_fails(response=SimpleNamespace(stop_reason='end_turn', content=[]))

    def test_missing_key(self):
        for key in TAX_PAYLOAD:
            with self.subTest(key=key):
                payload = {k: v for k, v in TAX_PAYLOAD.items() if k != key}

                self.assert_fails(response=json_response(payload))

    def test_wrong_type(self):
        wrong = {
            'doc_type': None, 'title': 1, 'translated_text': ['a'], 'summary': '한 문장',
            'terms': [{'word': '주민세'}], 'deadlines': [{'label': '납부 기한', 'date': '2026-08-31'}],
            'required_docs': [1], 'agency': '아산시청', 'amount': 11000, 'payment_methods': None,
        }
        for key, value in wrong.items():
            with self.subTest(key=key):
                self.assert_fails(response=json_response({**TAX_PAYLOAD, key: value}))

    def test_empty_required_value(self):
        for key, value in (('doc_type', ''), ('title', '  '), ('translated_text', ''), ('summary', [])):
            with self.subTest(key=key):
                self.assert_fails(response=json_response({**TAX_PAYLOAD, key: value}))


# 더미 OCR 원문(pipeline.DUMMY_OCR_TEXT)에 맞춘 AI 응답
PIPELINE_PAYLOAD = {
    **TAX_PAYLOAD,
    'title': '2026년도 주민세(개인분) 납부 안내',
    'deadlines': [{'label': '납부 기한', 'date': '2026-08-31', 'kind': 'payment'}],
    'agency': {'name': '아산시청 세무과', 'phone': '041-000-0000'},
    'amount': '6,250원',
    'payment_methods': ['은행 창구', '가상계좌', '위택스'],
}

pipeline_settings = override_settings(
    DOCUMENTS_STEP_DELAY=0, OCR_MODE='dummy', MASK_MODE='dummy', DOCUMENTS_VERIFY_MODE='dummy',
    INTERPRET_API_KEY='test-key', INTERPRET_MODEL='test-model',
)


@pipeline_settings
class InterpretPipelineTests(TestCase):
    def setUp(self):
        User = get_user_model()
        user = User.objects.create_user(**{User.USERNAME_FIELD: 'owner@example.com'}, password='test-password')
        self.document = Document.objects.create(
            owner=user, file='documents/notice.jpg', file_name='notice.jpg',
            file_type=Document.FileType.IMAGE, step=Document.Step.QUEUED,
        )

    def assert_failed_at_interpreting(self, logs):
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.FAILED)
        self.assertEqual(self.document.step, Document.Step.INTERPRETING)
        self.assertEqual(self.document.fail_reason, pipeline.FAIL_MESSAGES[Document.Step.INTERPRETING])
        self.assertFalse(InterpretationResult.objects.exists())
        # 실패 로그에 문서 내용·AI 응답·키가 남지 않는다.
        logged = '\n'.join(logs.output)
        for secret in ('6,250', '아산시청', '041-000-0000', 'test-key', 'Thông báo'):
            self.assertNotIn(secret, logged)

    @override_settings(INTERPRET_MODE='llm')
    def test_llm_result_is_saved(self):
        with MockedAnthropic(json_response(PIPELINE_PAYLOAD)) as mocked:
            process_document(self.document.id)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertEqual(self.document.title, '2026년도 주민세(개인분) 납부 안내')
        result = self.document.result
        self.assertEqual(result.amount, '6,250원')
        self.assertEqual(result.deadlines, [{'label': '납부 기한', 'date': '2026-08-31'}])
        self.assertEqual(result.agency['phone'], '041-000-0000')
        self.assertEqual(result.language, pipeline.DEFAULT_LANGUAGE)
        self.assertEqual(self.document.checklist_items.count(), 0)
        # AI로 나간 글자와 저장한 원문은 마스킹된 글자다.
        sent = mocked.create.call_args.kwargs['messages'][0]['content']
        self.assertNotIn('900101-5123456', sent)
        self.assertNotIn('900101-5123456', result.original_text)
        self.assertIn('******-*******', sent)

    @override_settings(INTERPRET_MODE='llm')
    def test_ai_failure_marks_failed(self):
        errors = {
            'timeout': {'error': sdk_error(anthropic.APITimeoutError)},
            'connection': {'error': sdk_error(anthropic.APIConnectionError)},
            'server error': {'error': sdk_error(anthropic.InternalServerError, status_code=500)},
            'not json': {'response': text_response('아산시청 문서입니다.')},
            'wrong format': {'response': json_response({**PIPELINE_PAYLOAD, 'summary': '한 문장'})},
            'truncated': {'response': json_response(PIPELINE_PAYLOAD, stop_reason='max_tokens')},
        }
        for name, mocked in errors.items():
            with self.subTest(name=name):
                Document.objects.filter(id=self.document.id).update(status=Document.Status.PENDING)

                with MockedAnthropic(**mocked), self.assertLogs(pipeline.logger, 'ERROR') as logs:
                    process_document(self.document.id)

                self.assert_failed_at_interpreting(logs)

    @override_settings(INTERPRET_MODE='llm', INTERPRET_API_KEY='')
    def test_missing_api_key_marks_failed(self):
        with MockedAnthropic() as mocked, self.assertLogs(pipeline.logger, 'ERROR') as logs:
            process_document(self.document.id)

        self.assert_failed_at_interpreting(logs)
        mocked.client_class.assert_not_called()

    @override_settings(INTERPRET_MODE='claude')
    def test_unknown_mode_marks_failed(self):
        with MockedAnthropic() as mocked, self.assertLogs(pipeline.logger, 'ERROR') as logs:
            process_document(self.document.id)

        self.assert_failed_at_interpreting(logs)
        mocked.client_class.assert_not_called()

    @override_settings(INTERPRET_MODE='dummy')
    def test_dummy_mode_does_not_call_ai(self):
        with MockedAnthropic() as mocked:
            process_document(self.document.id)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertEqual(self.document.title, '주민세 납부 안내')
        self.assertEqual(self.document.result.amount, '6,250원')
        mocked.client_class.assert_not_called()

    def test_default_mode_is_dummy(self):
        # .env에 INTERPRET_MODE가 없으면 dummy다. 테스트 실행 환경에 값이 남아 있으면 여기서 드러난다.
        with override_settings():
            from django.conf import settings

            del settings.INTERPRET_MODE
            self.assertEqual(pipeline.interpret('원문', 'vi')['title'], '주민세 납부 안내')
