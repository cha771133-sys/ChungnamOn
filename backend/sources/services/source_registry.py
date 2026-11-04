import ipaddress
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml
from django.core.exceptions import ValidationError

from sources.fields import normalize_source_url

from .errors import CollectionError


# 하위 도메인을 자동 허용하지 않는다. 기관·정책 검토 후 코드 리뷰로 확장한다.
OFFICIAL_HOSTS = frozenset({
    'www.chungnam.go.kr', 'chungnam.go.kr', 'www.cheonan.go.kr', 'cheonan.go.kr',
    'www.asan.go.kr', 'asan.go.kr', 'www.gov.kr', 'm.gov.kr',
    'www.hikorea.go.kr', 'www.immigration.go.kr', 'immigration.go.kr',
    'www.moj.go.kr', 'www.nhis.or.kr',
})
REGIONS = frozenset({'충청남도', '천안시', '아산시', '전국'})
CATEGORIES = frozenset({'지방세', '건강보험', '출입국·체류'})
DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / 'data' / 'source_urls.yaml'
SENSITIVE_KEY = re.compile(r'token|password|passwd|secret|api.?key|session|authorization', re.I)


def validate_source_url(value):
    """P1-6 정규화를 재사용하며 네트워크 없이 요청 가능 범위를 제한한다."""
    try:
        if not isinstance(value, str) or len(value) > 2048 or re.search(r'\s|[\x00-\x1f\\]', value):
            raise ValueError
        parts = urlsplit(value)
        if parts.scheme.lower() != 'https' or parts.username or parts.password:
            raise ValueError
        if not parts.hostname or parts.port not in (None, 443):
            raise ValueError
        try:
            ipaddress.ip_address(parts.hostname)
        except ValueError:
            pass
        else:
            raise ValueError
        normalized = normalize_source_url(value)
        parts = urlsplit(normalized)
        if parts.hostname not in OFFICIAL_HOSTS:
            raise ValueError
        if any(SENSITIVE_KEY.search(key) for key, _ in parse_qsl(parts.query)):
            raise ValueError
        if re.search(r';[^/]*(?:session|token|password)', unquote(parts.path), re.I):
            raise ValueError
        return normalized
    except (ValueError, UnicodeError, ValidationError):
        raise CollectionError('URL_REJECTED', '허용된 공식 HTTPS URL이 아니거나 민감한 정보가 포함되어 있습니다.') from None


@dataclass(frozen=True)
class SourceSpec:
    url: str
    title: str
    agency: str
    region: str
    category: str
    published_at: date | None = None
    notes: str = ''
    enabled: bool = True


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    values = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in values:
            raise ValueError('중복 또는 잘못된 YAML 키')
        values[key] = loader.construct_object(value_node)
    return values


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_registry(path=DEFAULT_REGISTRY):
    try:
        raw = Path(path).read_text(encoding='utf-8')
        if len(raw) > 1_000_000:
            raise ValueError
        data = yaml.load(raw, Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        raise CollectionError('REGISTRY_INVALID', 'URL 목록 파일을 읽을 수 없거나 YAML 형식이 잘못되었습니다.') from None
    if not isinstance(data, dict) or set(data) != {'sources'} or not isinstance(data['sources'], list):
        raise CollectionError('REGISTRY_INVALID', '최상위 sources 목록이 필요합니다.')
    result, seen = [], set()
    for index, entry in enumerate(data['sources'], 1):
        try:
            if not isinstance(entry, dict):
                raise ValueError('항목은 객체여야 합니다.')
            allowed = set(SourceSpec.__dataclass_fields__)
            if set(entry) - allowed:
                raise ValueError('지원하지 않는 필드가 있습니다.')
            for field, maximum in (('url', 2048), ('title', 500), ('agency', 255), ('region', 100), ('category', 100)):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                    raise ValueError(f'{field} 필수 문자열 또는 길이 제한을 확인하세요.')
            if entry['region'] not in REGIONS or entry['category'] not in CATEGORIES:
                raise ValueError('region 또는 category 표기 규칙을 확인하세요.')
            if not isinstance(entry.get('enabled', True), bool) or not isinstance(entry.get('notes', ''), str):
                raise ValueError('enabled는 불리언, notes는 문자열이어야 합니다.')
            published = entry.get('published_at')
            if isinstance(published, str):
                published = date.fromisoformat(published)
            if published is not None and (not isinstance(published, date) or isinstance(published, datetime)):
                raise ValueError('published_at은 확인된 YYYY-MM-DD 또는 null이어야 합니다.')
            url = validate_source_url(entry['url'])
            if url in seen:
                raise ValueError('정규화 후 중복된 URL입니다.')
            seen.add(url)
            result.append(SourceSpec(**{**entry, 'url': url, 'published_at': published}))
        except (ValueError, TypeError) as error:
            raise CollectionError('REGISTRY_INVALID', f'{index}번 항목: {error}') from None
        except CollectionError:
            raise CollectionError('REGISTRY_INVALID', f'{index}번 항목: 허용되지 않은 URL입니다.') from None
    return result
