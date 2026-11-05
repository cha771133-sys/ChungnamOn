import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from sources.models import CrawlLog, SourceDocument, SourceRefreshTarget, SourceRevision

from .errors import CollectionError
from .normalization import OfficialRecord, official_date, official_url
from .parser import parse_html
from .source_service import CollectionResult


LEASE_DURATION = timedelta(minutes=30)


def document_snapshot(document):
    """승인된 출처 메타데이터를 후보와 비교할 수 있는 날짜 문자열로 직렬화한다."""
    values = {name: getattr(document, name) for name in ('source_url', 'title', 'agency', 'region', 'category', 'published_at', 'effective_at', 'metadata')}
    for name in ('published_at', 'effective_at'):
        values[name] = values[name].isoformat() if values[name] else None
    return values


def log_url(target):
    """공개 상세링크가 없으면 인증정보 없는 고정 API 주소를 실행 이력에 사용한다."""
    return official_url(target.source_url, target.provider) or {
        'public_data': 'https://api.odcloud.kr/api/gov24/v3/serviceDetail',
        'law': 'https://www.law.go.kr/DRF/lawService.do',
    }[target.provider]


def fetch_record(target, client):
    """HTML은 기존 파서를 재사용하고 API는 제공자 정규화 계약을 검증한다."""
    if target.provider != 'html':
        return client.fetch(target).validate()
    response = client.fetch(target.source_url)
    page = parse_html(response.body, response.content_type, response.url)
    configuration = target.configuration
    return OfficialRecord(
        provider='html', external_id='', source_url=target.source_url,
        title=configuration.get('title', ''), agency=configuration.get('agency', ''),
        region=configuration.get('region', ''), category=configuration.get('category', ''),
        published_at=official_date(configuration.get('published_at')),
        content_text=page.content_text,
    ).validate()


def claim(target):
    """짧은 트랜잭션에서 실행 소유권을 얻고 만료된 작업 이력을 실패로 마감한다."""
    now, token = timezone.now(), uuid.uuid4()
    with transaction.atomic():
        claimed = SourceRefreshTarget.objects.filter(pk=target.pk, enabled=True).filter(
            Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now),
        ).update(lease_token=token, lease_expires_at=now + LEASE_DURATION, last_attempt_at=now)
        if not claimed:
            return None, None
        CrawlLog.objects.filter(target=target, result='RUNNING').update(
            result='FAILED', finished_at=now, error_code='LEASE_EXPIRED',
            error_message='LEASE_EXPIRED: 이전 작업의 잠금이 만료되어 안전하게 재시도합니다.',
        )
        log = CrawlLog.objects.create(target=target, source_url=log_url(target), provider=target.provider, external_id=target.external_id, started_at=now)
    return token, log


def _lookup(target):
    """HTML은 정규화 URL, API는 제공자·외부 ID로 기존 자료를 식별한다."""
    if target.provider == 'html':
        return {'source_url': target.source_url}
    return {'provider': target.provider, 'external_id': target.external_id}


