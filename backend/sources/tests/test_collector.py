import socket
import time
from http.client import HTTPResponse
from threading import Event, Thread
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase
from urllib3.response import HTTPResponse as Urllib3Response

from sources.services.collector import Collector, MAX_BYTES, PinnedHTTPSAdapter, resolve_public_host, retry_delay
from sources.services.errors import CollectionError

from .pipeline_helpers import HTML, URL, response


class CollectorTests(SimpleTestCase):
    def setUp(self):
        self.collector = Collector()
        self.addCleanup(self.collector.close)
        self.sleep = patch('sources.services.collector.time.sleep').start()
        self.addCleanup(patch.stopall)
        self.get = patch.object(self.collector.session, 'get').start()
        self.robots = patch.object(self.collector, '_check_robots').start()

    def test_normal_html_with_headers_timeout_stream_and_no_auto_redirects(self):
        self.get.return_value = response()
        result = self.collector.fetch(URL)
        self.assertEqual(result.body, HTML)
        self.assertFalse(self.collector.session.trust_env)
        self.assertIn('ChungnamOnSources', self.collector.session.headers['User-Agent'])
        self.assertEqual(self.get.call_args.kwargs['timeout'], (5, 15))
        self.assertFalse(self.get.call_args.kwargs['allow_redirects'])
        self.assertTrue(self.get.call_args.kwargs['stream'])
        self.get.return_value.close.assert_called_once()

    def test_permanent_http_errors_are_not_retried(self):
        for status in (403, 404):
            with self.subTest(status=status):
                self.get.reset_mock()
                self.get.return_value = response(status)
                with self.assertRaisesMessage(CollectionError, str(status)):
                    self.collector.fetch(URL)
                self.assertEqual(self.get.call_count, 1)

    def test_transient_http_retries_are_bounded_and_all_responses_closed(self):
        replies = [response(500) for _ in range(3)]
        self.get.side_effect = replies
        with self.assertRaisesMessage(CollectionError, '500'):
            self.collector.fetch(URL)
        self.assertEqual(self.get.call_count, 3)
        for reply in replies:
            reply.close.assert_called_once()

    def test_retry_can_recover_and_retry_after_is_honored(self):
        self.get.side_effect = [response(429, **{'Retry-After': '5'}), response()]
        self.assertEqual(self.collector.fetch(URL).body, HTML)
        self.sleep.assert_any_call(5)
        self.assertEqual(retry_delay('Wed, 01 Jan 2020 00:00:00 GMT', 0), 1)
        self.assertEqual(retry_delay('invalid', 1), 2)
        with self.assertRaisesMessage(CollectionError, 'RETRY_LATER'):
            retry_delay('3600', 0)

    def test_timeout_and_network_failures_hide_exception_url(self):
        for error in (requests.Timeout('secret-token'), requests.ConnectionError('secret-token')):
            with self.subTest(error=type(error)):
                self.get.reset_mock()
                self.get.side_effect = error
                with self.assertRaisesMessage(CollectionError, 'NETWORK_FAILED') as caught:
                    self.collector.fetch(URL)
                self.assertNotIn('secret-token', str(caught.exception))
                self.assertEqual(self.get.call_count, 3)

    def test_tls_errors_are_not_retried(self):
        self.get.side_effect = requests.exceptions.SSLError('secret')
        with self.assertRaisesMessage(CollectionError, 'TLS_FAILED'):
            self.collector.fetch(URL)
        self.assertEqual(self.get.call_count, 1)

    def test_non_html_and_missing_content_type_rejected(self):
        for content_type in ('application/pdf', 'application/octet-stream', ''):
            self.get.return_value = response(**{'Content-Type': content_type})
            with self.subTest(content_type=content_type), self.assertRaisesMessage(CollectionError, 'CONTENT_TYPE'):
                self.collector.fetch(URL)

    def test_declared_and_streamed_response_size_limits(self):
        for reply in (response(**{'Content-Length': str(MAX_BYTES + 1)}), response(body=b'x' * (MAX_BYTES + 1))):
            self.get.return_value = reply
            with self.assertRaisesMessage(CollectionError, 'RESPONSE_TOO_LARGE'):
                self.collector.fetch(URL)
            reply.close.assert_called_once()

    def test_stream_error_is_failure_and_connection_closed(self):
        reply = response()
        reply.iter_content.side_effect = requests.ConnectionError('response secret')
        self.get.return_value = reply
        with self.assertRaisesMessage(CollectionError, 'BODY_FAILED'):
            self.collector.fetch(URL)
        reply.close.assert_called_once()

    def test_disallowed_redirect_never_requested(self):
        for location in ('https://evil.example/', 'https://127.0.0.1/', 'http://www.asan.go.kr/'):
            self.get.reset_mock()
            self.get.return_value = response(302, Location=location)
            with self.subTest(location=location), self.assertRaisesMessage(CollectionError, 'URL_REJECTED'):
                self.collector.fetch(URL)
            self.assertEqual(self.get.call_count, 1)

    def test_allowed_redirect_and_bounded_loop_recheck_robots(self):
        self.get.side_effect = [response(302, Location='/changed'), response()]
        self.assertEqual(self.collector.fetch(URL).url, 'https://www.asan.go.kr/changed')
        self.robots.assert_any_call('https://www.asan.go.kr/changed')
        self.get.side_effect = None
        self.get.return_value = response(302, Location='/again')
        self.get.reset_mock()
        with self.assertRaisesMessage(CollectionError, 'REDIRECT_FAILED'):
            self.collector.fetch(URL)
        self.assertEqual(self.get.call_count, 4)

    def test_requests_are_paced_on_same_host(self):
        self.get.return_value = response()
        self.collector.fetch(URL)
        self.collector.fetch(URL)
        self.assertTrue(any(call.args[0] > 0 for call in self.sleep.call_args_list))


