from copy import deepcopy
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError
from django.test import TestCase

from sources.models import Agency, AgencyAlias, SourceDocument
from sources.services.agency_seed import load_agencies, seed_agencies
from .agency_helpers import CHECKED, create_agency, seed_entry


class AgencySeedTests(TestCase):
    def load(self, entries):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'agencies.yaml'
            path.write_text(yaml.safe_dump({'agencies': entries}, allow_unicode=True), encoding='utf-8')
            return load_agencies(path)

    def test_operational_seed_has_verified_sources_and_three_local_regions(self):
        with self.assertNumQueries(0):
            specs = load_agencies()
        self.assertTrue({'충청남도', '천안시', '아산시'} <= {values['region'] for values, _ in specs})
        for values, aliases in specs:
            self.assertIsNotNone(values['checked_at'])
            self.assertTrue(values['source_url'].startswith('https://'))
            self.assertTrue(values['phone'])
            self.assertNotIn('가상', values['name'])

    def test_full_validation_before_any_write(self):
        valid = seed_entry()
        invalid = deepcopy(valid)
        del invalid['name']
        with self.assertRaises(ValidationError):
            self.load([valid, invalid])
        self.assertFalse(Agency.objects.exists())

    def test_invalid_fields_and_unverified_contact(self):
        for values in ({'name': ''}, {'department': None}, {'region': '충남'},
                       {'phone': 123}, {'phone': '000', 'checked_at': None},
                       {'checked_at': 'not-a-date'}, {'checked_at': '2099-01-01T00:00:00Z'},
                       {'checked_at': '2026-01-01T00:00:00'},
                       {'source_url': 'https://example.com/'}, {'aliases': '가상'},
                       {'aliases': ['별칭', ' 별칭 ']}, {'aliases': [12]},
                       {'phone': None, 'phone_type': 'DEPARTMENT'}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.load([seed_entry(**values)])

    def test_duplicate_normalized_identity_rejected(self):
        with self.assertRaises(ValidationError):
            self.load([seed_entry(), seed_entry(name=' 가상시청 ')])

    def test_bad_yaml_duplicate_keys_and_top_level(self):
        for content in ('[', 'other: []', 'agencies: {}', 'agencies: []\nagencies: []', 'agencies: [true]'):
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'agencies.yaml'
                path.write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaises(ValidationError):
                    load_agencies(path)

    def test_initial_and_repeated_seed_keep_ids_and_times(self):
        specs = self.load([seed_entry()])
        self.assertEqual(seed_agencies(specs)['created'], 1)
        before = list(Agency.objects.values())
        alias_id = AgencyAlias.objects.get().pk
        self.assertEqual(seed_agencies(specs)['unchanged'], 1)
        self.assertEqual(list(Agency.objects.values()), before)
        self.assertEqual(AgencyAlias.objects.get().pk, alias_id)
        self.assertFalse(SourceDocument.objects.exists())

    def test_newer_verified_contact_replaces_contact_as_one_unit(self):
        original = create_agency()
        specs = self.load([seed_entry(phone='000-111-2222', source_url='https://www.asan.go.kr/new',
                                      checked_at=(CHECKED + timedelta(days=1)).isoformat())])
        self.assertEqual(seed_agencies(specs)['updated'], 1)
        original.refresh_from_db()
        self.assertEqual(original.phone, '000-111-2222')
        self.assertEqual(original.source_url, 'https://www.asan.go.kr/new')
        self.assertEqual(original.checked_at, CHECKED + timedelta(days=1))
        self.assertEqual(Agency.objects.count(), 1)

    def test_blank_contact_preserves_existing_provenance(self):
        original = create_agency()
        before = list(Agency.objects.values())
        for checked in (None, (CHECKED + timedelta(days=1)).isoformat()):
            specs = self.load([seed_entry(phone=None, phone_type='UNKNOWN', checked_at=checked, aliases=[])])
            seed_agencies(specs)
            self.assertEqual(list(Agency.objects.values()), before)

    def test_older_and_equal_conflicting_information_not_applied(self):
        original = create_agency()
        for checked in (CHECKED, CHECKED - timedelta(days=1)):
            specs = self.load([seed_entry(phone='000-222-3333', checked_at=checked.isoformat(), aliases=['다른별칭'])])
            self.assertEqual(seed_agencies(specs)['unchanged'], 1)
            original.refresh_from_db()
            self.assertEqual(original.phone, '000-000-0000')
            self.assertFalse(original.aliases.exists())

    def test_dry_run_is_read_only_for_create_and_update(self):
        specs = self.load([seed_entry()])
        self.assertEqual(seed_agencies(specs, dry_run=True)['created'], 1)
        self.assertFalse(Agency.objects.exists())
        seed_agencies(specs)
        before = list(Agency.objects.values())
        new = self.load([seed_entry(phone='000-123-4567', checked_at=(CHECKED + timedelta(days=1)).isoformat())])
        self.assertEqual(seed_agencies(new, dry_run=True)['updated'], 1)
        self.assertEqual(list(Agency.objects.values()), before)

    def test_unverified_seed_has_no_invented_checked_time(self):
        specs = self.load([seed_entry(phone=None, phone_type='UNKNOWN', source_url=None, checked_at=None, aliases=[])])
        seed_agencies(specs)
        self.assertIsNone(Agency.objects.get().checked_at)
        self.assertIsNone(Agency.objects.get().phone)

    def test_removed_entries_and_admin_aliases_not_deleted(self):
        agency = create_agency()
        alias = AgencyAlias.objects.create(agency=agency, name='관리자등록별칭')
        seed_agencies([])
        seed_agencies(self.load([seed_entry(aliases=[])]))
        self.assertTrue(Agency.objects.filter(pk=agency.pk).exists())
        self.assertTrue(AgencyAlias.objects.filter(pk=alias.pk).exists())

    def test_database_failure_rolls_back_whole_batch(self):
        specs = self.load([seed_entry(), seed_entry(name='다른가상기관')])
        original = AgencyAlias.objects.create
        def fail_second(**kwargs):
            if kwargs['agency'].name == '다른가상기관':
                raise IntegrityError('가상 저장 실패')
            return original(**kwargs)
        with patch.object(AgencyAlias.objects, 'create', side_effect=fail_second), self.assertRaises(IntegrityError):
            seed_agencies(specs)
        self.assertFalse(Agency.objects.exists())
        self.assertFalse(AgencyAlias.objects.exists())

    def test_management_command_and_dry_run_without_network(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('외부 요청 금지')):
            output = StringIO()
            call_command('seed_agencies', dry_run=True, stdout=output)
            self.assertIn('저장 없는 검증', output.getvalue())
            self.assertFalse(Agency.objects.exists())
            call_command('seed_agencies', stdout=StringIO())
            before = list(Agency.objects.values())
            call_command('seed_agencies', stdout=StringIO())
            self.assertEqual(list(Agency.objects.values()), before)
        with self.assertRaises(CommandError):
            call_command('seed_agencies', file='존재하지않는파일.yaml', stdout=StringIO())