def _apply_observation(target, record, started_at, checked_at):
    """호출자는 짧은 저장 트랜잭션과 대상 행의 쓰기 잠금을 확보해야 한다."""
    identity = _lookup(target)
    metadata = record.snapshot()
    defaults = {**metadata, 'provider': record.provider, 'external_id': record.external_id}
    defaults['published_at'] = record.published_at
    defaults['effective_at'] = record.effective_at
    document, created = SourceDocument.objects.select_for_update().get_or_create(**identity, defaults=defaults)
    if target.document_id and target.document_id != document.pk:
        raise CollectionError('IDENTITY_CONFLICT', '등록 대상과 공식자료 식별자가 충돌합니다.')
    if document.provider != record.provider or document.external_id != record.external_id:
        raise CollectionError('IDENTITY_CONFLICT', '기존 출처 URL과 제공자 식별자가 충돌합니다.')
    if document.observed_started_at and document.observed_started_at > started_at:
        return 'skipped', document
    digest = record.content_hash
    unchanged = document.content_hash == digest and document.content_text == record.content_text and document_snapshot(document) == metadata
    needs_review = not unchanged or document.review_status != 'APPROVED'
    if needs_review:
        revision, revision_created = SourceRevision.objects.get_or_create(
            document=document, content_hash=digest, base_version=document.version,
            review_status='PENDING_REVIEW',
            defaults={
                'previous_hash': document.content_hash, 'previous_content_text': document.content_text,
                'previous_metadata': document_snapshot(document), 'metadata': metadata,
                'content_text': record.content_text, 'provider': record.provider, 'collected_at': checked_at,
            },
        )
        if not revision_created:
            # 본문은 같아도 공식 링크·공포일 등 메타데이터가 달라졌다면 최신 후보에 보존한다.
            revision.metadata, revision.collected_at = metadata, checked_at
            revision.save(update_fields=['metadata', 'collected_at'])
        outcome = 'new' if created else ('candidate' if revision_created else 'unchanged')
    else:
        # 승인 자료의 동일 본문을 실제로 다시 확인한 경우에만 그 본문 확인일도 갱신한다.
        document.collected_at = checked_at
        outcome = 'unchanged'
    document.last_success_at = checked_at
    document.observed_started_at, document.observed_hash = started_at, digest
    document.save(update_fields=['collected_at', 'last_success_at', 'observed_started_at', 'observed_hash', 'updated_at'])
    return outcome, document


def refresh_target(target, client, *, dry_run=False):
    """잠금 없이 외부 요청 후 소유권을 재검증해 후보·성공 이력을 원자적으로 저장한다.

    실패는 승인 본문과 성공 확인일을 보존하며 dry-run은 실행 이력도 쓰지 않는다.
    """
    if not target.enabled:
        return CollectionResult('skipped')
    token = log = None
    try:
        target.full_clean()
        if not dry_run:
            token, log = claim(target)
            if token is None:
                return CollectionResult('skipped', '같은 자료가 이미 실행 중이거나 비활성화되었습니다.')
        record = fetch_record(target, client)
        if record.provider != target.provider or record.external_id != target.external_id:
            raise CollectionError('IDENTITY_CONFLICT', '요청한 자료와 응답 식별자가 다릅니다.')
        checked_at = timezone.now()
        if dry_run:
            document = SourceDocument.objects.filter(**_lookup(target)).first()
            outcome = 'new' if document is None else ('unchanged' if document.content_hash == record.content_hash and document_snapshot(document) == record.snapshot() else 'candidate')
        else:
            with transaction.atomic():
                # SQLite에서도 SELECT FOR UPDATE만 의존하지 않고 조건부 UPDATE로 쓰기를 직렬화한다.
                owned = SourceRefreshTarget.objects.filter(pk=target.pk, lease_token=token, lease_expires_at__gt=checked_at, enabled=True).update(lease_expires_at=checked_at + LEASE_DURATION)
                if not owned:
                    raise CollectionError('LEASE_LOST', '작업 잠금이 만료되거나 대상이 중지되어 결과를 적용하지 않았습니다.')
                current = SourceRefreshTarget.objects.select_for_update().get(pk=target.pk)
                outcome, document = _apply_observation(current, record, log.started_at, checked_at)
                SourceRefreshTarget.objects.filter(pk=target.pk, lease_token=token).update(
                    document=document, last_success_at=checked_at, failures=0, retry_after=None,
                    lease_token=None, lease_expires_at=None,
                )
                CrawlLog.objects.filter(pk=log.pk).update(result='SUCCESS', outcome=outcome, finished_at=timezone.now())
        return CollectionResult(outcome, title=record.title, content_hash=record.content_hash, characters=len(record.content_text))
    except CollectionError as error:
        code, message = error.code, str(error)
    except (DatabaseError, ValidationError):
        code, message = 'DB_FAILED', 'DB_FAILED: 자료 저장 또는 검증에 실패했습니다.'
    except Exception:
        code, message = 'UNEXPECTED_FAILED', 'UNEXPECTED_FAILED: 수집 처리 오류입니다. 인증정보 없는 서버 코드를 점검하세요.'
    if log is not None:
        try:
            with transaction.atomic():
                # 새 실행이 잠금을 인수했다면 이전 실행은 재시도 시각·실패 수까지 건드리지 않는다.
                owned = SourceRefreshTarget.objects.filter(pk=target.pk, lease_token=token).update(lease_expires_at=timezone.now())
                if owned:
                    current = SourceRefreshTarget.objects.get(pk=target.pk)
                    failures = current.failures + 1
                    SourceRefreshTarget.objects.filter(pk=target.pk, lease_token=token).update(
                        failures=failures, retry_after=timezone.now() + timedelta(hours=min(24, 2 ** min(failures - 1, 5))),
                        lease_token=None, lease_expires_at=None,
                    )
                CrawlLog.objects.filter(pk=log.pk, result='RUNNING').update(result='FAILED', error_code=code, error_message=message, outcome='failed', finished_at=timezone.now())
        except DatabaseError:
            message += ' LOG_DB_FAILED: 종료 기록 실패, 잠금 만료 후 재시도할 수 있습니다.'
    return CollectionResult('failed', message)


