import json
import logging
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from requests import Request

from django.test import SimpleTestCase

from sources.services.errors import CollectionError
from sources.services.normalization import OfficialRecord, official_url
from sources.services.providers.law_api import LawAPIClient
from sources.services.providers.public_data import PublicDataClient
from sources.services.providers.transport import APIHTTPSAdapter, APITransport, CredentialFilter, parse_payload, validate_api_url


FIXTURES = Path(__file__).parent / 'fixtures'
PUBLIC_ROW = json.loads((FIXTURES / 'public_service.json').read_text(encoding='utf-8'))
LAW_BODY = (FIXTURES / 'ordinance.xml').read_bytes()


def envelope(rows, page=1, total=None):
    return {'page': page, 'perPage': 100, 'totalCount': len(rows) if total is None else total, 'matchCount': len(rows) if total is None else total, 'currentCount': len(rows), 'data': rows}


class OfficialProviderTests(SimpleTestCase):
    def setUp(self):
        credentials = patch.dict(os.environ, {'PUBLIC_DATA_SERVICE_KEY': 'test-public-secret', 'LAW_API_OC': 'test-law-secret'})
        credentials.start()
        self.addCleanup(credentials.stop)
        self.transport = Mock()
        self.public = PublicDataClient(self.transport)
        self.law = LawAPIClient(self.transport)

    def test_credentials_required_without_network(self):
        for client, key in ((PublicDataClient, 'PUBLIC_DATA_SERVICE_KEY'), (LawAPIClient, 'LAW_API_OC')):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                with self.assertRaisesMessage(CollectionError, 'AUTH_MISSING'):
                    client(self.transport)
        self.transport.get.assert_not_called()

    def test_public_list_filters_and_authentication(self):
        self.transport.get.return_value = envelope([PUBLIC_ROW, {**PUBLIC_ROW, '서비스ID': 'other', '소관기관명': '서울특별시'}])
        rows = list(self.public.list_services(query='주민', region='아산시', agency='아산'))
        self.assertEqual(rows, [PUBLIC_ROW])
        args, kwargs = self.transport.get.call_args
        self.assertTrue(args[0].endswith('/gov24/v3/serviceList'))
        self.assertEqual(args[1]['cond[서비스명::LIKE]'], '주민')
        self.assertNotIn('region', args[1])
        self.assertEqual(kwargs['authorization'], 'test-public-secret')

    def test_public_national_filter_requires_official_central_institution(self):
        national = {**PUBLIC_ROW, '소관기관명': '행정안전부', '소관기관유형': '중앙행정기관'}
        self.transport.get.return_value = envelope([PUBLIC_ROW, national])
        self.assertEqual(list(self.public.list_services(query='주민', region='전국')), [national])

    def test_public_more_than_100_paginated_and_bounded(self):
        rows = [{**PUBLIC_ROW, '서비스ID': str(index)} for index in range(120)]
        self.transport.get.side_effect = [envelope(rows[:100], total=120), envelope(rows[100:], page=2, total=120)]
        self.assertEqual(len(list(self.public.list_services(query='주민', region='아산시', limit=120))), 120)
        self.assertEqual(self.transport.get.call_args_list[1].args[1]['page'], 2)

    def test_public_empty_results(self):
        self.transport.get.return_value = envelope([])
        self.assertEqual(list(self.public.list_services(query='주민', region='아산시')), [])

    def test_public_details_and_conditions_preserved(self):
        self.transport.get.side_effect = [envelope([PUBLIC_ROW]), envelope([PUBLIC_ROW]), envelope([{'서비스ID': PUBLIC_ROW['서비스ID'], 'JA0101': 'Y', 'JA0329': None}])]
        record = self.public.fetch(SimpleNamespace(external_id=PUBLIC_ROW['서비스ID'], configuration={'collection_region': '아산시'}))
        self.assertIn(PUBLIC_ROW['선정기준'], record.content_text)
        self.assertIn('JA0329', record.content_text)
        self.assertIn('null', record.content_text)
        self.assertIsNone(record.published_at)
        self.assertEqual(record.region, '확인 필요')
        self.assertEqual([call.args[0].rsplit('/', 1)[1] for call in self.transport.get.call_args_list], ['serviceList', 'serviceDetail', 'supportConditions'])

    def test_public_missing_link_not_invented(self):
        row = {key: value for key, value in PUBLIC_ROW.items() if key != '상세조회URL'}
        self.transport.get.side_effect = [envelope([row]), envelope([row]), envelope([])]
        record = self.public.fetch(SimpleNamespace(external_id=PUBLIC_ROW['서비스ID'], configuration={}))
        self.assertIsNone(record.source_url)
        self.assertFalse(record.metadata['source_link_available'])

    def test_public_unusable_link_is_omitted_without_losing_policy_body(self):
        listing = {**PUBLIC_ROW, '상세조회URL': 'https://www.gov.kr/detail?serviceKey=test-public-secret'}
        self.transport.get.side_effect = [envelope([listing]), envelope([PUBLIC_ROW]), envelope([])]
        record = self.public.fetch(SimpleNamespace(external_id=PUBLIC_ROW['서비스ID'], configuration={}))
        self.assertIsNone(record.source_url)
        self.assertIn(PUBLIC_ROW['선정기준'], record.content_text)
        self.assertNotIn('test-public-secret', record.content_text)

    def test_public_bad_envelope_and_internal_errors(self):
        for payload in ({'code': -4, 'msg': 'test-public-secret'}, {'response': {'header': {'resultCode': '99'}}}, {'data': []}, envelope(['bad']), {**envelope([]), 'page': 2}):
            with self.subTest(payload=payload):
                self.transport.get.return_value = payload
                with self.assertRaises(CollectionError) as caught:
                    list(self.public.list_services(query='주민', region='아산시'))
                self.assertNotIn('test-public-secret', str(caught.exception))

    def test_public_mismatched_detail_id_and_conditions_rejected(self):
        self.transport.get.return_value = envelope([{**PUBLIC_ROW, '서비스ID': 'other'}])
        for method in (self.public.detail, self.public.support_conditions):
            with self.subTest(method=method), self.assertRaises(CollectionError):
                method('wanted')

    def test_public_page_stall_and_max_pages(self):
        repeated = envelope([PUBLIC_ROW], total=1000)
        self.transport.get.side_effect = [repeated, {**repeated, 'page': 2}]
        with self.assertRaisesMessage(CollectionError, 'PAGINATION_STALLED'):
            list(self.public._pages('serviceList', {}, per_page=1))
        self.transport.get.side_effect = [repeated]
        with self.assertRaisesMessage(CollectionError, 'PAGE_LIMIT'):
            list(self.public._pages('serviceList', {}, per_page=1, max_pages=1))

    def test_law_region_parameters_and_current_history(self):
        for region, sborg in (('충청남도', None), ('천안시', '4490000'), ('아산시', '4520000')):
            for history in (False, True):
                with self.subTest(region=region, history=history):
                    institution = '충청남도' if region == '충청남도' else f'충청남도 {region}'
                    self.transport.get.return_value = {'OrdinSearch': {'totalCnt': '1', 'page': '1', 'law': {'자치법규ID': '9999001', '지자체기관명': institution}}}
                    rows = list(self.law.list_ordinances(query='주민', region=region, history=history))
                    self.assertEqual(len(rows), 1)
                    params = self.transport.get.call_args.args[1]
                    self.assertEqual(params['OC'], 'test-law-secret')
                    self.assertEqual(params['org'], '6440000')
                    self.assertEqual(params.get('sborg'), sborg)
                    self.assertEqual(params['nw'], 2 if history else 1)

    def test_law_province_scope_excludes_other_city_jurisdictions(self):
        self.transport.get.return_value = {'OrdinSearch': {'totalCnt': 2, 'law': [
            {'자치법규ID': '1', '지자체기관명': '충청남도'},
            {'자치법규ID': '2', '지자체기관명': '충청남도 논산시'},
        ]}}
        rows = list(self.law.list_ordinances(query='지방세', region='충청남도'))
        self.assertEqual([row['자치법규ID'] for row in rows], ['1'])

    def test_law_history_allows_distinct_versions_of_same_stable_id(self):
        self.transport.get.return_value = {'OrdinSearch': {'totalCnt': 2, 'law': [
            {'자치법규ID': '1', '자치법규일련번호': '10', '지자체기관명': '아산시'},
            {'자치법규ID': '1', '자치법규일련번호': '20', '지자체기관명': '아산시'},
        ]}}
        self.assertEqual(len(list(self.law.list_ordinances(query='주민', region='아산시', history=True))), 2)

    def test_law_body_preserves_articles_dates_and_unknown_effect(self):
        self.transport.get.side_effect = [parse_payload(LAW_BODY), {'OrdinSearch': {'totalCnt': '0'}}]
        target = SimpleNamespace(external_id='9999001', source_url=None, configuration={'collection_region': '아산시'})
        record = self.law.fetch(target)
        self.assertEqual(record.published_at, date(2026, 10, 1))
        self.assertEqual(record.effective_at, date(2027, 1, 1))
        for value in ('조문번호', '조제목', '제1조', '제2조', '목적', '정의'):
            self.assertIn(value, record.content_text)
        self.assertEqual(record.metadata['legal_effect'], '확인 필요')
        self.assertIsNone(record.source_url)
        self.assertEqual(self.transport.get.call_args_list[0].args[1]['ID'], '9999001')

    def test_law_current_version_link_is_refreshed_and_version_race_rejected(self):
        target = SimpleNamespace(external_id='9999001', source_url='https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=old', configuration={'collection_region': '아산시'})
        row = {'자치법규ID': '9999001', '지자체기관명': '아산시', '자치법규일련번호': '9999002', '자치법규상세링크': '/LSW/ordinInfoP.do?ordinSeq=9999002'}
        self.transport.get.side_effect = [parse_payload(LAW_BODY), {'OrdinSearch': {'totalCnt': 1, 'law': row}}]
        record = self.law.fetch(target)
        self.assertTrue(record.source_url.endswith('ordinSeq=9999002'))
        self.assertEqual(record.metadata['listing_scope'], '현행 목록')
        self.transport.get.side_effect = [parse_payload(LAW_BODY), {'OrdinSearch': {'totalCnt': 1, 'law': {**row, '자치법규일련번호': 'newer'}}}]
        with self.assertRaisesMessage(CollectionError, 'VERSION_CONFLICT'):
            self.law.fetch(target)

    def test_law_api_link_is_not_stored_as_public_source_but_body_is_collected(self):
        row = {'자치법규ID': '9999001', '지자체기관명': '아산시', '자치법규일련번호': '9999002', '자치법규상세링크': '/DRF/lawService.do?OC=test-law-secret&target=ordin&ID=9999001'}
        self.transport.get.side_effect = [parse_payload(LAW_BODY), {'OrdinSearch': {'totalCnt': 1, 'law': row}}]
        record = self.law.fetch(SimpleNamespace(external_id='9999001', source_url=None, configuration={'collection_region': '아산시'}))
        self.assertIsNone(record.source_url)
        self.assertFalse(record.metadata['source_link_available'])
        self.assertIn('제1조', record.content_text)
        self.assertNotIn('test-law-secret', record.content_text)

    def test_law_region_mismatch_rejected(self):
        self.transport.get.return_value = parse_payload(LAW_BODY)
        with self.assertRaisesMessage(CollectionError, 'REGION_MISMATCH'):
            self.law.fetch(SimpleNamespace(external_id='9999001', source_url=None, configuration={'collection_region': '천안시'}))

    def test_law_empty_list_and_invalid_body(self):
        self.transport.get.return_value = {'OrdinSearch': {'totalCnt': '0'}}
        self.assertEqual(list(self.law.list_ordinances(query='주민', region='아산시')), [])
        for payload in ({'result': 'API 오류: test-law-secret'}, {'자치법규': {'자치법규ID': 'different'}}, {}):
            with self.subTest(payload=payload):
                self.transport.get.return_value = payload
                with self.assertRaises(CollectionError) as caught:
                    self.law.detail('9999001')
                self.assertNotIn('test-law-secret', str(caught.exception))

    def test_law_http_200_authentication_error_envelope_is_not_success(self):
        # 공식 가이드의 공개 샘플 호출에서 관찰한 오류 구조를 키 없는 fixture로 재현한다.
        self.transport.get.return_value = {'Response': {'result': '사용자 정보 검증에 실패하였습니다.', 'msg': '호출 서버 등록 확인'}}
        with self.assertRaisesMessage(CollectionError, 'AUTH_FAILED'):
            self.law.detail('9999001')

    def test_law_pagination_and_malformed_page(self):
        row = {'자치법규ID': '1', '지자체기관명': '아산시'}
        self.transport.get.side_effect = [
            {'OrdinSearch': {'totalCnt': '2', 'page': '1', 'law': row}},
            {'OrdinSearch': {'totalCnt': '2', 'page': '2', 'law': {**row, '자치법규ID': '2'}}},
        ]
        self.assertEqual(len(list(self.law.list_ordinances(query='주민', region='아산시', per_page=1))), 2)
        self.transport.get.side_effect = None
        self.transport.get.return_value = {'OrdinSearch': {'totalCnt': '2', 'page': '1', 'law': []}}
        with self.assertRaisesMessage(CollectionError, 'PAGINATION_STALLED'):
            list(self.law.list_ordinances(query='주민', region='아산시'))

    def test_transport_http_auth_redirect_size_and_resource_cleanup(self):
        transport = APITransport()
        self.addCleanup(transport.close)
        for status, headers, expected in ((401, {}, 'AUTH_FAILED'), (403, {}, 'AUTH_FAILED'), (302, {'Location': 'http://127.0.0.1'}, 'REDIRECT_REJECTED'), (500, {}, 'HTTP_STATUS'), (429, {}, 'HTTP_STATUS'), (200, {'Content-Length': '999999999'}, 'RESPONSE_TOO_LARGE')):
            with self.subTest(status=status):
                response = Mock(status_code=status, headers=headers)
                with patch.object(transport, '_request', return_value=response), self.assertRaisesMessage(CollectionError, expected):
                    transport.get('https://www.law.go.kr/DRF/lawService.do', {'OC': 'test-law-secret'})
                response.close.assert_called_once()

    def test_transport_json_and_xml_safe_parsing(self):
        self.assertIn('자치법규', parse_payload(LAW_BODY))
        self.assertEqual(parse_payload(b'{"data": []}'), {'data': []})
        for body in (b'broken', b'[]', b'<broken', b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///secret">]><foo>&x;</foo>', b'<!DOCTYPE foo><foo/>'):
            with self.subTest(body=body), self.assertRaisesMessage(CollectionError, 'INVALID_RESPONSE'):
                parse_payload(body)

    def test_credentials_are_redacted_in_http_debug_log(self):
        record = logging.LogRecord('urllib3.connectionpool', logging.DEBUG, '', 1, 'GET /?OC=%s&serviceKey=%s', ('test-law-secret', 'test-public-secret'), None)
        CredentialFilter().filter(record)
        self.assertNotIn('test-law-secret', record.getMessage())
        self.assertNotIn('test-public-secret', record.getMessage())

    def test_request_allowlist_blocks_internal_and_unknown_addresses(self):
        for url in ('http://www.law.go.kr/DRF/lawSearch.do', 'https://127.0.0.1/DRF/lawSearch.do', 'https://localhost/DRF/lawSearch.do', 'https://www.law.go.kr.evil.test/DRF/lawSearch.do', 'https://www.law.go.kr:444/DRF/lawSearch.do', 'https://user:pass@www.law.go.kr/DRF/lawSearch.do'):
            with self.subTest(url=url), self.assertRaises(CollectionError):
                validate_api_url(url)

    def test_provider_host_path_pairs_and_credentials_cannot_cross(self):
        for url in ('https://api.odcloud.kr/DRF/lawSearch.do', 'https://www.law.go.kr/api/gov24/v3/serviceDetail'):
            with self.subTest(url=url), self.assertRaises(CollectionError):
                validate_api_url(url)
        transport = APITransport()
        self.addCleanup(transport.close)
        with patch.object(transport, '_request') as request:
            with self.assertRaises(CollectionError):
                transport.get('https://www.law.go.kr/DRF/lawSearch.do', {}, authorization='public-key')
            with self.assertRaises(CollectionError):
                transport.get('https://api.odcloud.kr/api/gov24/v3/serviceList', {'OC': 'law-secret'})
            request.assert_not_called()

    def test_api_connection_pins_public_ip_and_keeps_original_tls_hostname(self):
        adapter = APIHTTPSAdapter()
        self.addCleanup(adapter.close)
        request = Request('GET', 'https://www.law.go.kr/DRF/lawService.do').prepare()
        with patch('sources.services.providers.transport.resolve_public_host', return_value=['93.184.216.34']), patch.object(adapter.poolmanager, 'connection_from_host') as connect:
            adapter.get_connection_with_tls_context(request, verify=True)
        self.assertEqual(connect.call_args.kwargs['host'], '93.184.216.34')
        self.assertEqual(connect.call_args.kwargs['pool_kwargs']['server_hostname'], 'www.law.go.kr')
        self.assertEqual(connect.call_args.kwargs['pool_kwargs']['assert_hostname'], 'www.law.go.kr')
        with patch('sources.services.collector.socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 443))]), self.assertRaisesMessage(CollectionError, 'DNS_REJECTED'):
            adapter.get_connection_with_tls_context(request, verify=True)

    def test_source_links_exclude_keys_api_and_fake_hosts(self):
        for url in ('https://www.gov.kr/detail?serviceKey=secret', 'https://www.gov.kr/detail?service%4Bey=secret', 'https://www.gov.kr/detail?OC=secret', 'https://www.gov.kr.evil.test/detail', 'https://api.odcloud.kr/api/gov24/v3/serviceDetail'):
            with self.subTest(url=url), self.assertRaises(CollectionError):
                official_url(url, 'public_data')
        self.assertEqual(official_url('/LSW/ordinInfoP.do?ordinSeq=999', 'law'), 'https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=999')
        self.assertEqual(official_url('http://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=999', 'law'), 'https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=999')
        with self.assertRaises(CollectionError):
            official_url('https://www.gov.kr/detail?serviceKey=secret', 'html')

    def test_retry_429_and_5xx_is_bounded_and_honors_delay(self):
        transport = APITransport()
        self.addCleanup(transport.close)
        first = Mock(status_code=429, headers={'Retry-After': '2'})
        final = Mock(status_code=200, headers={})
        with patch.object(transport.session, 'get', side_effect=[first, final]), patch.object(transport, '_pace'), patch('sources.services.collector.time.sleep') as sleep:
            self.assertIs(transport._request('https://www.law.go.kr/DRF/lawSearch.do'), final)
            sleep.assert_called_once_with(2)
            first.close.assert_called_once()
        responses = [Mock(status_code=503, headers={}) for _ in range(3)]
        with patch.object(transport.session, 'get', side_effect=responses) as get, patch.object(transport, '_pace'), patch('sources.services.collector.time.sleep'):
            self.assertIs(transport._request('https://www.law.go.kr/DRF/lawSearch.do'), responses[-1])
            self.assertEqual(get.call_count, 3)

    def test_normalization_hash_is_stable_and_body_validated(self):
        base = dict(provider='public_data', external_id='same', source_url=None, title='제목', agency='기관', region='확인 필요', category='지원', content_text='원문 조건')
        self.assertEqual(OfficialRecord(**base).content_hash, OfficialRecord(**base).content_hash)
        for change in ({'external_id': ''}, {'title': ''}, {'content_text': ''}, {'agency': 'a' * 256}):
            with self.subTest(change=change), self.assertRaises(CollectionError):
                OfficialRecord(**{**base, **change}).validate()
