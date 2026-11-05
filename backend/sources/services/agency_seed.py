from datetime import datetime
from pathlib import Path

import yaml
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from sources.agency_fields import normalize_agency_text, validate_lookup_text
from sources.models import Agency, AgencyAlias
from sources.services.source_registry import REGIONS, UniqueKeyLoader


DEFAULT_AGENCIES = Path(__file__).resolve().parent.parent / 'data' / 'agencies.yaml'


def load_agencies(path=DEFAULT_AGENCIES):
    """모든 시드 항목을 검증한 뒤 반환한다. 네트워크·DB 접근은 없다."""
    try:
        raw = Path(path).read_text(encoding='utf-8')
        if len(raw) > 1_000_000:
            raise ValueError
        data = yaml.load(raw, Loader=UniqueKeyLoader)
        if not isinstance(data, dict) or set(data) != {'agencies'} or not isinstance(data['agencies'], list):
            raise ValueError
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError):
        raise ValidationError('기관 시드 YAML 형식을 확인해 주세요.') from None
    result, seen = [], set()
    required = {'name', 'department', 'region', 'phone', 'source_url', 'checked_at', 'aliases'}
    for index, entry in enumerate(data['agencies'], 1):
        try:
            if not isinstance(entry, dict) or not required <= set(entry) or set(entry) - required - {'phone_type'}:
                raise ValidationError('필수 필드 또는 알 수 없는 필드를 확인해 주세요.')
            values = {key: entry[key] for key in required - {'aliases'}}
            values['name'] = validate_lookup_text(values['name'])
            values['department'] = normalize_agency_text(values['department'])
            if values['department']:
                values['department'] = validate_lookup_text(values['department'])
            values['region'] = validate_lookup_text(values['region'], maximum=100)
            if values['region'] not in REGIONS:
                raise ValidationError('관할 지역 표기를 확인해 주세요.')
            for field in ('phone', 'source_url'):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ValidationError(f'{field}는 문자열 또는 null이어야 합니다.')
                values[field] = values[field].strip() or None if values[field] else None
            checked = values['checked_at']
            if isinstance(checked, str):
                checked = parse_datetime(checked)
                if checked is None:
                    raise ValidationError('checked_at은 ISO 8601 시각이어야 합니다.')
            if checked is not None and not isinstance(checked, datetime):
                raise ValidationError('checked_at은 시각 또는 null이어야 합니다.')
            values['checked_at'] = checked
            values['phone_type'] = entry.get('phone_type', Agency.PhoneType.UNKNOWN)
            agency = Agency(**values)
            agency.full_clean(validate_unique=False, validate_constraints=False)
            # full_clean이 URL·텍스트를 정규화한 값을 사용한다.
            values = {key: getattr(agency, key) for key in values}
            identity = (agency.name, agency.department, agency.region)
            if identity in seen:
                raise ValidationError('정규화 후 중복된 기관·부서·지역입니다.')
            seen.add(identity)
            aliases = entry['aliases']
            if not isinstance(aliases, list):
                raise ValidationError('aliases는 문자열 목록이어야 합니다.')
            aliases = [validate_lookup_text(alias) for alias in aliases]
            if len(set(aliases)) != len(aliases) or agency.name in aliases:
                raise ValidationError('중복된 별칭 또는 공식명과 같은 별칭입니다.')
            result.append((values, aliases))
        except (ValidationError, ValueError, TypeError, OverflowError) as error:
            message = '; '.join(error.messages) if isinstance(error, ValidationError) else '필드 형식을 확인해 주세요.'
            raise ValidationError(f'{index}번 기관: {message}') from None
    return result


def _changes(agency, values, aliases):
    """동일 확인일의 충돌도 덮어쓰지 않는다. 번호가 비면 출처·확인일 묶음을 보존한다."""
    current, incoming = agency.checked_at, values['checked_at']
    newer = incoming is not None and (current is None or incoming > current)
    fields = []
    if newer and (values['phone'] or not agency.phone):
        for field in ('phone', 'phone_type', 'source_url', 'checked_at'):
            if getattr(agency, field) != values[field]:
                setattr(agency, field, values[field])
                fields.append(field)
    # 오래되거나 미확인인 시드로 별칭을 추가하여 매칭을 바꾸지 않는다.
    trusted = newer or (incoming is not None and incoming == current and all(
        getattr(agency, field) == values[field] for field in ('phone', 'phone_type', 'source_url')
    ))
    existing = set(agency.aliases.values_list('name', flat=True))
    additions = set(aliases) - existing if trusted else set()
    return fields, sorted(additions)


@transaction.atomic
def seed_agencies(specs, *, dry_run=False):
    """목록 전체를 원자적으로 반영한다. 삭제·네트워크 요청·자동 확인일 갱신은 없다."""
    counts = dict.fromkeys(('created', 'updated', 'unchanged'), 0)
    for values, aliases in specs:
        identity = {key: values[key] for key in ('name', 'department', 'region')}
        agency = Agency.objects.select_for_update().filter(**identity).first()
        if agency is None:
            if not dry_run:
                agency, created = Agency.objects.select_for_update().get_or_create(**identity, defaults=values)
            else:
                created = True
            if created:
                if not dry_run:
                    for alias in aliases:
                        AgencyAlias.objects.create(agency=agency, name=alias)
                counts['created'] += 1
                continue
        fields, additions = _changes(agency, values, aliases)
        if fields or additions:
            if not dry_run:
                if fields:
                    agency.full_clean()
                    agency.save(update_fields=[*fields, 'updated_at'])
                for alias in additions:
                    AgencyAlias.objects.get_or_create(agency=agency, name=alias)
            counts['updated'] += 1
        else:
            counts['unchanged'] += 1
    return counts