def review_revision(revision_id, user, *, approve):
    """실제 관리자 권한과 최신 관측을 확인하여 후보를 승인하거나 반려한다.

    승인 본문 변경은 기존 청크를 보존한 채 AI 재처리 대기로 전환한다.
    """
    if not user.is_active or not user.is_staff or not user.has_perm('sources.change_sourcerevision') or not user.has_perm('sources.change_sourcedocument'):
        raise CollectionError('PERMISSION_DENIED', '공식자료와 변경 후보 수정 권한이 있는 운영 관리자만 검토할 수 있습니다.')
    now = timezone.now()
    with transaction.atomic():
        reference = SourceRevision.objects.only('document_id').get(pk=revision_id)
        # 수집과 같은 순서로 문서를 잠근 후 후보를 잠가 교착을 피한다.
        # 조건부 쓰기로 SQLite에서도 최신 버전을 읽기 전에 쓰기 잠금을 확보한다.
        SourceDocument.objects.filter(pk=reference.document_id).update(updated_at=now)
        document = SourceDocument.objects.select_for_update().get(pk=reference.document_id)
        revision = SourceRevision.objects.select_for_update().get(pk=revision_id)
        if revision.review_status != 'PENDING_REVIEW':
            raise CollectionError('REVIEW_CONFLICT', '이미 검토한 후보입니다.')
        if approve:
            # 본문 해시가 같아도 출처·날짜가 원복된 관측은 이전 메타데이터 후보를 대체한다.
            # 후보 확인일은 후보를 재확인할 때 함께 갱신하므로 최신 성공 관측과 일치해야 한다.
            if document.version != revision.base_version or document.content_hash != revision.previous_hash or document.observed_hash != revision.content_hash or document.last_success_at != revision.collected_at:
                raise CollectionError('REVIEW_CONFLICT', '더 최근 자료 또는 관리자 수정이 있습니다. 최신 후보를 검토하세요.')
            needs_reprocessing = document.ai_reprocessing_required or document.review_status != 'APPROVED' or document.content_hash != revision.content_hash or document.content_text != revision.content_text
            for name, value in revision.metadata.items():
                setattr(document, name, official_date(value) if name in ('published_at', 'effective_at') else value)
            document.content_text, document.content_hash = revision.content_text, revision.content_hash
            document.collected_at = revision.collected_at
            document.review_status = 'APPROVED'
            document.reviewed_by, document.reviewed_at = user, now
            document.ai_reprocessing_required = needs_reprocessing
            document.version += 1
            # 기존 AI 검색도 ACTIVE를 사용한다. 새 승인 본문과 이전 청크가 섞이지 않도록
            # 재처리 확인 전에는 검색에서 제외하고 관리자의 사용 중지는 그대로 보존한다.
            if document.status != 'INACTIVE' and (needs_reprocessing or not document.source_url):
                document.status = 'PENDING'
            document.full_clean()
            document.save()
        elif not document.content_hash:
            document.review_status = 'REJECTED'
            document.reviewed_by, document.reviewed_at = user, now
            document.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        revision.review_status = 'APPROVED' if approve else 'REJECTED'
        revision.reviewed_by, revision.reviewed_at = user, now
        revision.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at'])
        return revision
