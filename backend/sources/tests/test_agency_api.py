from django.contrib.auth import get_user_model
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from sources.models import Agency, AgencyAlias
from .agency_helpers import create_agency


class AgencyAPITests(APITestCase):
    url = '/api/sources/agencies/lookup/'

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email='agency-test@example.com', password='test-password')
        cls.agency = create_agency()

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')

    def test_real_jwt_and_success_contract_with_provenance(self):
        response = self.client.get(self.url, {'name': ' 가상시청 ', 'region': '아산시'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {'lookupStatus', 'applicabilityStatus', 'agency', 'candidates', 'message'})
        self.assertEqual(body['lookupStatus'], 'FOUND')
        self.assertEqual(body['applicabilityStatus'], 'NEEDS_CONFIRMATION')
        self.assertEqual(set(body['agency']), {'id', 'name', 'department', 'region', 'phone', 'phoneType', 'sourceUrl', 'checkedAt'})
        self.assertEqual(body['agency']['sourceUrl'], self.agency.source_url)
        self.assertTrue(body['agency']['checkedAt'])
        self.assertFalse(body['candidates'])

    def test_missing_and_invalid_jwt(self):
        for token in (None, 'Bearer invalid'):
            self.client.credentials(**({'HTTP_AUTHORIZATION': token} if token else {}))
            response = self.client.get(self.url, {'name': '가상시청'})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(set(response.json()), {'code', 'message', 'details'})

    def test_invalid_query_uses_common_error_contract(self):
        for query in ({}, {'name': ''}, {'name': '  '}, {'name': '가' * 256},
                      {'name': '<script>'}, {'name': '가상\x00시청'}, {'name': '가상\u200b시청'},
                      {'name': '가상시청', 'region': ''}, {'name': '가상시청', 'region': '가' * 101}):
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.json()), {'code', 'message', 'details'})
                self.assertEqual(response.json()['code'], 'VALIDATION_ERROR')
                self.assertTrue(response.json()['details'])
        self.assertEqual(self.client.get(self.url + '?name=first&name=second').status_code, 400)

    def test_unverified_and_ambiguous_contracts(self):
        response = self.client.get(self.url, {'name': '없는가상기관'}).json()
        self.assertEqual(response['lookupStatus'], 'UNVERIFIED')
        self.assertIsNone(response['agency'])
        self.assertFalse(response['candidates'])
        create_agency(region='천안시')
        response = self.client.get(self.url, {'name': '가상시청'}).json()
        self.assertEqual(response['lookupStatus'], 'AMBIGUOUS')
        self.assertIsNone(response['agency'])
        self.assertEqual(len(response['candidates']), 2)
        self.assertEqual(response['applicabilityStatus'], 'NEEDS_CONFIRMATION')

    def test_lookup_does_not_write(self):
        before = list(Agency.objects.values())
        aliases = list(AgencyAlias.objects.values())
        for query in ({'name': '가상시청'}, {'name': '미확인'}, {'name': ''}):
            self.client.get(self.url, query)
        self.assertEqual(list(Agency.objects.values()), before)
        self.assertEqual(list(AgencyAlias.objects.values()), aliases)

    def test_no_public_mutations_or_crud_routes(self):
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(self.url, {}, format='json')
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.json()['code'], 'METHOD_NOT_ALLOWED')
        for url in ('/api/sources/agencies/', f'/api/sources/agencies/{self.agency.pk}/'):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_missing_contact_returns_null(self):
        create_agency(name='연락처없는가상기관', phone=None, phone_type='UNKNOWN', source_url=None, checked_at=None)
        body = self.client.get(self.url, {'name': '연락처없는가상기관'}).json()['agency']
        self.assertIsNone(body['phone'])
        self.assertIsNone(body['sourceUrl'])
        self.assertIsNone(body['checkedAt'])

    def test_schema_includes_parameters_jwt_errors_and_status_examples(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema['paths'][self.url]['get']
        self.assertEqual(operation['security'], [{'jwtAuth': []}])
        parameters = {parameter['name']: parameter for parameter in operation['parameters']}
        self.assertTrue(parameters['name']['required'])
        self.assertFalse(parameters['region'].get('required', False))
        self.assertEqual(parameters['name']['schema']['maxLength'], 255)
        self.assertTrue({'200', '400', '401', '405'} <= set(operation['responses']))
        examples = operation['responses']['200']['content']['application/json']['examples']
        self.assertEqual({example['value']['lookupStatus'] for example in examples.values()}, {'FOUND', 'AMBIGUOUS', 'UNVERIFIED'})
