import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Event, Timer
from urllib.parse import urljoin, urlsplit

import requests
from protego import Protego
from requests.adapters import HTTPAdapter

from .errors import CollectionError
from .source_registry import validate_source_url


USER_AGENT = 'ChungnamOnSources/1.0 (+https://github.com/cha771133-sys/ChungnamOn)'
MAX_BYTES = 2 * 1024 * 1024
BODY_TIMEOUT = 30
MAX_WAIT = 60
RETRIES = 2
REDIRECTS = {301, 302, 303, 307, 308}
RETRY_STATUS = {429, 500, 502, 503, 504}


def resolve_public_host(host):
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        if not addresses or any(
            not ipaddress.ip_address(address).is_global
            or ipaddress.ip_address(address).is_multicast
            or ipaddress.ip_address(address).is_reserved
            for address in addresses
        ):
            raise CollectionError('DNS_REJECTED', '공인 주소로만 연결할 수 있습니다.')
        return addresses
    except (OSError, ValueError):
        raise CollectionError('DNS_FAILED', '공식기관 DNS 주소를 확인하지 못했습니다.') from None


class PinnedHTTPSAdapter(HTTPAdapter):
    """검증한 공인 IP로 연결하되 원래 호스트의 SNI·인증서 검증을 유지한다."""

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        validate_source_url(request.url)
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        hostname = host_params['host']
        host_params['host'] = resolve_public_host(hostname)[0]
        pool_kwargs.update(assert_hostname=hostname, server_hostname=hostname)
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


@dataclass(frozen=True)
class HTMLResponse:
    body: bytes
    content_type: str
    url: str