class BodyDeadlineTests(SimpleTestCase):
    def test_trickling_body_is_interrupted_before_iter_content_yields(self):
        # 실제 소켓·HTTP 디코더로 청크가 완성되지 않는 읽기를 재현한다. 외부 요청은 없다.
        for framing in ('length', 'chunked', 'close'):
            with self.subTest(framing=framing):
                reader, writer = socket.socketpair()
                reader.settimeout(2)
                stop = Event()
                headers = {
                    'length': b'Content-Length: 100000\r\n',
                    'chunked': b'Transfer-Encoding: chunked\r\n',
                    'close': b'Connection: close\r\n',
                }[framing]
                writer.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n' + headers + b'\r\n')
                if framing == 'chunked':
                    writer.sendall(b'186a0\r\n')

                def trickle():
                    try:
                        for _ in range(40):
                            if stop.wait(0.02):
                                break
                            writer.sendall(b'x')
                        writer.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

                sender = Thread(target=trickle, daemon=True)
                sender.start()
                reply = requests.Response()
                wire = HTTPResponse(reader, method='GET')
                wire.begin()
                reply.status_code = 200
                reply.headers.update(dict(wire.getheaders()))
                reply.raw = Urllib3Response(
                    body=wire, headers=reply.headers, original_response=wire,
                    preload_content=False, request_method='GET',
                )
                client = Collector()
                try:
                    with (
                        patch.object(client, '_check_robots'),
                        patch.object(client, '_request', return_value=reply),
                        patch('sources.services.collector.BODY_TIMEOUT', 0.2, create=True),
                    ):
                        started = time.monotonic()
                        with self.assertRaisesMessage(CollectionError, 'RESPONSE_TIMEOUT'):
                            client.fetch(URL)
                        self.assertLess(time.monotonic() - started, 1.5)
                        self.assertTrue(reply.raw.closed)
                finally:
                    stop.set()
                    writer.close()
                    reader.close()
                    sender.join(timeout=2)
                    reply.close()
                    client.close()


