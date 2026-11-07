import json
import logging
import os
import re
from urllib.parse import urlsplit
from xml.parsers import expat
from xml.etree import ElementTree

from requests import Request

from ..collector import Collector, MAX_BYTES, PinnedHTTPSAdapter, resolve_public_host
from ..errors import CollectionError


API_ENDPOINTS = {
    'api.odcloud.kr': frozenset({'/api/gov24/v3/serviceList', '/api/gov24/v3/serviceDetail', '/api/gov24/v3/supportConditions'}),
    'www.law.go.kr': frozenset({'/DRF/lawSearch.do', '/DRF/lawService.do'}),
}


def validate_api_url(url):
    """고정된 HTTPS API 주소만 허용하고 인증정보는 호출 인자로만 받는다."""
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.port not in (None, 443) or parts.username or parts.password or parts.fragment or parts.path not in API_ENDPOINTS.get(parts.hostname, ()):
            raise ValueError
        return url
    except ValueError:
        raise CollectionError('URL_REJECTED', '허용된 공식 API 주소가 아닙니다.') from None


class CredentialFilter(logging.Filter):
    def filter(self, record):
        """HTTP 디버그 메시지에서 인증 쿼리와 설정된 인증값을 출력 전에 가린다."""
        message = record.getMessage()
        message = re.sub(r'(?i)([?&](?:OC|serviceKey)=)[^&\s"\']*', r'\1[숨김]', message)
        for name in ('PUBLIC_DATA_SERVICE_KEY', 'LAW_API_OC'):
            secret = os.environ.get(name, '')
            if secret:
                message = message.replace(secret, '[숨김]')
        record.msg, record.args = message, ()
        return True


class APIHTTPSAdapter(PinnedHTTPSAdapter):
    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        """허용 API의 공인 IP에 연결하되 원래 호스트의 SNI와 TLS 검증을 유지한다."""
        validate_api_url(request.url)
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        hostname = host_params['host']
        host_params['host'] = resolve_public_host(hostname)[0]
        pool_kwargs.update(assert_hostname=hostname, server_hostname=hostname)
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


def parse_payload(body):
    """DTD·엔티티 선언을 파서 단계에서 거부한 뒤 XML 또는 JSON을 해석한다."""
    try:
        if body.lstrip().startswith(b'<'):
            parser = expat.ParserCreate()

            def reject(*args):
                raise ValueError

            parser.StartDoctypeDeclHandler = reject
            parser.EntityDeclHandler = reject
            parser.ExternalEntityRefHandler = reject
            parser.Parse(body, True)
            root = ElementTree.fromstring(body)

            def convert(node):
                """반복 XML 태그는 목록으로 보존하며 조문 순서와 원문 값을 유지한다."""
                if not len(node):
                    return node.text or ''
                values = {}
                for child in node:
                    value = convert(child)
                    if child.tag in values:
                        if not isinstance(values[child.tag], list):
                            values[child.tag] = [values[child.tag]]
                        values[child.tag].append(value)
                    else:
                        values[child.tag] = value
                return values

            return {root.tag: convert(root)}
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError, expat.ExpatError, ElementTree.ParseError, RecursionError):
        raise CollectionError('INVALID_RESPONSE', 'JSON/XML 형식이 올바르지 않거나 안전하지 않습니다.') from None


class APITransport(Collector):
    """P1-7의 DNS 고정·호출 간격·재시도·크기·시간 제한을 재사용한다."""
    def __init__(self):
        """기존 수집 제한을 상속하고 API 전용 연결 어댑터와 키 마스킹을 설치한다."""
        super().__init__()
        self.session.mount('https://', APIHTTPSAdapter(max_retries=0))
        logger = logging.getLogger('urllib3.connectionpool')
        if not any(isinstance(item, CredentialFilter) for item in logger.filters):
            logger.addFilter(CredentialFilter())

    def get(self, url, params, *, authorization=None):
        """제공자별 인증 경계를 검증하고 제한된 응답을 해석한 뒤 연결을 정리한다."""
        validate_api_url(url)
        if authorization and urlsplit(url).hostname != 'api.odcloud.kr':
            raise CollectionError('URL_REJECTED', '공공데이터 인증 헤더를 다른 제공자에 전달할 수 없습니다.')
        if 'OC' in params and urlsplit(url).hostname != 'www.law.go.kr':
            raise CollectionError('URL_REJECTED', '법제처 인증값을 다른 제공자에 전달할 수 없습니다.')
        prepared_url = Request('GET', url, params=params).prepare().url
        if authorization:
            self.session.headers['Authorization'] = f'Infuser {authorization}'
        else:
            self.session.headers.pop('Authorization', None)
        response = self._request(prepared_url)
        try:
            if response.status_code in (401, 403):
                raise CollectionError('AUTH_FAILED', '공식 API 인증 또는 접근 권한을 확인하세요.')
            if response.status_code in (301, 302, 303, 307, 308):
                # 키를 다른 주소로 전달하지 않는다. API 리다이렉트는 전부 거부한다.
                raise CollectionError('REDIRECT_REJECTED', 'API 리다이렉트는 허용하지 않습니다.')
            if response.status_code != 200:
                raise CollectionError('HTTP_STATUS', f'공식 API HTTP {response.status_code} 오류입니다.')
            try:
                length = int(response.headers.get('Content-Length', '0'))
                if not 0 <= length <= MAX_BYTES:
                    raise ValueError
            except ValueError:
                raise CollectionError('RESPONSE_TOO_LARGE', '공식 API 응답 크기 제한을 초과했습니다.') from None
            return parse_payload(self._read_body(response, MAX_BYTES))
        finally:
            response.close()