def retry_delay(header, attempt):
    delay = 2 ** attempt
    if header:
        try:
            delay = max(delay, int(header))
        except ValueError:
            try:
                target = parsedate_to_datetime(header)
                delay = max(delay, (target - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    if delay > MAX_WAIT:
        raise CollectionError('RETRY_LATER', '서버가 긴 대기를 요청하여 이번 수집을 중단합니다.')
    return max(0, delay)


class Collector:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.session.mount('https://', PinnedHTTPSAdapter(max_retries=0))
        self.robots = {}
        self.last_request = {}
        self.intervals = {}

    def close(self):
        self.session.close()

    def _pace(self, host):
        interval = self.intervals.get(host, 1.0)
        if interval > MAX_WAIT:
            raise CollectionError('ROBOTS_DELAY', 'robots 요청 간격이 길어 이번 수집을 중단합니다.')
        delay = self.last_request.get(host, -interval) + interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_request[host] = time.monotonic()

    def _request(self, url):
        host = urlsplit(url).hostname
        for attempt in range(RETRIES + 1):
            self._pace(host)
            try:
                response = self.session.get(
                    url, headers={'Host': host}, timeout=(5, 15), stream=True, allow_redirects=False,
                )
            except requests.exceptions.SSLError:
                raise CollectionError('TLS_FAILED', '공식기관 TLS 인증서를 검증하지 못했습니다.') from None
            except (requests.Timeout, requests.ConnectionError):
                if attempt == RETRIES:
                    raise CollectionError('NETWORK_FAILED', '연결 실패 또는 제한 시간 초과로 재시도를 마쳤습니다.') from None
                time.sleep(2 ** attempt)
                continue
            except requests.RequestException:
                raise CollectionError('HTTP_FAILED', 'HTTP 요청을 완료하지 못했습니다.') from None
            if response.status_code in RETRY_STATUS and attempt < RETRIES:
                header = response.headers.get('Retry-After')
                response.close()
                time.sleep(retry_delay(header, attempt))
                continue
            return response

    def _read_body(self, response, limit):
        # requests/urllib3의 고정 버전에서 사용하는 실제 소켓을 보존한다.
        # Connection: close 응답은 connection.sock이 이미 비어 있어 HTTP 파일에서 찾는다.
        transport = getattr(getattr(response.raw, '_connection', None), 'sock', None)
        if transport is None:
            try:
                transport = response.raw._fp.fp.raw._sock
            except AttributeError:
                raise CollectionError('BODY_FAILED', '응답 읽기의 제한 시간을 적용하지 못했습니다.') from None
        if transport is None:
            raise CollectionError('BODY_FAILED', '응답 읽기의 제한 시간을 적용하지 못했습니다.')
        expired = Event()
        deadline = time.monotonic() + BODY_TIMEOUT

        def interrupt():
            expired.set()
            # response.close()는 읽기 버퍼의 잠금을 기다릴 수 있으므로 소켓 읽기를 먼저 깨운다.
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = Timer(BODY_TIMEOUT, interrupt)
        timer.daemon = True
        timer.start()
        chunks, size = [], 0
        try:
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if expired.is_set() or time.monotonic() >= deadline:
                        raise CollectionError('RESPONSE_TIMEOUT', '본문 읽기 제한 시간을 초과했습니다.')
                    size += len(chunk)
                    if size > limit:
                        raise CollectionError('RESPONSE_TOO_LARGE', '압축 해제한 응답이 크기 제한을 초과했습니다.')
                    chunks.append(chunk)
            except (requests.RequestException, OSError):
                if not expired.is_set() and time.monotonic() < deadline:
                    raise CollectionError('BODY_FAILED', '응답 본문을 읽지 못했습니다.') from None
                raise CollectionError('RESPONSE_TIMEOUT', '본문 읽기 제한 시간을 초과했습니다.') from None
            if expired.is_set() or time.monotonic() >= deadline:
                raise CollectionError('RESPONSE_TIMEOUT', '본문 읽기 제한 시간을 초과했습니다.')
            return b''.join(chunks)
        finally:
            # 응답을 닫거나 연결을 재사용하기 전에 감시 스레드가 반드시 종료되어야 한다.
            timer.cancel()
            timer.join()

    def _fetch(self, url, *, robots=False):
        for hop in range(4):
            url = validate_source_url(url)
            if not robots:
                self._check_robots(url)
            response = self._request(url)
            try:
                status = response.status_code
                if status in REDIRECTS:
                    location = response.headers.get('Location')
                    if not location or hop == 3:
                        raise CollectionError('REDIRECT_FAILED', '리다이렉트가 없거나 허용 횟수를 초과했습니다.')
                    url = validate_source_url(urljoin(url, location))
                    continue
                if robots and status in (404, 410):
                    return HTMLResponse(b'', 'text/plain; charset=utf-8', url)
                if status != 200:
                    raise CollectionError('HTTP_STATUS', f'HTTP {status} 응답으로 수집하지 못했습니다.')
                content_type = response.headers.get('Content-Type', '')
                media_type = content_type.split(';', 1)[0].strip().lower()
                allowed = {'text/plain'} if robots else {'text/html', 'application/xhtml+xml'}
                if media_type not in allowed:
                    raise CollectionError('CONTENT_TYPE', '지원하지 않는 응답 형식입니다.')
                limit = 256 * 1024 if robots else MAX_BYTES
                try:
                    length = int(response.headers.get('Content-Length', '0'))
                except ValueError:
                    raise CollectionError('CONTENT_LENGTH', '잘못된 응답 크기입니다.') from None
                if length < 0 or length > limit:
                    raise CollectionError('RESPONSE_TOO_LARGE', '응답 크기가 제한을 초과했습니다.')
                return HTMLResponse(self._read_body(response, limit), content_type, url)
            finally:
                response.close()
        raise CollectionError('REDIRECT_FAILED', '리다이렉트 제한을 초과했습니다.')

    def _check_robots(self, url):
        parts = urlsplit(url)
        host = parts.hostname
        if host not in self.robots:
            response = self._fetch(f'https://{host}/robots.txt', robots=True)
            try:
                text = response.body.decode('utf-8-sig')
            except UnicodeError:
                raise CollectionError('ROBOTS_INVALID', 'robots.txt 인코딩을 확인하지 못했습니다.') from None
            if '<html' in text.lower() or ('user-agent:' not in text.lower() and text.strip()):
                raise CollectionError('ROBOTS_INVALID', 'robots.txt 규칙을 확인하지 못했습니다.')
            policy = Protego.parse(text)
            self.robots[host] = policy
            delay = policy.crawl_delay(USER_AGENT) or 0
            rate = policy.request_rate(USER_AGENT)
            if rate:
                delay = max(delay, rate.seconds / rate.requests)
            self.intervals[host] = max(1.0, delay)
        if not self.robots[host].can_fetch(url, USER_AGENT):
            raise CollectionError('ROBOTS_DENIED', 'robots.txt에서 이 경로의 수집을 허용하지 않습니다.')

    def fetch(self, url):
        return self._fetch(url)
