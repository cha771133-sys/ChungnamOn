"""수동 자료의 이용조건을 기존 관리자 승인 경계에서 검사한다."""

from posixpath import normpath
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import CollectionError
from .source_registry import load_registry


def is_manual_metadata(metadata):
    """수동 입력 표식은 이용조건이 누락되어도 유지되는 승인 검증 기준이다."""
    return isinstance(metadata, dict) and 'manual_import' in metadata


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
            # 화면·게시판 표시용 쿼리의 생략/추가로 변경금지 공지를 다시 승인하지 않는다.
            identifiers = {'pds_no': identifiers['pds_no']}
        if all(set(values).issubset(query.get(key, [])) for key, values in identifiers.items()):
            return True
    return False


def validate_manual_approval(snapshot, *, previous_metadata=None):
    """TXT의 AI 가공 허용 확인과 기존 수집 제외 결정을 자동 승인으로 대체하지 않는다."""
    metadata = snapshot.get('metadata', {})
    if not is_manual_metadata(metadata) and not is_manual_metadata(previous_metadata):
        return
    terms = metadata.get('usage_terms', {})
    if not isinstance(terms, dict) or terms.get('status') != 'confirmed' or not all(
        terms.get(key) for key in ('note', 'checked_by', 'checked_on')
    ):
        raise CollectionError('TERMS_UNCONFIRMED', 'AI 정제·청크·임베딩·검색 이용이 허용되는지 확인하고 TXT를 다시 등록하세요.')
    if is_registry_excluded(snapshot.get('source_url')):
        raise CollectionError('SOURCE_DISABLED', '목록에서 이용조건 등의 사유로 제외한 자료입니다. 별도 정책 검토 전 승인할 수 없습니다.')
