import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Document
from .serializers import MAX_FILE_SIZE

TEMP_MEDIA_ROOT = tempfile.mkdtemp()
UPLOAD_URL = '/api/documents/'


def create_user(email):
    User = get_user_model()
    return User.objects.create_user(**{User.USERNAME_FIELD: email}, password='test-password')


def status_url(document_id):
    return f'/api/documents/{document_id}/status/'


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
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
                self.assertIn('file', response.json())
        self.assertFalse(Document.objects.exists())

    def test_rejects_file_over_20mb(self):
        response = self.upload('large.png', b'0' * (MAX_FILE_SIZE + 1), 'image/png')

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.json())
        self.assertFalse(Document.objects.exists())

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.upload('notice.jpg', content_type='image/jpeg')

        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(Document.objects.exists())


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class DocumentStatusTests(APITestCase):
    def setUp(self):
        self.user = create_user('owner@example.com')
        self.document = Document.objects.create(
            owner=self.user,
            file='documents/notice.jpg',
            file_name='notice.jpg',
            file_type=Document.FileType.IMAGE,
            step=Document.Step.QUEUED,
        )

    def test_owner_can_view_status(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(status_url(self.document.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'documentId': self.document.id, 'status': 'PENDING', 'step': 'QUEUED'},
        )

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

        self.assertIn(response.status_code, (401, 403))
