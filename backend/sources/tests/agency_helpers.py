from datetime import datetime, timezone

from sources.models import Agency


CHECKED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def create_agency(**overrides):
    values = dict(name='가상시청', department='', region='아산시', phone='000-000-0000',
                  source_url='https://www.asan.go.kr/test', checked_at=CHECKED,
                  phone_type=Agency.PhoneType.REPRESENTATIVE)
    return Agency.objects.create(**{**values, **overrides})


def seed_entry(**overrides):
    values = dict(name='가상시청', department='', region='아산시', phone='000-000-0000',
                  phone_type='REPRESENTATIVE', source_url='https://www.asan.go.kr/test',
                  checked_at=CHECKED.isoformat(), aliases=['가상시청 본청'])
    return {**values, **overrides}
