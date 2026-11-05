import unicodedata

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from sources.models import Agency, AgencyAlias
from .agency_helpers import create_agency


class AgencyModelTests(TestCase):
    def test_fields_and_string(self):
        agency = create_agency(department='가상부서')
        agency.full_clean()
        agency.refresh_from_db()
        self.assertEqual(str(agency), '가상시청 / 가상부서 (아산시)')
        self.assertEqual(agency.phone, '000-000-0000')
        self.assertEqual(agency.source_url, 'https://www.asan.go.kr/test')
        self.assertIsNotNone(agency.created_at)
        self.assertIsNotNone(agency.updated_at)

    def test_required_identity_fields(self):
        for field in ('name', 'region'):
            with self.subTest(field=field):
                agency = Agency(name='가상시청', region='아산시')
                setattr(agency, field, '  ')
                with self.assertRaises(ValidationError):
                    agency.full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    agency.save()

    def test_optional_contact_and_checked_time_have_no_default(self):
        agency = Agency.objects.create(name='미확인가상기관', region='전국')
        agency.full_clean()
        self.assertIsNone(agency.phone)
        self.assertIsNone(agency.source_url)
        self.assertIsNone(agency.checked_at)
        self.assertEqual(agency.phone_type, Agency.PhoneType.UNKNOWN)

    def test_normalized_identity_unique_in_database(self):
        create_agency(name='가상 시청', department='가상 부서')
        duplicate = Agency(name='  가상　 시청 ', department='가상  부서', region=' 아산시 ')
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_other_region_and_department_allowed(self):
        create_agency()
        create_agency(region='천안시')
        create_agency(department='가상부서')
        self.assertEqual(Agency.objects.count(), 3)

    def test_unicode_and_bulk_writes_use_same_normalization(self):
        name = unicodedata.normalize('NFD', '가상시청')
        Agency.objects.bulk_create([Agency(name=name, region=' 아산시 ')])
        self.assertEqual(Agency.objects.get().name, '가상시청')
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_agency()
        Agency.objects.all().update(name='  새　기관  ')
        self.assertEqual(Agency.objects.get().name, '새 기관')

    def test_alias_duplicate_scoped_to_agency(self):
        first, second = create_agency(), create_agency(region='천안시')
        AgencyAlias.objects.create(agency=first, name=' 가상  본청 ')
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgencyAlias.objects.create(agency=first, name='가상 본청')
        alias = AgencyAlias.objects.create(agency=second, name='가상 본청')
        self.assertIn(str(second), str(alias))

    def test_contact_requires_official_source_and_real_checked_time(self):
        for values in ({'source_url': None}, {'checked_at': None},
                       {'source_url': 'https://example.com/contact'},
                       {'source_url': 'https://www.asan.go.kr@evil.example/contact'}):
            with self.subTest(values=values):
                agency = create_agency(**values)
                with self.assertRaises(ValidationError):
                    agency.full_clean()
                agency.delete()

    def test_normalized_url_and_international_phone_preserved(self):
        agency = create_agency(phone='+82-2-0000-0000', source_url='HTTPS://WWW.ASAN.GO.KR:443/test#part')
        agency.full_clean()
        self.assertEqual(agency.source_url, 'https://www.asan.go.kr/test')
        self.assertEqual(agency.phone, '+82-2-0000-0000')

    def test_invalid_checked_date_is_a_validation_error(self):
        agency = Agency(name='가상시청', region='아산시', source_url='https://www.asan.go.kr/test', checked_at='잘못된 날짜')
        with self.assertRaises(ValidationError) as error:
            agency.full_clean()
        self.assertIn('checked_at', error.exception.message_dict)
