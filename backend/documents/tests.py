import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.fields.files import FieldFile
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import UserProfile
from chatbot.models import ChatMessage

from .models import Document, InterpretationResult
from .serializers import MAX_FILE_SIZE
from .services import pipeline
from .services.pipeline import process_document
from .services.runner import start_processing
from .views import DocumentDetailView

TEMP_MEDIA_ROOT = tempfile.mkdtemp()
UPLOAD_URL = '/api/documents/'
RESULT_KEYS = {
    'documentId', 'docType', 'title', 'originalText', 'translatedText',
    'summary', 'terms', 'deadlines', 'requiredDocs', 'agency', 'verification',
    'maskedCount', 'lowConfidence',
}

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
        self.assertEqual(result.verification['status'], 'MATCHED')
        self.assertEqual(result.language, pipeline.DEFAULT_LANGUAGE)

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
        self.assertEqual(data['verification']['status'], 'MATCHED')
        self.assertEqual(data['maskedCount'], 2)
        self.assertFalse(data['lowConfidence'])

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
