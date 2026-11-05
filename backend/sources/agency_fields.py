import re
import unicodedata
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import URLValidator

from sources.fields import normalize_source_url


def validate_agency_source(value):
    # 기관 안내 출처만 확장한다. P1-7 크롤러의 허용 목록은 변경하지 않는다.
    from sources.services.source_registry import OFFICIAL_HOSTS, SENSITIVE_KEY
    from urllib.parse import parse_qsl

    value = normalize_source_url(value)
    URLValidator(schemes=['https'])(value)
    parts = urlsplit(value)
    if (parts.hostname not in OFFICIAL_HOSTS | {'www.passport.go.kr', 'passport.go.kr'}
            or parts.username or parts.password or parts.port not in (None, 443)
            or any(SENSITIVE_KEY.search(key) for key, _ in parse_qsl(parts.query))):
        raise ValidationError('허용된 공식 HTTPS 출처가 필요합니다.')
    return value


def normalize_agency_text(value):
    """지역·부서를 보존하며 NFKC, 공백, 중점·하이픈 표기만 통일한다."""
    if not isinstance(value, str):
        raise ValidationError('기관·부서·지역은 문자열이어야 합니다.')
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise ValidationError('제어 문자와 숨은 문자는 사용할 수 없습니다.')
    separators = str.maketrans('ㆍ・‐‑–—', '··----')
    value = unicodedata.normalize('NFKC', value.translate(separators)).translate(separators)
    return re.sub(r'\s+', ' ', value).strip()


def validate_lookup_text(value, *, maximum=255):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValidationError(f'{maximum}자 이하 문자열을 입력해 주세요.')
    value = normalize_agency_text(value)
    if not value or len(value) > maximum:
        raise ValidationError(f'공백이 아닌 {maximum}자 이하 문자열을 입력해 주세요.')
    if not any(char.isalnum() for char in value) or any(
        not (char.isalnum() or char in ' .·-()[]&') for char in value
    ):
        raise ValidationError('기관명·지역명에 허용되지 않는 형식입니다.')
    return value


class AgencyTextField(models.CharField):
    """일반 저장 및 리터럴 bulk 쓰기도 정규화하여 DB 고유 제약과 일치시킨다."""

    def to_python(self, value):
        if value is None:
            return value
        return normalize_agency_text(value)

    def pre_save(self, model_instance, add):
        value = self.to_python(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value
