import os

from ..errors import CollectionError
from ..normalization import OfficialRecord, canonical_text, official_date, optional_official_url
from .transport import APITransport


BASE_URL = 'https://www.law.go.kr/DRF/'
REGIONS = {'충청남도': ('6440000', None), '천안시': ('6440000', '4490000'), '아산시': ('6440000', '4520000')}


def matches_region(institution, region):
    """수집 관할만 판별하며 도 자체 자료와 도내 다른 시·군 자료를 구분한다."""
    # org만 지정하면 도내 다른 시·군도 응답될 수 있다. 도 자체 자료와 시 자료를 구분한다.
    return institution.strip() == '충청남도' if region == '충청남도' else region in institution


def values_for(data, key):
    """JSON 또는 XML에서 변환한 중첩 객체를 순회하여 같은 이름의 값을 찾는다."""
    if isinstance(data, dict):
        for name, value in data.items():
            if name == key:
                yield value
            else:
                yield from values_for(value, key)
    elif isinstance(data, list):
        for value in data:
            yield from values_for(value, key)


def scalar(data, key, default=''):
    """중첩 응답에서 문자열·정수인 첫 값을 반환하고 누락은 명시한 기본값으로 둔다."""
    for value in values_for(data, key):
        if isinstance(value, (str, int)):
            return str(value)
    return default


class LawAPIClient:
    def __init__(self, transport=None):
        """환경의 OC 인증값을 사용하며 인증 설정 누락은 요청 오류와 구분한다."""
        self.oc = os.environ.get('LAW_API_OC', '').strip()
        if not self.oc:
            raise CollectionError('AUTH_MISSING', 'LAW_API_OC가 필요합니다.')
        self.transport = transport or APITransport()

    def close(self):
        self.transport.close()

    def _get(self, endpoint, **params):
        """공식 XML 요청을 보내고 HTTP 200의 인증·내부 오류 본문도 실패로 처리한다."""
        data = self.transport.get(BASE_URL + endpoint, {'OC': self.oc, 'target': 'ordin', 'type': 'XML', **params})
        error_root = data.get('Response', data.get('response', data))
        if isinstance(error_root, dict) and any(key.lower() in ('error', 'errors', 'openapi_serviceresponse', 'result', 'errorcode') for key in error_root):
            response_message = str(error_root.get('result', ''))
            if ('사용자' in response_message and '검증' in response_message) or '인증' in response_message:
                raise CollectionError('AUTH_FAILED', '법제처 인증값 및 등록된 호출 서버 권한을 확인하세요.')
            raise CollectionError('API_ERROR', '법제처 API가 내부 오류를 반환했습니다.')
        return data

    def list_ordinances(self, *, query, region, limit=100, per_page=100, history=False):
        """관할을 재검증하며 현행·연혁 목록을 구분하고 반복 페이지와 수집 상한을 검사한다."""
        if not query.strip() or region not in REGIONS or not 1 <= limit <= 1000 or not 1 <= per_page <= 100:
            raise CollectionError('FILTER_REQUIRED', '검색어·충남/천안/아산 범위와 제한된 수집량이 필요합니다.')
        org, sborg = REGIONS[region]
        filters = {'org': org, **({'sborg': sborg} if sborg else {})}
        seen, count = set(), 0
        for page in range(1, 11):
            data = self._get('lawSearch.do', query=query, nw=2 if history else 1, page=page, display=per_page, **filters)
            root = data.get('OrdinSearch', data.get('LawSearch'))
            if not isinstance(root, dict):
                raise CollectionError('INVALID_RESPONSE', '법제처 목록 응답 구조를 확인하지 못했습니다.')
            try:
                total = int(root['totalCnt'])
                if total < 0 or int(root.get('page', page)) != page:
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                raise CollectionError('INVALID_RESPONSE', '법제처 목록의 전체 건수·페이지가 올바르지 않습니다.') from None
            rows = root.get('law', root.get('ordin', [])) or []
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list) or len(rows) > per_page or any(not isinstance(row, dict) for row in rows):
                raise CollectionError('INVALID_RESPONSE', '법제처 목록 항목이 올바르지 않습니다.')
            if not rows and (page - 1) * per_page < total:
                raise CollectionError('PAGINATION_STALLED', '법제처 목록이 완료 전에 중단되었습니다.')
            for row in rows:
                external_id = scalar(row, '자치법규ID')
                identity = (external_id, scalar(row, '자치법규일련번호')) if history else external_id
                if not external_id or identity in seen:
                    raise CollectionError('PAGINATION_STALLED', '자치법규 ID가 없거나 페이지가 반복되었습니다.')
                seen.add(identity)
                # API 검색 범위와 별개로 실제 관할기관을 검증한다.
                institution = scalar(row, '지자체기관명')
                if not matches_region(institution, region):
                    continue
                yield {**row, '_history': history, '_collection_region': region}
                count += 1
                if count >= limit:
                    return
            if page * per_page >= total:
                return
        raise CollectionError('PAGE_LIMIT', '법제처 검색 범위를 초과했습니다. 검색어를 좁히세요.')

    def detail(self, external_id):
        """안정적인 자치법규 ID로 본문을 요청하고 응답 ID와 조문 존재를 검증한다."""
        data = self._get('lawService.do', ID=external_id)
        root = data.get('자치법규', data.get('OrdinService', data.get('LawService')))
        if not isinstance(root, dict) or scalar(root, '자치법규ID') != external_id or not scalar(root, '조내용'):
            raise CollectionError('INVALID_RESPONSE', '자치법규 본문·조문·외부 ID를 확인하지 못했습니다.')
        return root

    def fetch(self, target):
        """목록과 본문의 개정 버전을 대조하고 공포일·시행일·관할을 별도로 보존한다."""
        data = self.detail(target.external_id)
        institution = scalar(data, '지자체기관명')
        collection_region = target.configuration.get('collection_region', '')
        if collection_region not in REGIONS or not matches_region(institution, collection_region):
            raise CollectionError('REGION_MISMATCH', '등록 범위와 자치법규 관할기관이 일치하지 않습니다.')
        # ID는 안정적이지만 상세링크의 일련번호는 개정마다 달라질 수 있다.
        # 현재 목록을 다시 확인해 같은 버전에서 제공한 링크만 근거로 사용한다.
        listing = None
        for row in self.list_ordinances(query=f'"{scalar(data, "자치법규명")}"', region=collection_region, limit=1000):
            if scalar(row, '자치법규ID') == target.external_id:
                listing = row
                break
        serial = scalar(data, '자치법규일련번호')
        if listing and serial and scalar(listing, '자치법규일련번호') != serial:
            raise CollectionError('VERSION_CONFLICT', '목록과 본문의 개정 버전이 달라 다음 실행에서 다시 확인해야 합니다.')
        link = optional_official_url(listing.get('자치법규상세링크'), 'law') if listing else None
        return OfficialRecord(
            provider='law', external_id=target.external_id, source_url=link,
            title=scalar(data, '자치법규명'), agency=institution, region='확인 필요',
            category='자치법규', published_at=official_date(scalar(data, '공포일자')),
            effective_at=official_date(scalar(data, '시행일자')), content_text=canonical_text(data),
            metadata={
                'collection_region': collection_region, 'jurisdiction': institution,
                'application_region_verified': False,
                'listing_scope': '현행 목록' if listing else '현행 목록 확인 불가',
                'legal_effect': '확인 필요', 'serial': serial,
                'amendment': scalar(data, '제개정정보'), 'source_link_available': bool(link),
            },
        ).validate()
