from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = '마감 알림 발송 (P2-11 Schedule 미구현으로 현재 실행 차단)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='실제 발송·DB 변경 없이 확인')
        parser.add_argument('--limit', type=int, help='최대 이벤트 수 (양의 정수)')

    def handle(self, *args, **options):
        try:
            if options['limit'] is not None and options['limit'] <= 0:
                raise CommandError('--limit는 양의 정수여야 합니다.')
            # 모형 Schedule이나 가짜 0건 성공으로 의존성 차단을 숨기지 않는다.
            raise CommandError(
                'SCHEDULE_INTEGRATION_UNAVAILABLE: P2-11 Schedule 계약 및 알림 이벤트·발송 이력 '
                '연동이 필요합니다. 발송/대상 집계/DB 변경을 수행하지 않았습니다.'
            )
        finally:
            connections.close_all()
