from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from sources.services.errors import CollectionError
from sources.services.manual_import import TERMS_STATUSES, import_official_txt
from sources.services.source_registry import CATEGORIES, REGIONS


class Command(BaseCommand):
    help = '공식 UTF-8 TXT를 외부 요청 없이 검토 후보로 등록합니다. 승인·AI 재처리는 실행하지 않습니다.'

    def add_arguments(self, parser):
        for name, help_text in (
            ('file', 'backend/sources/data/manual 안의 .txt 파일 경로'),
            ('url', '공식 원본 상세 HTTPS URL'), ('agency', '공식 발행기관명'),
            ('title', '공식 문서 제목'), ('registered-by', '수동 등록자 식별명'),
            ('terms-note', '원본 이용조건·AI 가공 허용 근거 또는 미확인 사유'),
        ):
            parser.add_argument(f'--{name}', required=True, help=help_text)
        parser.add_argument('--region', choices=sorted(REGIONS), default='전국')
        parser.add_argument('--category', choices=sorted(CATEGORIES), required=True)
        parser.add_argument('--published-at', type=date.fromisoformat, help='발행일·기준일 YYYY-MM-DD, 미상은 생략')
        parser.add_argument('--terms-status', choices=TERMS_STATUSES, default='unverified', help='unverified=미확인, confirmed=AI 가공 허용 확인, prohibited=가공 금지')
        parser.add_argument('--terms-url', help='확인한 공식 이용조건 HTTPS URL, 미상은 생략')
        parser.add_argument('--terms-checked-on', type=date.fromisoformat, help='이용조건 확인일 YYYY-MM-DD, confirmed이면 필수')

    def handle(self, *args, **options):
        try:
            result = import_official_txt(**{key: options[key] for key in (
                'file', 'url', 'agency', 'title', 'region', 'category', 'registered_by',
                'terms_note', 'terms_status', 'terms_url', 'terms_checked_on', 'published_at',
            )})
        except CollectionError as error:
            raise CommandError(str(error)) from None
        except (DatabaseError, ValidationError):
            raise CommandError('IMPORT_DB_FAILED: 저장 충돌 또는 검증 실패입니다. 원본은 변경하지 않았습니다. 최신 상태 확인 후 다시 실행하세요.') from None
        self.stdout.write(
            f'[{result.outcome}] document={result.document_id} revision={result.revision_id or "없음"} '
            f'sha256={result.content_hash}\n'
            '자동 승인·AI 실행 없음. 후보가 있으면 공식자료 변경 후보에서 원문 대조와 이용조건을 검토하세요.'
        )
