"""수동 자료의 이용조건과 수집 목록 제외 여부를 참고 기록으로 보존한다."""

from posixpath import normpath
from urllib.parse import parse_qs, unquote, urlsplit

from .source_registry import load_registry


def is_manual_metadata(metadata):
    """이용조건 유무와 별개로 수동 등록 출처를 식별한다."""
    return isinstance(metadata, dict) and 'manual_import' in metadata


def inherit_manual_terms(latest, previous, content_hash):
    """자동 관측의 실제 출처와 구분하여 이전 TXT 이력·조건을 보존한다.

    manual_import는 원래 TXT의 지문이다. 현재 자동 본문의 지문으로 해석하지 않도록
    당시 본문 해시와 현재 수집 방식을 함께 기록한다. 명시적 수동 재등록은 정정으로 본다.
    """
    if is_manual_metadata(latest) or not is_manual_metadata(previous):
        return latest
    return {
        **latest, 'manual_import': previous['manual_import'],
        'usage_terms': previous.get('usage_terms', {}),
        'manual_import_content_hash': previous.get('manual_import_content_hash', content_hash),
        'collection_method': 'automatic',
    }


def is_registry_excluded(url):
    """추적 쿼리·순서·공식 www 별칭과 무관하게 목록에서 제외한 자료인지 판단한다."""
    if not url:
        return False
    parts = urlsplit(url)
    host, path = parts.hostname.removeprefix('www.'), normpath(unquote(parts.path))
    query = parse_qs(parts.query, keep_blank_values=True)
    for spec in load_registry():
        if spec.enabled:
            continue
        blocked = urlsplit(spec.url)
        if (host, path) != (blocked.hostname.removeprefix('www.'), normpath(unquote(blocked.path))):
            continue
        identifiers = parse_qs(blocked.query, keep_blank_values=True)
        if host == 'asan.go.kr' and path == '/main/cms' and 'pds_no' in identifiers:
            # 아산 공지는 같은 pds_no를 보수적으로 같은 제외 자료로 본다.
            # 화면·게시판 표시용 쿼리의 생략/추가와 무관하게 같은 공지의 제외 표시를 기록한다.
            identifiers = {'pds_no': identifiers['pds_no']}
        if all(set(values).issubset(query.get(key, [])) for key, values in identifiers.items()):
            return True
    return False
