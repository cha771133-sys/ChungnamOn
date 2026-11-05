from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken

from core.permissions import IsOwner
from documents.models import Document
from .helpers import create_user


class JWTProtectedView(APIView):
    """인증 설정을 변경하지 않고 기존 SimpleJWT와 오류 처리기의 호환성을 검증한다."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'ok': True})


class ObjectPermissionView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        self.check_object_permissions(request, SimpleNamespace(owner=None))
        return Response({'ok': True})


class APIContractTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user('owner')
        cls.other = create_user('other')
        cls.document = Document.objects.create(
            owner=cls.owner, file='notice.pdf', file_name='notice.pdf', file_type='PDF', step='QUEUED',
        )

    @property
    def status_url(self):
        return f'/api/documents/{self.document.pk}/status/'

    def test_creation_cannot_assign_a_different_owner_and_success_is_unchanged(self):
        self.client.force_authenticate(self.owner)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post('/api/documents/', {
                'file': SimpleUploadedFile('notice.pdf', b'pdf'),
                'owner': self.other.pk, 'owner_id': self.other.pk,
            }, format='multipart')
        self.assertEqual(response.status_code, 201)
        document = Document.objects.get(pk=response.json()['documentId'])
        self.assertEqual(document.owner, self.owner)
        self.assertEqual(response.json(), {'documentId': document.pk, 'status': 'PENDING'})

    def test_single_object_response_ignores_pagination_parameters(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self.status_url + '?page=999&page_size=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'documentId': self.document.pk, 'status': 'PENDING', 'step': 'QUEUED',
        })

    def test_other_owners_and_missing_documents_have_identical_404_errors(self):
        self.client.force_authenticate(self.other)
        other = self.client.get(self.status_url)
        missing = self.client.get('/api/documents/999999/status/')
        self.assertEqual(other.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other.json(), missing.json())
        self.assertEqual(other.json()['code'], 'NOT_FOUND')

    def test_missing_authentication_uses_jwt_401_policy(self):
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="api"')
        self.assertEqual(response.json(), {
            'code': 'NOT_AUTHENTICATED', 'message': '인증이 필요합니다.', 'details': {},
        })

    def test_validation_method_and_media_type_errors(self):
        self.client.force_authenticate(self.owner)
        for response, status, code in (
            (self.client.post('/api/documents/', {}, format='multipart'), 400, 'VALIDATION_ERROR'),
            (self.client.delete(self.status_url), 405, 'METHOD_NOT_ALLOWED'),
            (self.client.post('/api/documents/', {}, format='json'), 415, 'UNSUPPORTED_MEDIA_TYPE'),
        ):
            with self.subTest(code=code):
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['code'], code)
                if status == 400:
                    self.assertIn('file', response.json()['details'])
                else:
                    self.assertEqual(response.json()['details'], {})

    def test_actual_object_permission_failure_is_403(self):
        request = APIRequestFactory().get('/')
        force_authenticate(request, self.owner)
        response = ObjectPermissionView.as_view()(request)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'PERMISSION_DENIED')

    def test_jwt_missing_invalid_and_valid_token_compatibility(self):
        factory = APIRequestFactory()
        missing = JWTProtectedView.as_view()(factory.get('/'))
        invalid = JWTProtectedView.as_view()(factory.get('/', HTTP_AUTHORIZATION='Bearer sensitive-invalid-token'))
        token = str(AccessToken.for_user(self.owner))
        valid = JWTProtectedView.as_view()(factory.get('/', HTTP_AUTHORIZATION='Bearer ' + token))
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.data['code'], 'NOT_AUTHENTICATED')
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.data['code'], 'AUTHENTICATION_FAILED')
        self.assertNotIn('sensitive-invalid-token', str(invalid.data))
        self.assertEqual(invalid['WWW-Authenticate'], 'Bearer realm="api"')
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.data, {'ok': True})

    def test_non_api_html_routes_are_unchanged(self):
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        missing = self.client.get('/missing-page/')
        self.assertEqual(missing.status_code, 404)
        self.assertTrue(missing['Content-Type'].startswith('text/html'))
