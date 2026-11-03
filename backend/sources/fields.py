from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.db import models


def normalize_source_url(value):
    """네트워크 접근 없이 출처 식별용 URL을 보수적으로 정규화한다."""
    value = value.strip()
    if not value:
        return value
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
        if not hostname:
            return value
        hostname = hostname.encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError) as error:
        raise ValidationError('올바른 출처 URL을 입력해 주세요.') from error

    scheme = parts.scheme.lower()
    host = f'[{hostname}]' if ':' in hostname else hostname
    if port is not None and (scheme, port) not in (('http', 80), ('https', 443)):
        host = f'{host}:{port}'
    if '@' in parts.netloc:
        host = f'{parts.netloc.rsplit("@", 1)[0]}@{host}'
    return urlunsplit((scheme, host, parts.path or '/', parts.query, ''))


class NormalizedURLField(models.URLField):
    """검증·일반 저장·bulk 쓰기에 같은 정규화 규칙을 적용한다."""

    def to_python(self, value):
        value = super().to_python(value)
        return normalize_source_url(value) if value is not None else None

    def pre_save(self, model_instance, add):
        value = self.to_python(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value
