from pathlib import Path
from unittest.mock import Mock

from sources.services.collector import HTMLResponse
from sources.services.source_registry import SourceSpec


HTML = (Path(__file__).parent / 'fixtures' / 'administrative.html').read_bytes()
URL = 'https://www.asan.go.kr/test-fixtures/notice?id=1'


def spec(**changes):
    return SourceSpec(**{
        'url': URL, 'title': '주민세 납부 안내', 'agency': '아산시청',
        'region': '아산시', 'category': '지방세', **changes,
    })


def collector(body=HTML):
    return Mock(fetch=Mock(return_value=HTMLResponse(body, 'text/html; charset=utf-8', URL)))


def response(status=200, body=HTML, **headers):
    result = Mock()
    result.status_code = status
    result.headers = {'Content-Type': 'text/html; charset=utf-8', **headers}
    result.iter_content.return_value = [body]
    return result
