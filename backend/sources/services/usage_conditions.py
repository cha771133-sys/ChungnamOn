"""수동 자료의 이용조건을 기존 관리자 승인 경계에서 검사한다."""

from .errors import CollectionError
from .source_registry import load_registry


def is_manual_metadata(metadata):
    """수동 입력 표식은 이용조건이 누락되어도 유지되는 승인 검증 기준이다."""
    return isinstance(metadata, dict) and 'manual_import' in metadata


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
    if any(spec.url == snapshot.get('source_url') and not spec.enabled for spec in load_registry()):
        raise CollectionError('SOURCE_DISABLED', '목록에서 이용조건 등의 사유로 제외한 자료입니다. 별도 정책 검토 전 승인할 수 없습니다.')
