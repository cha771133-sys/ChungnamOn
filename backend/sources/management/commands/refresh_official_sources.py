from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from sources.models import CrawlLog, SourceDocument, SourceRefreshTarget
from sources.services.collector import Collector
from sources.services.errors import CollectionError
from sources.services.freshness import is_due, next_due_at
from sources.services.normalization import official_url
from sources.services.providers.law_api import LawAPIClient
from sources.services.providers.public_data import PublicDataClient
from sources.services.refresh_service import refresh_target
from sources.services.source_registry import load_registry


CLIENTS = {'html': Collector, 'public_data': PublicDataClient, 'law': LawAPIClient}


class UnavailableClient:
    """인증 설정 오류도 자료별 공통 실패·재시도 이력으로 기록한다."""
    def __init__(self, error):
        self.error = error

    def fetch(self, target):
        raise self.error

    def close(self):
        pass


def html_targets(*, dry_run):
    """기존 고정 목록을 등록하되 운영자가 중지한 대상을 자동으로 재활성화하지 않는다."""
    targets = []
    disabled = set()
    for spec in load_registry():
        if not spec.enabled:
            disabled.add(spec.url)
            continue
        configuration = {
            'title': spec.title, 'agency': spec.agency, 'region': spec.region,
            'category': spec.category, 'published_at': spec.published_at.isoformat() if spec.published_at else None,
        }
        document = SourceDocument.objects.filter(source_url=spec.url).first()
        defaults = {'configuration': configuration, 'document': document}
        if dry_run:
            target = SourceRefreshTarget.objects.filter(provider='html', source_url=spec.url).first()
            target = target or SourceRefreshTarget(provider='html', source_url=spec.url, **defaults)
        else:
            target, _ = SourceRefreshTarget.objects.get_or_create(provider='html', source_url=spec.url, defaults=defaults)
        targets.append(target)
    # Admin에서 별도 등록한 허용 공식 HTML 대상도 처리한다.
    known = {target.source_url for target in targets} | disabled
    targets.extend(SourceRefreshTarget.objects.filter(provider='html').exclude(source_url__in=known).select_related('document'))
    return targets


