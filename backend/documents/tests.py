import shutil
import tempfile
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.fields.files import FieldFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import UserProfile
from chatbot.models import ChatMessage

from .models import ChecklistItem, Document, InterpretationResult, Schedule
from .serializers import MAX_FILE_SIZE, normalize_verification
from .services import pipeline
from .services.pipeline import process_document
from .services.runner import start_processing
from .views import DocumentDetailView

TEMP_MEDIA_ROOT = tempfile.mkdtemp()
UPLOAD_URL = '/api/documents/'
RESULT_KEYS = {
    'documentId', 'docType', 'title', 'originalText', 'translatedText',
    'summary', 'terms', 'deadlines', 'requiredDocs', 'agency', 'amount', 'paymentMethods',
    'verification', 'maskedCount', 'lowConfidence',
}
VERIFICATION_KEYS = {'overall', 'status', 'items', 'sources'}
VERIFICATION_ITEM_KEYS = {'key', 'label', 'docValue', 'officialValue', 'status', 'reason', 'sourceIds'}

test_settings = override_settings(
    MEDIA_ROOT=TEMP_MEDIA_ROOT,
    DOCUMENTS_PROCESS_SYNC=True,
    DOCUMENTS_STEP_DELAY=0,
)


def create_user(email):
    User = get_user_model()
    return User.objects.create_user(**{User.USERNAME_FIELD: email}, password='test-password')


def create_document(owner, **fields):
    return Document.objects.create(
        owner=owner,
        file='documents/notice.jpg',
        file_name='notice.jpg',
        file_type=Document.FileType.IMAGE,
        step=Document.Step.QUEUED,
        **fields,
    )


def status_url(document_id):
    return f'/api/documents/{document_id}/status/'


def result_url(document_id):
    return f'/api/documents/{document_id}/result/'


@test_settings
class DocumentUploadTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = create_user('owner@example.com')
        self.client.force_authenticate(self.user)

    def upload(self, name, content=b'test', content_type='application/octet-stream'):
        file = SimpleUploadedFile(name, content, content_type=content_type)
        return self.client.post(UPLOAD_URL, {'file': file}, format='multipart')

    def test_upload_image(self):
        response = self.upload('notice.jpg', content_type='image/jpeg')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['status'], 'PENDING')
        document = Document.objects.get(id=response.json()['documentId'])
        self.assertEqual(document.owner, self.user)
        self.assertEqual(document.file_name, 'notice.jpg')
        self.assertEqual(document.file_type, Document.FileType.IMAGE)
        self.assertEqual(document.step, Document.Step.QUEUED)
        self.assertAlmostEqual(
            document.expires_at - document.created_at,
            timedelta(days=30),
            delta=timedelta(seconds=1),
        )

    def test_upload_pdf(self):
        response = self.upload('notice.PDF', content_type='application/pdf')

        self.assertEqual(response.status_code, 201)
        document = Document.objects.get(id=response.json()['documentId'])
        self.assertEqual(document.file_type, Document.FileType.PDF)

    def test_rejects_disallowed_extension(self):
        for name in ('notice.txt', 'notice.gif', 'notice'):
            with self.subTest(name=name):
                response = self.upload(name)

                self.assertEqual(response.status_code, 400)
                self.assertIn('file', response.json()['details'])
        self.assertFalse(Document.objects.exists())

    def test_rejects_file_over_20mb(self):
        response = self.upload('large.png', b'0' * (MAX_FILE_SIZE + 1), 'image/png')

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.json()['details'])
        self.assertFalse(Document.objects.exists())

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.upload('notice.jpg', content_type='image/jpeg')

        self.assertEqual(response.status_code, 401)
        self.assertFalse(Document.objects.exists())

    def test_upload_starts_processing_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.upload('notice.jpg', content_type='image/jpeg')

        document = Document.objects.get(id=response.json()['documentId'])
        self.assertEqual(document.status, Document.Status.DONE)
        self.assertEqual(document.step, Document.Step.DONE)
        self.assertTrue(InterpretationResult.objects.filter(document=document).exists())


