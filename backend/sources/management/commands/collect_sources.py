from django.core.management.base import BaseCommand, CommandError

from sources.services.collector import Collector
from sources.services.errors import CollectionError
from sources.services.source_registry import load_registry, validate_source_url
from sources.services.source_service import collect_source


class Command(BaseCommand):
    help = '검증된 공식 HTML URL 목록만 수집하고 원문·확인 이력을 보존합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, help='실제로 처리할 활성 URL의 최대 개수')
        parser.add_argument('--url', help='목록에 등록된 URL 하나만 처리')
        parser.add_argument('--dry-run', action='store_true', help='수집·비교만 수행하고 자료·실행 이력을 쓰지 않음')

    def handle(self, *args, **options):
        if options['limit'] is not None and options['limit'] < 1:
            raise CommandError('--limit은 1 이상이어야 합니다.')
        try:
            specs = load_registry()
            if options['url']:
                url = validate_source_url(options['url'])
                specs = [spec for spec in specs if spec.url == url]
                if not specs:
                    raise CommandError('목록에 등록되지 않은 URL은 수집할 수 없습니다.')
        except CollectionError as error:
            raise CommandError(str(error)) from None
        counts = dict.fromkeys(('new', 'updated', 'unchanged', 'failed', 'skipped'), 0)
        processed = 0
        collector = Collector()
        try:
            for spec in specs:
                if not spec.enabled or (options['limit'] is not None and processed >= options['limit']):
                    counts['skipped'] += 1
                    continue
                result = collect_source(spec, collector, dry_run=options['dry_run'])
                processed += 1
                counts[result.outcome] += 1
                self.stdout.write(f'[{result.outcome}] {spec.title}: {result.characters}자 {result.message}')
        finally:
            collector.close()
        label = '저장 없는 검증' if options['dry_run'] else '수집 결과'
        self.stdout.write(
            f'{label}: 전체={len(specs)} 신규={counts["new"]} 갱신={counts["updated"]} '
            f'동일={counts["unchanged"]} 실패={counts["failed"]} 건너뜀={counts["skipped"]}'
        )
        if counts['failed']:
            raise CommandError('일부 수집 실패' if counts['failed'] < processed else '전체 수집 실패')
