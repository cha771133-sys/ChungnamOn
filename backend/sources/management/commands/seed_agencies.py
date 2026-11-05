from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from sources.services.agency_seed import DEFAULT_AGENCIES, load_agencies, seed_agencies


class Command(BaseCommand):
    help = '공식 출처를 확인한 담당기관 시드를 중복 없이 등록합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--file', default=str(DEFAULT_AGENCIES), help='검토한 기관 YAML 파일 경로')
        parser.add_argument('--dry-run', action='store_true', help='DB 쓰기 없이 예정 변경 집계')

    def handle(self, *args, **options):
        try:
            counts = seed_agencies(load_agencies(options['file']), dry_run=options['dry_run'])
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from None
        except DatabaseError:
            raise CommandError('기관 저장에 실패하여 이번 실행의 변경을 모두 취소했습니다.') from None
        label = '저장 없는 검증' if options['dry_run'] else '기관 등록 결과'
        self.stdout.write(f'{label}: 신규={counts["created"]} 갱신={counts["updated"]} 동일={counts["unchanged"]}')
