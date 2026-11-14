"""네트워크 요청 없이 공식 TXT를 검증하고 기존 승인 후보 저장소에 연결한다."""

import hashlib
import os
import stat
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from sources.models import SourceRevision

from .errors import CollectionError
from .normalization import OfficialRecord, official_url
from .refresh_service import stage_official_record
from .source_registry import CATEGORIES, REGIONS


MAX_TXT_BYTES = 2 * 1024 * 1024
TERMS_STATUSES = ('unverified', 'confirmed', 'prohibited')


@dataclass(frozen=True)
class ManualImportResult:
    outcome: str
    document_id: int
    revision_id: int | None
    content_hash: str


def read_official_txt(file):
    """지정 폴더 안의 일반 TXT만 제한 크기로 읽고 원본 바이트와 본문을 보존한다."""
    try:
        path = Path(file)
        if '..' in path.parts or str(path).startswith(('\\\\', '//')):
            raise ValueError
        path = path if path.is_absolute() else Path(settings.BASE_DIR) / path
        root = Path(getattr(settings, 'SOURCE_TXT_IMPORT_ROOT', Path(settings.BASE_DIR) / 'sources/data/manual'))
        # 심볼릭 링크·Windows junction·대체 데이터 스트림을 통한 경계 이탈도 거부한다.
        if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
            raise ValueError
        if any(':' in part for part in path.parts[1:]):
            raise ValueError
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve(strict=True)) or resolved.suffix.lower() != '.txt' or not resolved.is_file():
            raise ValueError
        with resolved.open('rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError
            if info.st_size > MAX_TXT_BYTES:
                raise CollectionError('TXT_TOO_LARGE', 'TXT 파일은 BOM을 포함하여 2 MiB 이하여야 합니다.')
            raw = stream.read(MAX_TXT_BYTES + 1)
        if len(raw) > MAX_TXT_BYTES:
            raise CollectionError('TXT_TOO_LARGE', 'TXT 파일은 BOM을 포함하여 2 MiB 이하여야 합니다.')
    except (OSError, ValueError, RuntimeError, TypeError):
        raise CollectionError('TXT_PATH_INVALID', '지정된 수동 등록 폴더 안의 존재하는 일반 .txt 파일이 필요합니다. 상위 경로·링크·네트워크 경로는 허용하지 않습니다.') from None
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        raise CollectionError('TXT_ENCODING_INVALID', 'UTF-8 또는 UTF-8 BOM TXT만 등록할 수 있습니다.') from None
    if not text.strip():
        raise CollectionError('TXT_EMPTY', '빈 TXT 본문은 등록할 수 없습니다.')
    if text.startswith(('%PDF-', 'PK\x03\x04', '{\\rtf')) or any(
        unicodedata.category(char) in ('Cc', 'Cs') and char not in '\r\n\t' for char in text
    ):
        raise CollectionError('TXT_BINARY_REJECTED', '바이너리 또는 제어 문자가 포함된 파일은 TXT로 등록할 수 없습니다.')
    return text, {
        'method': 'manual_txt', 'filename': resolved.name,
        'raw_sha256': hashlib.sha256(raw).hexdigest(), 'byte_size': len(raw),
        'encoding': 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8',
    }


def _text(value, maximum):
    """추적·이용조건에는 비어 있지 않은 제한 길이 문자열을 기록한다."""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(
        unicodedata.category(char) == 'Cc' for char in value
    ):
        raise CollectionError('IMPORT_METADATA_INVALID', '등록자·이용조건 설명의 필수값과 길이·제어 문자를 확인하세요.')
    return value.strip()


def import_official_txt(*, file, url, agency, title, region, category, registered_by,
                        terms_note, terms_status='unverified', terms_url=None,
                        terms_checked_on=None, published_at=None):
    """검증을 마친 뒤에만 DB에 후보를 저장하며 승인·AI 처리·외부 요청은 수행하지 않는다."""
    started_at = timezone.now()
    source_url = official_url(url, 'html')
    if not source_url or region not in REGIONS or category not in CATEGORIES or terms_status not in TERMS_STATUSES:
        raise CollectionError('IMPORT_METADATA_INVALID', '공식 URL·적용 지역·분류·이용조건 확인 상태를 확인하세요.')
    actor, note = _text(registered_by, 255), _text(terms_note, 4000)
    for value in (published_at, terms_checked_on):
        if value is not None and (type(value) is not date or value > timezone.localdate()):
            raise CollectionError('DATE_INVALID', '확인된 과거 또는 오늘의 날짜만 입력하세요.')
    if terms_status == 'confirmed' and terms_checked_on is None:
        raise CollectionError('TERMS_DATE_REQUIRED', '이용조건 확인 완료에는 --terms-checked-on 날짜가 필요합니다.')
    terms_link = official_url(terms_url, 'html')
    text, trace = read_official_txt(file)
    trace['registered_by'] = actor
    record = OfficialRecord(
        provider='html', external_id='', source_url=source_url, agency=agency, title=title,
        region=region, category=category, published_at=published_at, content_text=text,
        metadata={
            'manual_import': trace,
            'usage_terms': {
                'status': terms_status, 'note': note, 'url': terms_link,
                'checked_by': actor if terms_checked_on else None,
                'checked_on': terms_checked_on.isoformat() if terms_checked_on else None,
            },
        },
    ).validate()
    outcome, document = stage_official_record(record, started_at, timezone.now())
    revision = SourceRevision.objects.filter(
        document=document, content_hash=record.content_hash,
        base_version=document.version, review_status='PENDING_REVIEW',
    ).first()
    return ManualImportResult(outcome, document.pk, revision.pk if revision else None, record.content_hash)
