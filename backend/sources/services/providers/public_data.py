import os

from ..errors import CollectionError
from ..normalization import OfficialRecord, canonical_text, optional_official_url
from .transport import APITransport


BASE_URL = 'https://api.odcloud.kr/api/gov24/v3/'
REGION_AGENCIES = {'충청남도': '충청남도', '천안시': '천안시', '아산시': '아산시'}


class PublicDataClient:
    def __init__(self, transport=None):
        """로컬 환경의 인증키를 읽으며 키가 없으면 외부 요청 전에 실패한다."""
        self.key = os.environ.get('PUBLIC_DATA_SERVICE_KEY', '').strip()
        if not self.key:
            raise CollectionError('AUTH_MISSING', 'PUBLIC_DATA_SERVICE_KEY가 필요합니다.')
        self.transport = transport or APITransport()

    def close(self):
        self.transport.close()

    def _pages(self, endpoint, conditions, *, per_page=100, max_pages=10):
        """건수·페이지·오류 본문을 검증하며 최대 10페이지 안에서 원본 행을 반환한다."""
        if not 1 <= per_page <= 100 or not 1 <= max_pages <= 10:
            raise CollectionError('PAGE_LIMIT', '페이지 크기는 1~100, 최대 페이지는 1~10이어야 합니다.')
        seen = set()
        for page in range(1, max_pages + 1):
            payload = self.transport.get(BASE_URL + endpoint, {
                'page': page, 'perPage': per_page, 'returnType': 'JSON', **conditions,
            }, authorization=self.key)
            if 'code' in payload or 'error' in payload or 'response' in payload:
                raise CollectionError('API_ERROR', '공공데이터 API가 내부 오류를 반환했습니다.')
            try:
                rows = payload['data']
                count, total, actual_page = int(payload['currentCount']), int(payload['matchCount']), int(payload['page'])
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or count != len(rows) or count > per_page or total < 0 or actual_page != page:
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                raise CollectionError('INVALID_RESPONSE', '공공데이터 응답 구조·페이지 정보를 확인하지 못했습니다.') from None
            fingerprint = canonical_text(rows)
            if rows and fingerprint in seen:
                raise CollectionError('PAGINATION_STALLED', '공공데이터 API가 같은 페이지를 반복했습니다.')
            seen.add(fingerprint)
            yield from rows
            if page * per_page >= total:
                return
            if not rows:
                raise CollectionError('PAGINATION_STALLED', '공공데이터 API가 완료 전에 빈 페이지를 반환했습니다.')
        if page * per_page < total:
            raise CollectionError('PAGE_LIMIT', '등록한 최대 검색 범위를 초과했습니다. 검색 조건을 좁히세요.')

    def list_services(self, *, query, region, agency='', limit=100, per_page=100):
        """공식 명세에 없는 지역 조건을 보내지 않고 기관 필드를 제한된 범위에서 필터링한다."""
        if not query.strip() or region not in (*REGION_AGENCIES, '전국') or not 1 <= limit <= 1000:
            raise CollectionError('FILTER_REQUIRED', '검색어·충남/천안/아산/전국 범위와 1~1000건 상한이 필요합니다.')
        found = 0
        for row in self._pages('serviceList', {'cond[서비스명::LIKE]': query}, per_page=per_page):
            name = str(row.get('소관기관명', ''))
            if agency and agency not in name:
                continue
            if region == '전국':
                if row.get('소관기관유형') != '중앙행정기관':
                    continue
            elif REGION_AGENCIES[region] not in name:
                continue
            if not row.get('서비스ID'):
                raise CollectionError('INVALID_RESPONSE', '목록 자료에 서비스 ID가 없습니다.')
            yield row
            found += 1
            if found >= limit:
                return

    def _one(self, endpoint, external_id):
        """ID 조건 조회가 요청한 서비스 한 건만 반환했는지 확인한다."""
        rows = list(self._pages(endpoint, {'cond[서비스ID::EQ]': external_id}))
        if len(rows) != 1 or str(rows[0].get('서비스ID', '')) != external_id:
            raise CollectionError('INVALID_RESPONSE', '요청한 공공서비스 상세 자료가 없거나 ID가 일치하지 않습니다.')
        return rows[0]

    def detail(self, external_id):
        """요청한 공공서비스의 상세 원문을 조회한다."""
        return self._one('serviceDetail', external_id)

    def support_conditions(self, external_id):
        """빈 지원조건은 허용하되 다른 서비스의 조건이 섞인 응답은 거부한다."""
        rows = list(self._pages('supportConditions', {'cond[서비스ID::EQ]': external_id}))
        if any(str(row.get('서비스ID', '')) != external_id for row in rows):
            raise CollectionError('INVALID_RESPONSE', '지원조건의 서비스 ID가 일치하지 않습니다.')
        return rows

    def fetch(self, target):
        """목록·상세·지원조건을 결합하고 통계값을 제외한 정책 원문을 정규화한다."""
        external_id = target.external_id
        listing = self._one('serviceList', external_id)
        detail = self.detail(external_id)
        conditions = self.support_conditions(external_id)
        # 수정일은 공식 발행일로 간주하지 않으며 조회수는 변경 감지 대상에서 제외한다.
        detail_body = {key: value for key, value in detail.items() if key not in {'수정일시', '조회수'}}
        body = canonical_text({'상세정보': detail_body, '지원조건': conditions})
        link = optional_official_url(listing.get('상세조회URL'), 'public_data')
        return OfficialRecord(
            provider='public_data', external_id=external_id, source_url=link,
            title=detail.get('서비스명', ''), agency=detail.get('소관기관명', ''),
            region='확인 필요', category='공공서비스 지원정책', content_text=body,
            metadata={
                'collection_region': target.configuration.get('collection_region', '확인 필요'),
                'application_region_verified': False,
                'source_link_available': bool(link),
                'institution_code': listing.get('소관기관코드'),
            },
        ).validate()
