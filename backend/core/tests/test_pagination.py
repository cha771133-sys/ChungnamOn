from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.generics import ListAPIView
from rest_framework.test import APIRequestFactory, APITestCase

from core.pagination import StandardPagination
from core.permissions import IsOwner
from documents.models import Document
from documents.serializers import DocumentStatusSerializer
from .helpers import create_user


class OwnedDocumentList(ListAPIView):
    """목록 계약 검증 전용 뷰이며 운영 URL에는 등록하지 않는다."""

    serializer_class = DocumentStatusSerializer
    permission_classes = [IsOwner]

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user).order_by('pk')


urlpatterns = [path('owned/', OwnedDocumentList.as_view())]


class PaginationTests(TestCase):
    def paginate(self, query=''):
        from rest_framework.request import Request
        paginator = StandardPagination()
        request = Request(APIRequestFactory().get('/items/' + query))
        page = paginator.paginate_queryset(list(range(150)), request)
        return paginator.get_paginated_response(page).data

    def test_default_and_custom_size_and_maximum(self):
        for query, length in (('', 20), ('?page_size=7', 7), ('?page_size=1000', 100)):
            with self.subTest(query=query):
                data = self.paginate(query)
                self.assertEqual(set(data), {'count', 'next', 'previous', 'results'})
                self.assertEqual(data['count'], 150)
                self.assertEqual(data['results'], list(range(length)))
                self.assertIsNone(data['previous'])

    def test_second_page_and_standard_links(self):
        data = self.paginate('?page=2&page_size=7')
        self.assertEqual(data['results'], list(range(7, 14)))
        self.assertEqual(parse_qs(urlparse(data['next']).query), {'page': ['3'], 'page_size': ['7']})
        self.assertEqual(parse_qs(urlparse(data['previous']).query), {'page_size': ['7']})

    def test_invalid_page_returns_not_found(self):
        from rest_framework.exceptions import NotFound
        for page in ('0', '-1', '999', 'invalid'):
            with self.subTest(page=page), self.assertRaises(NotFound):
                self.paginate(f'?page={page}')

    def test_invalid_page_size_uses_default(self):
        for value in ('0', '-1', 'invalid'):
            with self.subTest(value=value):
                self.assertEqual(len(self.paginate(f'?page_size={value}')['results']), 20)


@override_settings(ROOT_URLCONF=__name__)
class OwnedListIntegrationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user('owner')
        cls.other = create_user('other')
        for user in (cls.owner, cls.other):
            Document.objects.bulk_create([
                Document(owner=user, file='notice.pdf', file_name='notice.pdf', file_type='PDF',
                         expires_at='2030-01-01T00:00:00Z') for _ in range(35)
            ])

    def setUp(self):
        self.client.force_authenticate(self.owner)

    def test_global_pagination_and_stable_owner_only_order(self):
        expected = list(Document.objects.filter(owner=self.owner).order_by('pk').values_list('pk', flat=True))
        first = self.client.get('/owned/').json()
        second = self.client.get('/owned/?page=2').json()
        self.assertEqual(first['count'], 35)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(len(second['results']), 15)
        self.assertEqual([item['documentId'] for item in first['results'] + second['results']], expected)
        self.assertEqual(first, self.client.get('/owned/').json())

    def test_out_of_range_page_uses_common_error(self):
        response = self.client.get('/owned/?page=999')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'NOT_FOUND')

    def test_anonymous_list_is_denied(self):
        self.client.force_authenticate(None)
        response = self.client.get('/owned/')
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.json()['code'], 'NOT_AUTHENTICATED')