class RobotsTests(SimpleTestCase):
    def setUp(self):
        self.collector = Collector()
        self.addCleanup(self.collector.close)
        patch('sources.services.collector.time.sleep').start()
        self.addCleanup(patch.stopall)
        self.get = patch.object(self.collector.session, 'get').start()

    def test_robots_allow_and_cache_and_crawl_delay(self):
        self.get.side_effect = [response(body=b'User-agent: *\nAllow: /\nCrawl-delay: 3', **{'Content-Type': 'text/plain'}), response(), response()]
        self.collector.fetch(URL)
        self.collector.fetch(URL)
        self.assertEqual(self.get.call_count, 3)
        self.assertEqual(self.collector.intervals['www.asan.go.kr'], 3)

    def test_wildcard_and_specific_allow_are_respected(self):
        self.get.return_value = response(body=b'User-agent: *\nDisallow: /*?*\nAllow: /public*', **{'Content-Type': 'text/plain'})
        with self.assertRaisesMessage(CollectionError, 'ROBOTS_DENIED'):
            self.collector.fetch(URL)
        self.assertEqual(self.get.call_count, 1)

    def test_robots_404_means_no_rules_but_403_or_500_fail_closed(self):
        self.get.side_effect = [response(404), response()]
        self.assertEqual(self.collector.fetch(URL).body, HTML)
        self.collector.robots.clear()
        self.get.side_effect = None
        self.get.return_value = response(403)
        with self.assertRaisesMessage(CollectionError, '403'):
            self.collector.fetch(URL)
        self.get.return_value = response(503)
        with self.assertRaisesMessage(CollectionError, '503'):
            self.collector.fetch(URL)

    def test_html_disguised_as_robots_and_invalid_encoding_fail_closed(self):
        for body in (b'<html>Error</html>', b'not rules', b'\xff'):
            self.get.return_value = response(body=body, **{'Content-Type': 'text/plain'})
            with self.subTest(body=body), self.assertRaises(CollectionError):
                self.collector.fetch(URL)

    def test_disallowed_robots_redirect_is_blocked(self):
        self.get.return_value = response(302, Location='https://evil.example/robots.txt')
        with self.assertRaisesMessage(CollectionError, 'URL_REJECTED'):
            self.collector.fetch(URL)
        self.assertEqual(self.get.call_count, 1)


class DNSAndPinningTests(SimpleTestCase):
    @patch('sources.services.collector.socket.getaddrinfo')
    def test_dns_private_mixed_loopback_linklocal_and_multicast_rejected(self, lookup):
        for addresses in (['127.0.0.1'], ['10.0.0.1'], ['169.254.169.254'], ['::1'], ['fd00::1'], ['224.0.0.1'], ['8.8.8.8', '192.168.1.1']):
            lookup.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]
            with self.subTest(addresses=addresses), self.assertRaisesMessage(CollectionError, 'DNS_REJECTED'):
                resolve_public_host('www.asan.go.kr')

    @patch('sources.services.collector.socket.getaddrinfo', side_effect=socket.gaierror)
    def test_dns_failure_is_sanitized(self, lookup):
        with self.assertRaisesMessage(CollectionError, 'DNS_FAILED'):
            resolve_public_host('www.asan.go.kr')

    @patch('sources.services.collector.resolve_public_host', return_value=['8.8.8.8'])
    def test_verified_ip_is_connection_target_with_original_tls_identity(self, lookup):
        adapter = PinnedHTTPSAdapter()
        self.addCleanup(adapter.close)
        request = requests.Request('GET', URL).prepare()
        with patch.object(adapter.poolmanager, 'connection_from_host') as pool:
            adapter.get_connection_with_tls_context(request, True)
        values = pool.call_args.kwargs
        self.assertEqual(values['host'], '8.8.8.8')
        self.assertEqual(values['pool_kwargs']['assert_hostname'], 'www.asan.go.kr')
        self.assertEqual(values['pool_kwargs']['server_hostname'], 'www.asan.go.kr')
        self.assertEqual(values['pool_kwargs']['cert_reqs'], 'CERT_REQUIRED')