@test_settings
class DocumentStatusTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = create_document(self.user)

    def test_owner_can_view_status(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(status_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'documentId': self.document.id, 'status': 'PENDING', 'step': 'QUEUED'},
        )

    def test_failed_status_includes_fail_reason(self):
        self.client.force_authenticate(self.user)
        with (
            mock.patch.object(pipeline, 'run_ocr', side_effect=RuntimeError('ocr down')),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            process_document(self.document.id)

        response = self.client.get(status_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'FAILED')
        self.assertEqual(response.json()['step'], 'OCR')
        self.assertEqual(response.json()['failReason'], pipeline.FAIL_MESSAGES[Document.Step.OCR])

    def test_other_user_gets_404(self):
        self.client.force_authenticate(create_user('other@example.com'))

        response = self.client.get(status_url(self.document.id))

        self.assertEqual(response.status_code, 404)

    def test_missing_document_gets_404(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(status_url(self.document.id + 1))

        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        response = self.client.get(status_url(self.document.id))

        self.assertEqual(response.status_code, 401)


@test_settings
class DocumentProcessingTests(TestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = create_document(self.user)

    def test_process_document_saves_result(self):
        self.assertTrue(process_document(self.document.id))

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertEqual(self.document.step, Document.Step.DONE)
        self.assertEqual(self.document.doc_type, '고지서')
        self.assertEqual(self.document.title, '주민세 납부 안내')
        self.assertEqual(self.document.masked_count, 2)
        self.assertFalse(self.document.low_confidence)
        result = self.document.result
        self.assertNotIn('900101-5123456', result.original_text)
        self.assertEqual(result.amount, '6,250원')
        self.assertEqual(result.payment_methods, ['은행 창구', '가상계좌', '위택스'])
        self.assertEqual(result.verification['overall'], 'MISMATCH')
        self.assertEqual(result.verification['status'], 'MISMATCH')
        self.assertEqual(
            [item['key'] for item in result.verification['items']],
            ['AMOUNT', 'DEADLINE', 'PAYMENT_METHOD', 'AGENCY_CONTACT'],
        )
        self.assertEqual(result.language, pipeline.DEFAULT_LANGUAGE)

    def test_result_without_amount_and_payment_methods_saves_empty_values(self):
        output = pipeline.interpret('원문', 'vi')
        del output['amount'], output['payment_methods']

        with mock.patch.object(pipeline, 'interpret', return_value=output):
            process_document(self.document.id)

        result = InterpretationResult.objects.get(document=self.document)
        self.assertEqual(result.amount, '')
        self.assertEqual(result.payment_methods, [])

    def test_dummy_verification_source_ids_point_to_sources(self):
        verification = pipeline.verify(pipeline.interpret('원문', 'vi'))

        source_ids = {source['id'] for source in verification['sources']}
        statuses = [item['status'] for item in verification['items']]
        self.assertEqual(statuses.count('MATCHED'), 3)
        self.assertEqual(statuses.count('MISMATCH'), 1)
        for item in verification['items']:
            self.assertEqual(set(item), VERIFICATION_ITEM_KEYS)
            self.assertTrue(set(item['sourceIds']) <= source_ids)

    def interpret_spy(self):
        return mock.patch.object(pipeline, 'interpret', wraps=pipeline.interpret)

    def test_interpret_receives_profile_language(self):
        UserProfile.objects.create(user=self.user, language=UserProfile.Language.ENGLISH)

        with self.interpret_spy() as interpret:
            process_document(self.document.id)

        self.assertEqual(interpret.call_args.args[1], UserProfile.Language.ENGLISH)

    def test_blank_profile_language_uses_default(self):
        UserProfile.objects.create(user=self.user, language='')

        with self.interpret_spy() as interpret:
            process_document(self.document.id)

        self.assertEqual(interpret.call_args.args[1], pipeline.DEFAULT_LANGUAGE)

    def test_saved_language_matches_translation(self):
        # 더미 번역은 베트남어뿐이라 영어 프로필이어도 결과 언어는 vi로 저장한다.
        UserProfile.objects.create(user=self.user, language=UserProfile.Language.ENGLISH)

        process_document(self.document.id)

        result = self.document.result
        self.assertEqual(result.language, 'vi')
        self.assertIn('Thông báo nộp thuế', result.translated_text)

    def test_saved_language_follows_interpret_output(self):
        translated = {**pipeline.interpret('', 'en'), 'language': 'en', 'translated_text': 'Resident tax notice'}

        with mock.patch.object(pipeline, 'interpret', return_value=translated):
            process_document(self.document.id)

        self.assertEqual(self.document.result.language, 'en')

    def test_steps_advance_in_order(self):
        seen = []

        def record_step(original):
            def wrapper(*args):
                seen.append(Document.objects.get(id=self.document.id).step)
                return original(*args)
            return wrapper

        with (
            mock.patch.object(pipeline, 'run_ocr', record_step(pipeline.run_ocr)),
            mock.patch.object(pipeline, 'mask_personal_info', record_step(pipeline.mask_personal_info)),
            mock.patch.object(pipeline, 'interpret', record_step(pipeline.interpret)),
            mock.patch.object(pipeline, 'verify', record_step(pipeline.verify)),
        ):
            process_document(self.document.id)

        self.assertEqual(seen, ['OCR', 'MASKING', 'INTERPRETING', 'VERIFYING'])

    def test_low_ocr_confidence_sets_flag(self):
        low = {'text': pipeline.DUMMY_OCR_TEXT, 'confidence': 0.3}
        with mock.patch.object(pipeline, 'run_ocr', return_value=low):
            process_document(self.document.id)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertTrue(self.document.low_confidence)

    def test_exception_marks_failed(self):
        with (
            mock.patch.object(pipeline, 'interpret', side_effect=RuntimeError('llm down')),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            process_document(self.document.id)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.FAILED)
        self.assertEqual(self.document.step, Document.Step.INTERPRETING)
        self.assertEqual(self.document.fail_reason, pipeline.FAIL_MESSAGES[Document.Step.INTERPRETING])
        self.assertNotIn('llm down', self.document.fail_reason)
        self.assertFalse(InterpretationResult.objects.exists())

    def test_does_not_start_processing_or_done_document(self):
        for current in (Document.Status.PROCESSING, Document.Status.DONE):
            with self.subTest(status=current):
                Document.objects.filter(id=self.document.id).update(status=current)

                with mock.patch.object(pipeline, 'run_ocr') as run_ocr:
                    self.assertFalse(start_processing(self.document.id))
                    self.assertFalse(process_document(self.document.id))

                run_ocr.assert_not_called()

    def test_second_start_is_ignored(self):
        self.assertTrue(start_processing(self.document.id))

        with mock.patch.object(pipeline, 'run_ocr') as run_ocr:
            self.assertFalse(start_processing(self.document.id))

        run_ocr.assert_not_called()

    def test_failed_document_can_be_retried(self):
        with (
            mock.patch.object(pipeline, 'verify', side_effect=RuntimeError),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            process_document(self.document.id)

        self.assertTrue(start_processing(self.document.id))

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertEqual(self.document.fail_reason, '')

    @override_settings(DOCUMENTS_PROCESS_SYNC=False)
    def test_runs_in_background_thread_by_default(self):
        with mock.patch('documents.services.runner.threading.Thread') as thread:
            self.assertTrue(start_processing(self.document.id))

        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs['args'], (self.document.id,))
        thread.return_value.start.assert_called_once()


@test_settings
class DocumentResultTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = create_document(self.user)
        self.client.force_authenticate(self.user)

    def test_done_document_returns_result(self):
        process_document(self.document.id)

        response = self.client.get(result_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), RESULT_KEYS)
        self.assertEqual(data['documentId'], self.document.id)
        self.assertEqual(data['docType'], '고지서')
        self.assertEqual(data['deadlines'], [{'label': '납부 마감', 'date': '2026-08-31'}])
        self.assertEqual(data['agency']['source'], 'document')
        self.assertEqual(data['amount'], '6,250원')
        self.assertEqual(data['paymentMethods'], ['은행 창구', '가상계좌', '위택스'])
        self.assertEqual(set(data['verification']), VERIFICATION_KEYS)
        self.assertEqual(data['verification']['overall'], 'MISMATCH')
        self.assertEqual(data['verification']['status'], 'MISMATCH')
        self.assertEqual(len(data['verification']['items']), 4)
        self.assertEqual([source['id'] for source in data['verification']['sources']], ['s1', 's2'])
        self.assertEqual(data['maskedCount'], 2)
        self.assertFalse(data['lowConfidence'])

    def test_legacy_result_is_normalized(self):
        process_document(self.document.id)
        InterpretationResult.objects.filter(document=self.document).update(
            amount='', payment_methods=[],
            verification={'status': 'MATCHED', 'sources': [
                {'title': '아산시 지방세 납부 안내', 'url': 'https://www.asan.go.kr/', 'checkedAt': '2026-10-30'},
                {'title': '두 번째 자료', 'url': 'https://www.asan.go.kr/', 'checkedAt': '2026-10-30',
                 'sourceDocumentId': 3},
            ]},
        )

        data = self.client.get(result_url(self.document.id)).json()

        self.assertIsNone(data['amount'])
        self.assertEqual(data['paymentMethods'], [])
        self.assertEqual(data['verification'], {
            'overall': 'MATCHED', 'status': 'MATCHED', 'items': [],
            'sources': [
                {'id': 's1', 'title': '아산시 지방세 납부 안내', 'url': 'https://www.asan.go.kr/',
                 'checkedAt': '2026-10-30'},
                {'id': 's2', 'title': '두 번째 자료', 'url': 'https://www.asan.go.kr/', 'checkedAt': '2026-10-30',
                 'sourceDocumentId': 3},
            ],
        })
        # 저장된 값은 바꾸지 않고 응답에서만 맞춘다.
        stored = InterpretationResult.objects.get(document=self.document).verification
        self.assertNotIn('overall', stored)

    def test_unfinished_document_returns_409(self):
        for current, step in (
            (Document.Status.PENDING, Document.Step.QUEUED),
            (Document.Status.PROCESSING, Document.Step.INTERPRETING),
        ):
            with self.subTest(status=current):
                Document.objects.filter(id=self.document.id).update(status=current, step=step)

                response = self.client.get(result_url(self.document.id))

                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json(),
                    {'detail': '아직 처리 중입니다.', 'status': current, 'step': step},
                )

    def test_failed_document_returns_409_with_fail_reason(self):
        with (
            mock.patch.object(pipeline, 'mask_personal_info', side_effect=RuntimeError),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            process_document(self.document.id)

        response = self.client.get(result_url(self.document.id))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'FAILED')
        self.assertEqual(response.json()['step'], 'MASKING')
        self.assertEqual(response.json()['failReason'], pipeline.FAIL_MESSAGES[Document.Step.MASKING])

    def test_other_user_gets_404(self):
        process_document(self.document.id)
        self.client.force_authenticate(create_user('other@example.com'))

        response = self.client.get(result_url(self.document.id))

        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.get(result_url(self.document.id))

        self.assertEqual(response.status_code, 401)


def verification_url(document_id):
    return f'/api/documents/{document_id}/verification/'


class NormalizeVerificationTests(TestCase):
    def test_invalid_values_become_unverified(self):
        for stored in (None, [], {}, {'status': 'UNKNOWN'}, {'overall': 'BAD', 'items': 'x', 'sources': 'y'}):
            with self.subTest(stored=stored):
                self.assertEqual(
                    normalize_verification(stored),
                    {'overall': 'UNVERIFIED', 'status': 'UNVERIFIED', 'items': [], 'sources': []},
                )

    def test_new_format_is_kept(self):
        verification = pipeline.verify({})

        self.assertEqual(normalize_verification(verification), verification)

    def test_non_list_sources_become_empty(self):
        for sources in (7, 'abc', {'id': 's1'}):
            with self.subTest(sources=sources):
                normalized = normalize_verification({'status': 'MATCHED', 'sources': sources})

                self.assertEqual(normalized['sources'], [])
                self.assertEqual(normalized['overall'], 'MATCHED')

    def test_generated_source_ids_do_not_collide(self):
        normalized = normalize_verification({'status': 'MATCHED', 'sources': [
            {'id': 's2', 'title': 'A'}, {'title': 'B'}, {'id': '', 'title': 'C'}, {'id': 's1', 'title': 'D'},
        ]})

        self.assertEqual([source['id'] for source in normalized['sources']], ['s2', 's3', 's4', 's1'])

    def test_invalid_items_are_dropped_and_overall_becomes_unverified(self):
        valid = pipeline.verify({})['items'][0]
        broken = [{}, {**valid, 'key': 'OTHER'}, {**valid, 'status': 'OK'}, {**valid, 'sourceIds': 's1'},
                  {**valid, 'officialValue': 3}, 'text']
        for item in broken:
            with self.subTest(item=item):
                normalized = normalize_verification({'overall': 'MATCHED', 'items': [valid, item], 'sources': []})

                self.assertEqual(normalized['items'], [valid])
                self.assertEqual(normalized['overall'], 'UNVERIFIED')
                self.assertEqual(normalized['status'], 'UNVERIFIED')

    def test_non_list_items_make_overall_unverified(self):
        for items in ('broken', 3, {'key': 'AMOUNT'}):
            with self.subTest(items=items):
                normalized = normalize_verification({'overall': 'MATCHED', 'items': items, 'sources': []})

                self.assertEqual(normalized['items'], [])
                self.assertEqual(normalized['overall'], 'UNVERIFIED')

    def test_missing_items_keep_legacy_status(self):
        for stored in ({'status': 'MATCHED'}, {'status': 'MATCHED', 'items': None}):
            with self.subTest(stored=stored):
                self.assertEqual(normalize_verification(stored)['overall'], 'MATCHED')

    def test_null_official_value_is_valid(self):
        item = {**pipeline.verify({})['items'][0], 'officialValue': None, 'status': 'UNVERIFIED'}

        normalized = normalize_verification({'overall': 'UNVERIFIED', 'items': [item], 'sources': []})

        self.assertEqual(normalized['items'], [item])

    def test_status_follows_overall(self):
        normalized = normalize_verification({'overall': 'MATCHED', 'status': 'UNVERIFIED', 'items': [], 'sources': []})

        self.assertEqual(normalized['status'], 'MATCHED')


@test_settings
class DocumentVerificationTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = create_document(self.user)
        self.client.force_authenticate(self.user)

    def test_returns_verification_block(self):
        process_document(self.document.id)

        response = self.client.get(verification_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), VERIFICATION_KEYS)
        self.assertEqual(data, pipeline.verify({}))
        self.assertEqual(data, self.client.get(result_url(self.document.id)).json()['verification'])

    def test_legacy_verification_is_normalized(self):
        process_document(self.document.id)
        InterpretationResult.objects.filter(document=self.document).update(verification={
            'status': 'UNVERIFIED', 'sources': [],
        })

        response = self.client.get(verification_url(self.document.id))

        self.assertEqual(response.json(), {'overall': 'UNVERIFIED', 'status': 'UNVERIFIED', 'items': [], 'sources': []})

    def test_unfinished_document_returns_same_409_as_result(self):
        for current, step in (
            (Document.Status.PENDING, Document.Step.QUEUED),
            (Document.Status.PROCESSING, Document.Step.VERIFYING),
        ):
            with self.subTest(status=current):
                Document.objects.filter(id=self.document.id).update(status=current, step=step)

                response = self.client.get(verification_url(self.document.id))

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'detail': '아직 처리 중입니다.', 'status': current, 'step': step})

    def test_failed_document_returns_409_with_fail_reason(self):
        Document.objects.filter(id=self.document.id).update(
            status=Document.Status.FAILED, step=Document.Step.OCR, fail_reason='읽지 못했습니다.',
        )

        response = self.client.get(verification_url(self.document.id))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['failReason'], '읽지 못했습니다.')

    def test_other_user_and_missing_document_get_404(self):
        process_document(self.document.id)
        self.client.force_authenticate(create_user('other@example.com'))

        other = self.client.get(verification_url(self.document.id))
        missing = self.client.get(verification_url(self.document.id + 100))

        self.assertEqual(other.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other.json(), missing.json())

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.get(verification_url(self.document.id))

        self.assertEqual(response.status_code, 401)


