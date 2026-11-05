import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from sources.fields import normalize_source_url

from .errors import CollectionError
from .source_registry import validate_source_url


def official_url(value, provider):
    if not value:
        return None
    if not isinstance(value, str):
        raise CollectionError('SOURCE_URL_INVALID', '공식 상세 페이지 URL은 문자열이어야 합니다.')
    if provider == 'html':
        return validate_source_url(value)
    # 법제처가 제공하는 상대 상세링크만 공식 호스트에 결합한다.
    if provider == 'law' and value.startswith('/') and not value.startswith('//'):
        value = urljoin('https://www.law.go.kr', value)
    try:
        parts = urlsplit(value)
        hosts = {'www.gov.kr', 'm.gov.kr', 'www.gov24.go.kr'} if provider == 'public_data' else {'www.law.go.kr', 'law.go.kr'}
        if provider == 'law' and parts.scheme == 'http' and parts.hostname in hosts and parts.port in (None, 80) and not parts.username and not parts.password:
            # 법제처가 제공한 경로·쿼리를 보존하고 검증된 HTTPS 호스트로만 올린다.
            value = urlunsplit(('https', parts.hostname, parts.path, parts.query, parts.fragment))
            parts = urlsplit(value)
        keys = [unquote(key) for key, _ in parse_qsl(parts.query)]
        if parts.scheme != 'https' or parts.hostname not in hosts or parts.port not in (None, 443) or parts.username or parts.password or len(value) > 2048 or re.search(r'\s|[\x00-\x1f\\]', value):
            raise ValueError
        if any(re.search(r'(?i)^(oc)$|key|token|secret|password|session|authorization', key) for key in keys) or parts.path.startswith('/DRF/'):
            raise ValueError
        return normalize_source_url(value)
    except (ValueError, TypeError):
        raise CollectionError('SOURCE_URL_INVALID', '인증정보 없는 공식 상세 페이지를 확인하지 못했습니다.') from None


def optional_official_url(value, provider):
    """API의 링크가 공개 근거로 부적합하면 외부 ID로 수집하고 링크는 비워 둔다."""
    try:
        return official_url(value, provider)
    except CollectionError as error:
        if error.code != 'SOURCE_URL_INVALID':
            raise
        return None


def official_date(value):
    if value in (None, '', '00000000'):
        return None
    try:
        text = str(value)
        return date.fromisoformat(text if '-' in text else f'{text[:4]}-{text[4:6]}-{text[6:8]}')
    except ValueError:
        raise CollectionError('DATE_INVALID', '공식 발행일·시행일 형식을 확인하세요.') from None


def canonical_text(data):
    # 키 정렬로 API JSON 객체의 필드 순서만 통일한다. 자격·조건·코드값은 그대로 보존한다.
    return unicodedata.normalize('NFC', json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))


@dataclass(frozen=True)
class OfficialRecord:
    provider: str
    external_id: str
    source_url: str | None
    title: str
    agency: str
    region: str
    category: str
    content_text: str
    published_at: date | None = None
    effective_at: date | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self):
        return hashlib.sha256(self.content_text.encode('utf-8')).hexdigest()

    def validate(self):
        if self.provider not in ('html', 'public_data', 'law'):
            raise CollectionError('RECORD_INVALID', '수집 제공자가 올바르지 않습니다.')
        if self.provider != 'html' and (not self.external_id or len(self.external_id) > 100):
            raise CollectionError('RECORD_INVALID', '외부 자료 ID가 필요합니다.')
        if self.provider == 'html' and not self.source_url:
            raise CollectionError('RECORD_INVALID', 'HTML 출처 URL이 필요합니다.')
        official_url(self.source_url, self.provider)
        for value, maximum in ((self.title, 500), (self.agency, 255), (self.region, 100), (self.category, 100)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise CollectionError('RECORD_INVALID', '필수 메타데이터 또는 길이 제한을 확인하세요.')
        if not self.content_text.strip() or len(self.content_text.encode('utf-8')) > 2 * 1024 * 1024:
            raise CollectionError('RECORD_INVALID', '빈 본문 또는 본문 크기 제한을 확인하세요.')
        return self

    def snapshot(self):
        return {
            'source_url': official_url(self.source_url, self.provider), 'title': self.title,
            'agency': self.agency, 'region': self.region, 'category': self.category,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'effective_at': self.effective_at.isoformat() if self.effective_at else None,
            'metadata': self.metadata,
        }