class Command(BaseCommand):
    help = '공식자료를 재확인하고 변경 후보를 저장합니다. 정기 자동 호출은 설정하지 않습니다.'

    def add_arguments(self, parser):
        parser.add_argument('--provider', choices=['html', 'public_data', 'law', 'all'], default='all')
        parser.add_argument('--due-only', action='store_true', help='유형 주기 또는 실패 재시도 시각에 도달한 대상만 처리')
        parser.add_argument('--dry-run', action='store_true', help='실제 외부 호출·파싱은 수행하며 DB·로그·검토·AI 상태는 변경하지 않음')
        parser.add_argument('--limit', type=int, default=20, help='최대 시도 자료 수, 기본 20 / 상한 1000')
        parser.add_argument('--discover', action='store_true', help='좁은 검색 조건으로 API 자료를 찾아 등록·초기 수집')
        parser.add_argument('--query', default='', help='신규 API 검색어, discover 사용 시 필수')
        parser.add_argument('--region', choices=['충청남도', '천안시', '아산시', '전국'], default='충청남도')
        parser.add_argument('--agency', default='', help='공공데이터 목록의 소관기관명 필터')

    def discover(self, provider, client, options):
        rows = client.list_services(query=options['query'], region=options['region'], agency=options['agency'], limit=options['limit']) if provider == 'public_data' else client.list_ordinances(query=options['query'], region=options['region'], limit=options['limit'])
        seen = set()
        for row in rows:
            external_id = str(row.get('서비스ID') if provider == 'public_data' else row.get('자치법규ID', ''))
            if not external_id or len(external_id) > 100:
                raise CollectionError('INVALID_RESPONSE', '공식 목록의 외부 ID가 올바르지 않습니다.')
            if external_id in seen:
                continue
            seen.add(external_id)
            raw_link = row.get('상세조회URL') if provider == 'public_data' else row.get('자치법규상세링크')
            link = official_url(raw_link, provider)
            defaults = {'source_url': link, 'configuration': {'collection_region': options['region'], 'history': False}}
            target = SourceRefreshTarget.objects.filter(provider=provider, external_id=external_id).first()
            if target is None:
                target = SourceRefreshTarget(provider=provider, external_id=external_id, **defaults)
                target.full_clean()
                if not options['dry_run']:
                    target, _ = SourceRefreshTarget.objects.get_or_create(provider=provider, external_id=external_id, defaults=defaults)
            yield target

    def handle(self, *args, **options):
        limit = options['limit']
        provider = options['provider']
        if not 1 <= limit <= 1000:
            raise CommandError('--limit은 1~1000이어야 합니다.')
        if options['discover'] and (provider not in ('law', 'public_data') or not options['query'].strip() or (provider == 'law' and options['region'] == '전국')):
            raise CommandError('--discover는 API 제공자 하나와 검색어, 허용 지역이 필요합니다.')
        self.stdout.write('정기 자동 실행 트리거: 연결하지 않음. 이 명령은 호출 시 한 번 실행합니다.')
        if options['dry_run']:
            self.stdout.write('dry-run: 실제 외부 API/HTML 호출·파싱을 수행하며 DB 및 CrawlLog는 쓰지 않습니다.')
        counts = dict.fromkeys(('new', 'candidate', 'unchanged', 'failed', 'skipped'), 0)
        attempted = provider_errors = 0
        selected = CLIENTS if provider == 'all' else [provider]
        try:
            for name in selected:
                client = None
                try:
                    if attempted >= limit:
                        break
                    targets = None if options['discover'] else (html_targets(dry_run=options['dry_run']) if name == 'html' else list(SourceRefreshTarget.objects.filter(provider=name).select_related('document')))
                    # 등록 대상이 없는 API에는 인증정보가 필요하지 않다.
                    if targets == []:
                        continue
                    if targets is not None:
                        targets = [target for target in targets if target.enabled and (not options['due_only'] or is_due(target))]
                        if not targets:
                            continue
                    try:
                        client = CLIENTS[name]()
                    except CollectionError as error:
                        if targets is None:
                            raise
                        provider_errors += 1
                        client = UnavailableClient(error)
                    if options['discover']:
                        targets = self.discover(name, client, options)
                    for target in targets:
                        if not target.enabled or (options['due_only'] and not is_due(target)) or attempted >= limit:
                            counts['skipped'] += 1
                            continue
                        attempted += 1
                        result = refresh_target(target, client, dry_run=options['dry_run'])
                        counts[result.outcome] += 1
                        target_label = target.external_id or target.source_url
                        if target.pk and not options['dry_run']:
                            target.refresh_from_db()
                        self.stdout.write(f'[{result.outcome}] {name} {target_label}: {result.characters}자 {result.message} 다음 확인={next_due_at(target)}')
                except CollectionError as error:
                    provider_errors += 1
                    counts['failed'] += 1
                    self.stderr.write(f'[{name}] {error}')
                    if not options['dry_run']:
                        # 인증 누락·목록 검색 실패도 키가 없는 고정 공식 주소로 기록한다.
                        finished = timezone.now()
                        CrawlLog.objects.create(
                            source_url={'html': 'https://www.gov.kr/', 'law': 'https://www.law.go.kr/DRF/lawSearch.do', 'public_data': 'https://api.odcloud.kr/api/gov24/v3/serviceList'}[name],
                            provider=name, result='FAILED', started_at=finished, finished_at=finished,
                            error_code=error.code, error_message=str(error), outcome='failed',
                        )
                finally:
                    if client is not None:
                        client.close()
            self.stdout.write('실행 결과: ' + ' '.join(f'{key}={value}' for key, value in counts.items()) + f' 시도={attempted} 제공자오류={provider_errors}')
            if counts['failed']:
                successes = counts['new'] + counts['candidate'] + counts['unchanged']
                raise CommandError('부분 수집 실패' if successes else '전체 수집 실패')
        finally:
            connections.close_all()