def detail_url(document_id):
    return f'/api/documents/{document_id}/'


LIST_KEYS = {
    'documentId', 'title', 'docType', 'fileName', 'fileType', 'status', 'step',
    'lowConfidence', 'createdAt', 'expiresAt',
}


@test_settings
class DocumentListTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.client.force_authenticate(self.user)

    def test_lists_only_my_documents_newest_first(self):
        mine = [create_document(self.user) for _ in range(3)]
        create_document(create_user('other@example.com'))

        response = self.client.get(UPLOAD_URL)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)
        self.assertEqual([item['documentId'] for item in data['results']], [d.id for d in reversed(mine)])
        self.assertEqual(set(data['results'][0]), LIST_KEYS)

    def test_list_item_fields(self):
        document = create_document(self.user)
        process_document(document.id)

        item = self.client.get(UPLOAD_URL).json()['results'][0]

        self.assertEqual(item['title'], '주민세 납부 안내')
        self.assertEqual(item['docType'], '고지서')
        self.assertEqual(item['fileName'], 'notice.jpg')
        self.assertEqual(item['fileType'], 'IMAGE')
        self.assertEqual(item['status'], 'DONE')
        self.assertEqual(item['step'], 'DONE')
        self.assertFalse(item['lowConfidence'])
        self.assertIsNotNone(item['expiresAt'])

    def test_paginates(self):
        for _ in range(21):
            create_document(self.user)

        first = self.client.get(UPLOAD_URL).json()
        second = self.client.get(UPLOAD_URL, {'page': 2}).json()
        small = self.client.get(UPLOAD_URL, {'page_size': 5}).json()

        self.assertEqual(first['count'], 21)
        self.assertEqual(len(first['results']), 20)
        self.assertIsNotNone(first['next'])
        self.assertEqual(len(second['results']), 1)
        self.assertEqual(len(small['results']), 5)

    def test_missing_page_gets_404(self):
        create_document(self.user)

        response = self.client.get(UPLOAD_URL, {'page': 2})

        self.assertEqual(response.status_code, 404)

    def test_filters_by_status(self):
        pending = create_document(self.user)
        failed = create_document(self.user, status=Document.Status.FAILED)

        for value, expected in (('PENDING', [pending.id]), ('FAILED', [failed.id]), ('DONE', [])):
            with self.subTest(status=value):
                response = self.client.get(UPLOAD_URL, {'status': value})

                self.assertEqual(response.status_code, 200)
                self.assertEqual([item['documentId'] for item in response.json()['results']], expected)

    def test_rejects_unknown_status(self):
        response = self.client.get(UPLOAD_URL, {'status': 'done'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')
        self.assertIn('status', response.json()['details'])

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.get(UPLOAD_URL)

        self.assertEqual(response.status_code, 401)


@test_settings
class DocumentDetailTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = create_document(self.user)
        self.client.force_authenticate(self.user)

    def test_done_document_detail(self):
        process_document(self.document.id)

        response = self.client.get(detail_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), LIST_KEYS | {'failReason', 'maskedCount', 'hasResult'})
        self.assertEqual(data['documentId'], self.document.id)
        self.assertEqual(data['status'], 'DONE')
        self.assertEqual(data['failReason'], '')
        self.assertEqual(data['maskedCount'], 2)
        self.assertTrue(data['hasResult'])

    def test_pending_document_has_no_result(self):
        data = self.client.get(detail_url(self.document.id)).json()

        self.assertEqual(data['status'], 'PENDING')
        self.assertFalse(data['hasResult'])

    def test_failed_document_includes_fail_reason(self):
        with (
            mock.patch.object(pipeline, 'run_ocr', side_effect=RuntimeError('ocr down')),
            self.assertLogs(pipeline.logger, 'ERROR'),
        ):
            process_document(self.document.id)

        data = self.client.get(detail_url(self.document.id)).json()

        self.assertEqual(data['status'], 'FAILED')
        self.assertEqual(data['failReason'], pipeline.FAIL_MESSAGES[Document.Step.OCR])
        self.assertFalse(data['hasResult'])

    def test_other_user_and_missing_document_get_404(self):
        self.client.force_authenticate(create_user('other@example.com'))
        other = self.client.get(detail_url(self.document.id))
        missing = self.client.get(detail_url(self.document.id + 1))

        self.assertEqual(other.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other.json(), missing.json())

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.get(detail_url(self.document.id))

        self.assertEqual(response.status_code, 401)


@test_settings
class DocumentDeleteTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.client.force_authenticate(self.user)
        self.document = Document.objects.create(
            owner=self.user,
            file=SimpleUploadedFile('notice.jpg', b'test', content_type='image/jpeg'),
            file_name='notice.jpg',
            file_type=Document.FileType.IMAGE,
            step=Document.Step.QUEUED,
        )
        self.storage = self.document.file.storage
        self.file_name = self.document.file.name

    def test_deletes_document_result_chat_and_file(self):
        process_document(self.document.id)
        ChatMessage.objects.create(document=self.document, question='질문', answer='답변', language='ko')
        self.assertTrue(self.storage.exists(self.file_name))

        response = self.client.delete(detail_url(self.document.id))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Document.objects.filter(id=self.document.id).exists())
        self.assertFalse(InterpretationResult.objects.filter(document_id=self.document.id).exists())
        self.assertFalse(ChatMessage.objects.filter(document_id=self.document.id).exists())
        self.assertFalse(self.storage.exists(self.file_name))

    def test_deletes_pending_and_failed_documents(self):
        for current in (Document.Status.PENDING, Document.Status.FAILED):
            with self.subTest(status=current):
                document = create_document(self.user, status=current)

                response = self.client.delete(detail_url(document.id))

                self.assertEqual(response.status_code, 204)
                self.assertFalse(Document.objects.filter(id=document.id).exists())

    def test_processing_document_returns_409(self):
        Document.objects.filter(id=self.document.id).update(
            status=Document.Status.PROCESSING, step=Document.Step.OCR,
        )

        response = self.client.delete(detail_url(self.document.id))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            'detail': '처리 중인 문서는 삭제할 수 없습니다.', 'status': 'PROCESSING', 'step': 'OCR',
        })
        self.assertTrue(Document.objects.filter(id=self.document.id).exists())
        self.assertTrue(self.storage.exists(self.file_name))

    def test_processing_started_after_lookup_returns_409(self):
        get_object = DocumentDetailView.get_object

        def get_then_start(view):
            # 조회와 삭제 사이에 백그라운드 처리가 문서를 선점한 상황
            document = get_object(view)
            Document.objects.filter(id=document.id).update(
                status=Document.Status.PROCESSING, step=Document.Step.OCR,
            )
            return document

        with mock.patch.object(DocumentDetailView, 'get_object', get_then_start):
            response = self.client.delete(detail_url(self.document.id))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'PROCESSING')
        self.assertTrue(Document.objects.filter(id=self.document.id).exists())
        self.assertTrue(self.storage.exists(self.file_name))

    def test_file_delete_failure_still_returns_204(self):
        with (
            mock.patch.object(FieldFile, 'delete', side_effect=OSError('disk')),
            self.assertLogs('documents.views', 'ERROR'),
        ):
            response = self.client.delete(detail_url(self.document.id))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Document.objects.filter(id=self.document.id).exists())

    def test_other_user_and_missing_document_get_404(self):
        self.client.force_authenticate(create_user('other@example.com'))
        other = self.client.delete(detail_url(self.document.id))
        missing = self.client.delete(detail_url(self.document.id + 1))

        self.assertEqual(other.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other.json(), missing.json())
        self.assertTrue(Document.objects.filter(id=self.document.id).exists())

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.delete(detail_url(self.document.id))

        self.assertEqual(response.status_code, 401)
        self.assertTrue(Document.objects.filter(id=self.document.id).exists())


