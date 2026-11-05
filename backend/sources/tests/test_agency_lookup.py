from datetime import datetime, timezone

from django.core.exceptions import ValidationError
from django.test import TestCase

from sources.models import AgencyAlias
from sources.services.agency_lookup import ApplicabilityStatus, lookup_agency
from .agency_helpers import create_agency


class AgencyLookupTests(TestCase):
    def test_official_name_space_and_unicode_normalization(self):
        agency = create_agency(name='가상 기관·민원')
        for name in ('가상 기관·민원', '  가상　 기관ㆍ민원  '):
            result = lookup_agency(name)
            self.assertEqual(result.lookup_status, 'FOUND')
            self.assertEqual(result.agency, agency)
            self.assertEqual(result.applicability_status, 'NEEDS_CONFIRMATION')
            self.assertFalse(result.candidates)

    def test_registered_alias_only(self):
        agency = create_agency()
        AgencyAlias.objects.create(agency=agency, name='가상시청 본청')
        self.assertEqual(lookup_agency('가상시청 본청').agency, agency)
        for name in ('가상시', '가상시텅', '시청', '가상시청 세무과'):
            self.assertEqual(lookup_agency(name).lookup_status, 'UNVERIFIED')

    def test_region_filters_without_nationwide_fallback(self):
        local = create_agency()
        create_agency(region='천안시')
        national = create_agency(region='전국')
        self.assertEqual(lookup_agency('가상시청', '아산시').agency, local)
        self.assertEqual(lookup_agency('가상시청', '전국').agency, national)
        self.assertEqual(lookup_agency('가상시청', '충청남도').lookup_status, 'UNVERIFIED')
        self.assertEqual(lookup_agency('가상시청').lookup_status, 'AMBIGUOUS')

    def test_department_candidates_are_not_arbitrarily_chosen(self):
        first = create_agency()
        second = create_agency(department='가상부서')
        result = lookup_agency('가상시청', '아산시')
        self.assertIsNone(result.agency)
        self.assertEqual(set(result.candidates), {first, second})
        self.assertEqual(result.lookup_status, 'AMBIGUOUS')

    def test_alias_collision_and_official_name_precedence(self):
        first = create_agency()
        second = create_agency(name='다른가상기관')
        for agency in (first, second):
            AgencyAlias.objects.create(agency=agency, name='가상본청')
        self.assertEqual(lookup_agency('가상본청').lookup_status, 'AMBIGUOUS')
        AgencyAlias.objects.create(agency=second, name=first.name)
        self.assertEqual(lookup_agency(first.name).agency, first)
        self.assertEqual(lookup_agency(first.name, '천안시').lookup_status, 'UNVERIFIED')

    def test_invalid_service_inputs(self):
        for name in ('', '  ', '가' * 256, '<script>', 'https://example.com', '가상\x00시청', '가상\u200b시청', 1, None):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                lookup_agency(name)
        for region in ('', '가' * 101, [], '가상\n지역'):
            with self.subTest(region=region), self.assertRaises(ValidationError):
                lookup_agency('가상기관', region)

    def test_unknown_has_no_fabricated_contact(self):
        result = lookup_agency('없는가상기관')
        self.assertEqual(result.lookup_status, 'UNVERIFIED')
        self.assertIsNone(result.agency)
        self.assertEqual(result.applicability_status, 'NEEDS_CONFIRMATION')

    def test_old_and_missing_information_preserved(self):
        checked = datetime(2000, 1, 1, tzinfo=timezone.utc)
        agency = create_agency(phone=None, phone_type='UNKNOWN', checked_at=checked)
        result = lookup_agency(agency.name)
        self.assertIsNone(result.agency.phone)
        self.assertEqual(result.agency.checked_at, checked)
        self.assertEqual(result.agency.source_url, agency.source_url)
        self.assertIn('확인일 기준', result.message)
        self.assertEqual(result.applicability_status, 'NEEDS_CONFIRMATION')
        self.assertEqual(set(ApplicabilityStatus.values), {'CONFIRMED', 'CONDITIONAL', 'NEEDS_CONFIRMATION', 'MISMATCH'})
