from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models

from sources.agency_fields import validate_lookup_text
from sources.models import Agency


class LookupStatus(models.TextChoices):
    FOUND = 'FOUND', '기관 식별'
    AMBIGUOUS = 'AMBIGUOUS', '중복 후보'
    UNVERIFIED = 'UNVERIFIED', '미확인'


class ApplicabilityStatus(models.TextChoices):
    CONFIRMED = 'CONFIRMED', '확인됨'
    CONDITIONAL = 'CONDITIONAL', '조건부'
    NEEDS_CONFIRMATION = 'NEEDS_CONFIRMATION', '확인 필요'
    MISMATCH = 'MISMATCH', '불일치'


@dataclass(frozen=True)
class AgencyLookupResult:
    lookup_status: str
    agency: Agency | None = None
    candidates: tuple[Agency, ...] = ()
    applicability_status: str = ApplicabilityStatus.NEEDS_CONFIRMATION
    message: str = ''


def lookup_agency(name: str, region: str | None = None) -> AgencyLookupResult:
    """발신기관 정확 일치 조회. 개인 적용·최종 관할 판정 및 DB 쓰기는 수행하지 않는다."""
    try:
        name = validate_lookup_text(name)
    except ValidationError as error:
        raise ValidationError({'name': error.messages}) from None
    if region is not None:
        try:
            region = validate_lookup_text(region, maximum=100)
        except ValidationError as error:
            raise ValidationError({'region': error.messages}) from None

    # 공식명이 다른 지역에서만 발견되어도 별칭으로 다른 기관을 확정하지 않는다.
    candidates = Agency.objects.filter(name=name)
    if not candidates.exists():
        candidates = Agency.objects.filter(aliases__name=name).distinct()
    if region is not None:
        candidates = candidates.filter(region=region)
    candidates = tuple(candidates)
    if not candidates:
        return AgencyLookupResult(
            LookupStatus.UNVERIFIED,
            message='담당기관 정보를 확인할 수 없습니다. 문서 발신기관에 직접 확인해 주세요.',
        )
    if len(candidates) > 1:
        return AgencyLookupResult(
            LookupStatus.AMBIGUOUS, candidates=candidates,
            message='여러 담당기관 후보가 있습니다. 지역·담당 부서를 추가로 확인해 주세요.',
        )
    return AgencyLookupResult(
        LookupStatus.FOUND, agency=candidates[0],
        message='연락처·담당 부서는 표시된 확인일 기준입니다. 최신 정보와 개인별 적용 여부는 담당기관에 확인해야 합니다.',
    )