SCHEDULE_URL = '/api/schedules/'
TODAY = date(2026, 8, 20)
AGENCY = {'name': '아산시청 세무과', 'phone': '041-000-0000', 'source': 'document'}
SCHEDULE_KEYS = {
    'scheduleId', 'documentId', 'documentTitle', 'title', 'date', 'done', 'doneAt',
    'isOverdue', 'daysLeft', 'agency', 'createdAt', 'updatedAt',
}


def schedule_url(schedule_id):
    return f'{SCHEDULE_URL}{schedule_id}/'


def create_done_document(owner, deadlines=None, agency=None):
    document = create_document(
        owner, status=Document.Status.DONE, title='주민세 납부 안내', doc_type='고지서',
    )
    InterpretationResult.objects.create(
        document=document,
        title=document.title,
        deadlines=deadlines if deadlines is not None else [
            {'label': '납부 마감', 'date': '2026-08-31'},
            {'label': '이의 신청', 'date': '2026-08-10'},
        ],
        agency=AGENCY if agency is None else agency,
    )
    return document


def create_schedule(document, schedule_date, title='납부 마감', **fields):
    return Schedule.objects.create(
        owner=document.owner, document=document, date=schedule_date, title=title, **fields,
    )


class FixedTodayMixin:
    """isOverdue·daysLeft와 overdue 필터의 기준 날짜를 TODAY로 고정한다."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch('django.utils.timezone.localdate', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)


class ScheduleCreateTests(FixedTodayMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user('owner@example.com')
        self.document = create_done_document(self.user)
        self.client.force_authenticate(self.user)

    def test_create_with_default_title(self):
        response = self.client.post(
            SCHEDULE_URL, {'documentId': self.document.id, 'date': '2026-08-31'}, format='json',
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(set(data), SCHEDULE_KEYS)
        self.assertEqual(data['documentId'], self.document.id)
        self.assertEqual(data['documentTitle'], '주민세 납부 안내')
        self.assertEqual(data['title'], '납부 마감 - 주민세 납부 안내')
        self.assertEqual(data['date'], '2026-08-31')
        self.assertFalse(data['done'])
        self.assertIsNone(data['doneAt'])
        self.assertFalse(data['isOverdue'])
        self.assertEqual(data['daysLeft'], 11)
        self.assertEqual(data['agency'], AGENCY)
        schedule = Schedule.objects.get(id=data['scheduleId'])
        self.assertEqual(schedule.owner, self.user)
        self.assertEqual(schedule.date, date(2026, 8, 31))

    def test_create_with_title(self):
        response = self.client.post(SCHEDULE_URL, {
            'documentId': self.document.id, 'date': '2026-08-31', 'title': '  주민세 내기  ',
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['title'], '주민세 내기')

    def test_unfinished_document_returns_400(self):
        for current in (Document.Status.PENDING, Document.Status.PROCESSING, Document.Status.FAILED):
            with self.subTest(status=current):
                Document.objects.filter(id=self.document.id).update(status=current)

                response = self.client.post(
                    SCHEDULE_URL, {'documentId': self.document.id, 'date': '2026-08-31'}, format='json',
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')
                self.assertIn('documentId', response.json()['details'])
        self.assertFalse(Schedule.objects.exists())

    def test_date_not_in_deadlines_returns_400(self):
        response = self.client.post(
            SCHEDULE_URL, {'documentId': self.document.id, 'date': '2026-09-01'}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('date', response.json()['details'])
        self.assertFalse(Schedule.objects.exists())

    def test_missing_fields_return_400(self):
        response = self.client.post(SCHEDULE_URL, {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()['details']), {'documentId', 'date'})

    def test_other_user_and_missing_document_get_404(self):
        other_document = create_done_document(create_user('other@example.com'))

        other = self.client.post(
            SCHEDULE_URL, {'documentId': other_document.id, 'date': '2026-08-31'}, format='json',
        )
        missing = self.client.post(
            SCHEDULE_URL, {'documentId': other_document.id + 100, 'date': '2026-08-31'}, format='json',
        )

        self.assertEqual(other.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other.json(), missing.json())
        self.assertFalse(Schedule.objects.exists())

    def test_duplicate_returns_409(self):
        body = {'documentId': self.document.id, 'date': '2026-08-31'}
        first = self.client.post(SCHEDULE_URL, body, format='json')

        second = self.client.post(SCHEDULE_URL, body, format='json')

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json(), {
            'detail': '이미 등록된 일정입니다.', 'scheduleId': first.json()['scheduleId'],
        })
        self.assertEqual(Schedule.objects.count(), 1)

    def test_same_date_with_other_title_is_allowed(self):
        self.client.post(SCHEDULE_URL, {'documentId': self.document.id, 'date': '2026-08-31'}, format='json')

        response = self.client.post(SCHEDULE_URL, {
            'documentId': self.document.id, 'date': '2026-08-31', 'title': '서류 준비',
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Schedule.objects.count(), 2)

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.post(
            SCHEDULE_URL, {'documentId': self.document.id, 'date': '2026-08-31'}, format='json',
        )

        self.assertEqual(response.status_code, 401)


class ScheduleListTests(FixedTodayMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user('owner@example.com')
        self.client.force_authenticate(self.user)
        document = create_done_document(self.user)
        self.later = create_schedule(document, date(2026, 8, 31))
        self.overdue = create_schedule(document, date(2026, 8, 10), title='이의 신청')
        self.done_past = create_schedule(
            document, date(2026, 8, 1), title='완료한 일', done=True, done_at=timezone.now(),
        )
        self.today = create_schedule(document, TODAY, title='오늘 마감')
        create_schedule(create_done_document(create_user('other@example.com')), date(2026, 8, 25))

    def ids(self, response):
        return [item['scheduleId'] for item in response.json()['results']]

    def test_lists_only_mine_by_nearest_date(self):
        response = self.client.get(SCHEDULE_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 4)
        self.assertEqual(self.ids(response), [self.done_past.id, self.overdue.id, self.today.id, self.later.id])

    def test_overdue_and_days_left(self):
        items = {item['scheduleId']: item for item in self.client.get(SCHEDULE_URL).json()['results']}

        self.assertEqual((items[self.overdue.id]['isOverdue'], items[self.overdue.id]['daysLeft']), (True, -10))
        self.assertEqual((items[self.today.id]['isOverdue'], items[self.today.id]['daysLeft']), (False, 0))
        self.assertEqual((items[self.later.id]['isOverdue'], items[self.later.id]['daysLeft']), (False, 11))
        # 완료한 일정은 마감일이 지나도 경과로 보지 않는다.
        self.assertEqual((items[self.done_past.id]['isOverdue'], items[self.done_past.id]['daysLeft']), (False, -19))

    def test_done_filter(self):
        done = self.client.get(SCHEDULE_URL, {'done': 'true'})
        not_done = self.client.get(SCHEDULE_URL, {'done': 'false'})

        self.assertEqual(self.ids(done), [self.done_past.id])
        self.assertEqual(self.ids(not_done), [self.overdue.id, self.today.id, self.later.id])

    def test_overdue_filter(self):
        overdue = self.client.get(SCHEDULE_URL, {'overdue': 'true'})
        not_overdue = self.client.get(SCHEDULE_URL, {'overdue': 'false'})

        self.assertEqual(self.ids(overdue), [self.overdue.id])
        self.assertEqual(self.ids(not_overdue), [self.done_past.id, self.today.id, self.later.id])

    def test_invalid_filter_returns_400(self):
        for name in ('done', 'overdue'):
            with self.subTest(name=name):
                response = self.client.get(SCHEDULE_URL, {name: 'yes'})

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['details'], {name: ['true 또는 false를 입력하세요.']})

    def test_pagination(self):
        response = self.client.get(SCHEDULE_URL, {'page_size': 3})
        second = self.client.get(SCHEDULE_URL, {'page_size': 3, 'page': 2})

        self.assertEqual(response.json()['count'], 4)
        self.assertEqual(len(response.json()['results']), 3)
        self.assertIsNotNone(response.json()['next'])
        self.assertEqual(self.ids(second), [self.later.id])

    def test_agency_is_null_without_result_agency(self):
        Schedule.objects.all().delete()
        create_schedule(create_done_document(self.user, agency={}), date(2026, 8, 31))

        response = self.client.get(SCHEDULE_URL)

        self.assertIsNone(response.json()['results'][0]['agency'])

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(SCHEDULE_URL).status_code, 401)


class ScheduleDetailTests(FixedTodayMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user('owner@example.com')
        self.client.force_authenticate(self.user)
        self.document = create_done_document(self.user)
        self.schedule = create_schedule(self.document, date(2026, 8, 31))

    def test_detail(self):
        response = self.client.get(schedule_url(self.schedule.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), SCHEDULE_KEYS)
        self.assertEqual(response.json()['scheduleId'], self.schedule.id)

    def test_patch_done_toggles_done_at(self):
        done = self.client.patch(schedule_url(self.schedule.id), {'done': True}, format='json')

        self.assertEqual(done.status_code, 200)
        self.assertTrue(done.json()['done'])
        self.assertIsNotNone(done.json()['doneAt'])
        self.schedule.refresh_from_db()
        done_at = self.schedule.done_at
        self.assertIsNotNone(done_at)

        # 이미 완료한 일정은 처음 완료 시각을 유지한다.
        self.client.patch(schedule_url(self.schedule.id), {'done': True}, format='json')
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.done_at, done_at)

        undone = self.client.patch(schedule_url(self.schedule.id), {'done': False}, format='json')

        self.assertEqual(undone.status_code, 200)
        self.assertFalse(undone.json()['done'])
        self.assertIsNone(undone.json()['doneAt'])
        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.done_at)

    def test_patch_rejects_other_fields(self):
        for body in ({'done': True, 'date': '2026-09-01'}, {'title': '바꾼 이름'}):
            with self.subTest(body=body):
                response = self.client.patch(schedule_url(self.schedule.id), body, format='json')

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.done)
        self.assertEqual(self.schedule.title, '납부 마감')

    def test_patch_requires_done(self):
        response = self.client.patch(schedule_url(self.schedule.id), {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('done', response.json()['details'])

    def test_put_not_allowed(self):
        response = self.client.put(schedule_url(self.schedule.id), {'done': True}, format='json')

        self.assertEqual(response.status_code, 405)

    def test_delete(self):
        response = self.client.delete(schedule_url(self.schedule.id))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Schedule.objects.filter(id=self.schedule.id).exists())
        self.assertTrue(Document.objects.filter(id=self.document.id).exists())

    def test_document_delete_cascades_to_schedules(self):
        self.document.delete()

        self.assertFalse(Schedule.objects.filter(id=self.schedule.id).exists())

    def test_other_user_and_missing_schedule_get_404(self):
        self.client.force_authenticate(create_user('other@example.com'))
        url = schedule_url(self.schedule.id)
        missing_url = schedule_url(self.schedule.id + 100)

        for method, body in (('get', None), ('patch', {'done': True}), ('delete', None)):
            with self.subTest(method=method):
                other = getattr(self.client, method)(url, body, format='json')
                missing = getattr(self.client, method)(missing_url, body, format='json')

                self.assertEqual(other.status_code, 404)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(other.json(), missing.json())
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.done)

    def test_former_owner_cannot_read_reassigned_document_schedule(self):
        Document.objects.filter(id=self.document.id).update(owner=create_user('new@example.com'))

        self.assertEqual(self.client.get(SCHEDULE_URL).json()['count'], 0)
        for method, body in (('get', None), ('patch', {'done': True}), ('delete', None)):
            with self.subTest(method=method):
                response = getattr(self.client, method)(schedule_url(self.schedule.id), body, format='json')

                self.assertEqual(response.status_code, 404)
        self.assertTrue(Schedule.objects.filter(id=self.schedule.id).exists())

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(schedule_url(self.schedule.id)).status_code, 401)
        self.assertEqual(self.client.delete(schedule_url(self.schedule.id)).status_code, 401)
        self.assertTrue(Schedule.objects.filter(id=self.schedule.id).exists())


CHECKLIST_ITEM_KEYS = {
    'id', 'text', 'detail', 'docs', 'method', 'due', 'state', 'done', 'doneAt', 'note', 'sourceIds',
}


def checklist_url(document_id):
    return f'/api/documents/{document_id}/checklist/'


def checklist_item_url(document_id, item_id):
    return f'/api/documents/{document_id}/checklist/{item_id}/'


def interpret_with_checklist(checklist):
    output = pipeline.interpret('원문', 'vi')
    output['checklist'] = checklist
    return mock.patch.object(pipeline, 'interpret', return_value=output)


@test_settings
class ChecklistPipelineTests(TestCase):
    def setUp(self):
        self.document = create_document(create_user('owner@example.com'))

    def test_process_document_creates_items(self):
        process_document(self.document.id)

        items = list(self.document.checklist_items.all())
        self.assertEqual([item.order for item in items], [0, 1, 2, 3])
        self.assertEqual(
            {item.state for item in items},
            {'CONFIRMED', 'CONDITIONAL', 'CHECK_NEEDED'},
        )
        first = items[0]
        self.assertEqual(first.text, '주민세 6,250원 내기')
        self.assertEqual(first.docs, ['고지서'])
        self.assertEqual(first.due, date(2026, 8, 31))
        self.assertEqual(first.source_ids, ['s1'])
        self.assertFalse(first.done)

    def test_reprocessing_replaces_items(self):
        process_document(self.document.id)
        old_ids = set(self.document.checklist_items.values_list('id', flat=True))
        self.document.checklist_items.update(done=True, done_at=timezone.now())
        Document.objects.filter(id=self.document.id).update(status=Document.Status.FAILED)

        with interpret_with_checklist([{'text': '새 할 일', 'state': 'CONFIRMED'}]):
            process_document(self.document.id)

        items = list(self.document.checklist_items.all())
        self.assertEqual([item.text for item in items], ['새 할 일'])
        self.assertFalse(items[0].done)
        self.assertFalse(old_ids & {item.id for item in items})

    def test_invalid_checklist_keeps_document_done(self):
        for checklist in (None, 'abc', 3, {'text': '할 일'}, [1, 'x', {}, {'text': '  '}]):
            with self.subTest(checklist=checklist):
                Document.objects.filter(id=self.document.id).update(status=Document.Status.PENDING)

                with interpret_with_checklist(checklist):
                    process_document(self.document.id)

                self.document.refresh_from_db()
                self.assertEqual(self.document.status, Document.Status.DONE)
                self.assertFalse(self.document.checklist_items.exists())

    def test_missing_checklist_key_keeps_document_done(self):
        output = pipeline.interpret('원문', 'vi')
        del output['checklist']

        with mock.patch.object(pipeline, 'interpret', return_value=output):
            process_document(self.document.id)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        self.assertFalse(self.document.checklist_items.exists())

    def test_invalid_fields_are_cleaned(self):
        with interpret_with_checklist([
            {'text': '할 일 1', 'state': 'DONE', 'due': '2026/08/31', 'docs': '고지서', 'sourceIds': 's1',
             'done': True, 'detail': 3},
            {'text': '할 일 2', 'state': 'CONDITIONAL', 'due': '2026-02-30', 'docs': ['', '여권', 7]},
            {'text': 'x' * 400, 'due': None},
        ]):
            process_document(self.document.id)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, Document.Status.DONE)
        first, second, third = self.document.checklist_items.all()
        self.assertEqual((first.state, first.due, first.docs, first.source_ids), ('CHECK_NEEDED', None, ['고지서'], ['s1']))
        self.assertFalse(first.done)
        self.assertEqual(first.detail, '')
        self.assertEqual((second.state, second.due, second.docs), ('CONDITIONAL', None, ['여권']))
        self.assertEqual(len(third.text), 300)
        self.assertEqual(third.state, 'CHECK_NEEDED')

    def test_document_delete_removes_items(self):
        process_document(self.document.id)

        self.document.delete()

        self.assertFalse(ChecklistItem.objects.exists())


@test_settings
class ChecklistApiTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = create_document(self.user)
        process_document(self.document.id)
        self.items = list(self.document.checklist_items.all())
        self.client.force_authenticate(self.user)

    def test_returns_items_in_order(self):
        response = self.client.get(checklist_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['documentId'], self.document.id)
        self.assertEqual((data['total'], data['doneCount'], data['progress']), (4, 0, 0))
        self.assertEqual([item['id'] for item in data['items']], [item.id for item in self.items])
        self.assertEqual(set(data['items'][0]), CHECKLIST_ITEM_KEYS)
        self.assertEqual(data['items'][0]['due'], '2026-08-31')
        self.assertIsNone(data['items'][1]['due'])
        self.assertEqual(data['items'][2]['state'], 'CHECK_NEEDED')
        self.assertEqual(data['items'][2]['sourceIds'], ['s2'])

    def test_progress_rounds_down(self):
        ChecklistItem.objects.filter(id=self.items[0].id).update(done=True)
        self.assertEqual(self.client.get(checklist_url(self.document.id)).json()['progress'], 25)

        ChecklistItem.objects.filter(id__in=[item.id for item in self.items[:3]]).update(done=True)
        data = self.client.get(checklist_url(self.document.id)).json()
        self.assertEqual((data['doneCount'], data['progress']), (3, 75))

        ChecklistItem.objects.filter(document=self.document).update(done=True)
        self.assertEqual(self.client.get(checklist_url(self.document.id)).json()['progress'], 100)

    def test_progress_is_zero_without_items(self):
        ChecklistItem.objects.all().delete()

        data = self.client.get(checklist_url(self.document.id)).json()

        self.assertEqual(data, {'documentId': self.document.id, 'total': 0, 'doneCount': 0, 'progress': 0, 'items': []})

    def test_result_response_is_unchanged(self):
        data = self.client.get(result_url(self.document.id)).json()

        self.assertEqual(set(data), RESULT_KEYS)

    def test_patch_done_toggles_done_at(self):
        url = checklist_item_url(self.document.id, self.items[0].id)

        done = self.client.patch(url, {'done': True}, format='json')

        self.assertEqual(done.status_code, 200)
        self.assertEqual(set(done.json()), CHECKLIST_ITEM_KEYS)
        self.assertTrue(done.json()['done'])
        self.assertIsNotNone(done.json()['doneAt'])
        item = ChecklistItem.objects.get(id=self.items[0].id)
        done_at = item.done_at

        self.client.patch(url, {'done': True}, format='json')
        item.refresh_from_db()
        self.assertEqual(item.done_at, done_at)
        self.assertEqual(self.client.get(checklist_url(self.document.id)).json()['doneCount'], 1)

        undone = self.client.patch(url, {'done': False}, format='json')

        self.assertFalse(undone.json()['done'])
        self.assertIsNone(undone.json()['doneAt'])
        item.refresh_from_db()
        self.assertIsNone(item.done_at)

    def test_patch_rejects_other_fields(self):
        url = checklist_item_url(self.document.id, self.items[0].id)
        for body in ({'done': True, 'text': '바꿈'}, {'state': 'CONFIRMED'}, {}):
            with self.subTest(body=body):
                response = self.client.patch(url, body, format='json')

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')
        item = ChecklistItem.objects.get(id=self.items[0].id)
        self.assertFalse(item.done)
        self.assertEqual(item.text, '주민세 6,250원 내기')

    def test_other_methods_not_allowed(self):
        self.assertEqual(self.client.post(checklist_url(self.document.id), {}, format='json').status_code, 405)
        item_url = checklist_item_url(self.document.id, self.items[0].id)
        self.assertEqual(self.client.put(item_url, {'done': True}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(item_url).status_code, 405)

    def test_unfinished_document_returns_same_409_as_result(self):
        for current, step in (
            (Document.Status.PENDING, Document.Step.QUEUED),
            (Document.Status.PROCESSING, Document.Step.INTERPRETING),
        ):
            with self.subTest(status=current):
                Document.objects.filter(id=self.document.id).update(status=current, step=step)
                expected = {'detail': '아직 처리 중입니다.', 'status': current, 'step': step}

                get = self.client.get(checklist_url(self.document.id))
                patch = self.client.patch(
                    checklist_item_url(self.document.id, self.items[0].id), {'done': True}, format='json',
                )

                self.assertEqual((get.status_code, get.json()), (409, expected))
                self.assertEqual((patch.status_code, patch.json()), (409, expected))
        self.assertFalse(ChecklistItem.objects.get(id=self.items[0].id).done)

    def test_failed_document_returns_409_with_fail_reason(self):
        Document.objects.filter(id=self.document.id).update(
            status=Document.Status.FAILED, step=Document.Step.OCR, fail_reason='읽지 못했습니다.',
        )

        response = self.client.get(checklist_url(self.document.id))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['failReason'], '읽지 못했습니다.')

    def test_other_user_and_missing_document_get_404(self):
        self.client.force_authenticate(create_user('other@example.com'))
        item_id = self.items[0].id

        for url_func, method, body in (
            (lambda pk: checklist_url(pk), 'get', None),
            (lambda pk: checklist_item_url(pk, item_id), 'patch', {'done': True}),
        ):
            with self.subTest(method=method):
                other = getattr(self.client, method)(url_func(self.document.id), body, format='json')
                missing = getattr(self.client, method)(url_func(self.document.id + 100), body, format='json')

                self.assertEqual(other.status_code, 404)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(other.json(), missing.json())
        self.assertFalse(ChecklistItem.objects.get(id=item_id).done)

    def test_item_of_other_document_and_missing_item_get_404(self):
        other_document = create_document(self.user)
        process_document(other_document.id)
        other_item = other_document.checklist_items.first()

        wrong_document = self.client.patch(
            checklist_item_url(self.document.id, other_item.id), {'done': True}, format='json',
        )
        missing = self.client.patch(checklist_item_url(self.document.id, 999999), {'done': True}, format='json')

        self.assertEqual(wrong_document.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        other_item.refresh_from_db()
        self.assertFalse(other_item.done)

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(checklist_url(self.document.id)).status_code, 401)
        response = self.client.patch(
            checklist_item_url(self.document.id, self.items[0].id), {'done': True}, format='json',
        )
        self.assertEqual(response.status_code, 401)
