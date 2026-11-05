import hashlib
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from sources.models import CrawlLog, SourceDocument

from .errors import CollectionError
from .parser import parse_html
from .source_registry import validate_source_url


@dataclass(frozen=True)
class CollectionResult:
    outcome: str
    message: str = ''
    title: str = ''
    content_hash: str = ''
    characters: int = 0


def _store(spec, page, digest, checked_at, started_at):
    metadata = {
        'title': spec.title, 'agency': spec.agency, 'region': spec.region,
        'category': spec.category, 'published_at': spec.published_at,
    }
    # 행 잠금 + URL UNIQUE: 동시 최초 생성은 get_or_create 내부 savepoint로 처리한다.
    with transaction.atomic():
        document, created = SourceDocument.objects.select_for_update().get_or_create(
            source_url=spec.url,
            defaults={
                **metadata, 'content_text': page.content_text, 'content_hash': digest,
                'collected_at': checked_at, 'status': SourceDocument.Status.ACTIVE,
            },
        )
        if created:
            return 'new'
        # 행 잠금을 얻은 뒤 요청 시작 순서를 비교한다. 완료 순서로 원문이 역행하지 않게 한다.
        if CrawlLog.objects.filter(
            source_url=spec.url, result=CrawlLog.Result.SUCCESS, started_at__gt=started_at,
        ).exists():
            return 'skipped'
        changed = document.content_hash != digest or document.content_text != page.content_text
        fields = ['collected_at', *metadata]
        for name, value in metadata.items():
            setattr(document, name, value)
        document.collected_at = checked_at
        if changed:
            document.content_text, document.content_hash = page.content_text, digest
            fields += ['content_text', 'content_hash']
        if document.status == SourceDocument.Status.PENDING:
            document.status = SourceDocument.Status.ACTIVE
            fields.append('status')
        # 관리자가 사용 중지한 INACTIVE는 수집만으로 활성화하지 않는다.
        document.save(update_fields=fields)
        return 'updated' if changed else 'unchanged'


def collect_source(spec, collector, *, dry_run=False):
    if not spec.enabled:
        return CollectionResult('skipped', '비활성화된 자료입니다.')
    # 목록 밖 직접 호출에도 동일한 URL 경계를 적용한다. 예외에 URL 원문을 포함하지 않는다.
    validate_source_url(spec.url)
    # P3-17에 등록한 자료는 이전 명령으로 실행해도 승인 본문을 직접 교체하지 않는다.
    from sources.models import SourceRefreshTarget
    from .refresh_service import refresh_target
    target = SourceRefreshTarget.objects.filter(provider='html', source_url=spec.url).first()
    if target is not None:
        result = refresh_target(target, collector, dry_run=dry_run)
        return CollectionResult(
            'updated' if result.outcome == 'candidate' else result.outcome,
            result.message, result.title, result.content_hash, result.characters,
        )
    log = None
    if not dry_run:
        try:
            log = CrawlLog.objects.create(source_url=spec.url)
        except DatabaseError:
            return CollectionResult('failed', 'LOG_DB_FAILED: 실행 이력을 생성하지 못해 요청하지 않았습니다.')
    try:
        response = collector.fetch(spec.url)
        page = parse_html(response.body, response.content_type, response.url)
        checked_at = timezone.now()
        digest = hashlib.sha256(page.content_text.encode('utf-8')).hexdigest()
        if dry_run:
            document = SourceDocument.objects.filter(source_url=spec.url).first()
            outcome = 'new' if document is None else (
                'unchanged' if document.content_hash == digest and document.content_text == page.content_text else 'updated'
            )
        else:
            # 자료와 SUCCESS 이력을 함께 확정한다. 실패 이력은 이 atomic 밖에서 기록한다.
            with transaction.atomic():
                outcome = _store(spec, page, digest, checked_at, log.started_at)
                CrawlLog.objects.filter(pk=log.pk).update(
                    result=CrawlLog.Result.SUCCESS, finished_at=timezone.now(),
                )
        mismatch = f'HTML 제목 확인 필요: {page.title}' if page.title and page.title != spec.title else ''
        return CollectionResult(outcome, mismatch, page.title, digest, len(page.content_text))
    except CollectionError as error:
        message = str(error)
    except (DatabaseError, ValidationError):
        message = 'DB_FAILED: 자료 저장 또는 검증에 실패했습니다.'
    except Exception:
        # 원본 예외 문자열에는 응답·토큰이 있을 수 있으므로 출력·저장하지 않는다.
        message = 'UNEXPECTED_FAILED: 예상하지 못한 수집 오류입니다. 서버 코드를 점검하세요.'
    if log is not None:
        try:
            CrawlLog.objects.filter(pk=log.pk).update(
                result=CrawlLog.Result.FAILED, finished_at=timezone.now(), error_message=message,
            )
        except DatabaseError:
            message += ' LOG_DB_FAILED: 종료 이력을 저장하지 못했습니다. RUNNING 이력을 확인하세요.'
    return CollectionResult('failed', message)
