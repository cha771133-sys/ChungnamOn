import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path

import yaml
from django.test import SimpleTestCase

from sources.services.errors import CollectionError
from sources.services.source_registry import load_registry, validate_source_url

from .pipeline_helpers import URL, spec


class RegistryTests(SimpleTestCase):
    def test_committed_registry_is_valid_and_contains_no_placeholder_hosts(self):
        entries = load_registry()
        self.assertTrue(entries)
        self.assertTrue(any(entry.enabled for entry in entries))
        for entry in entries:
            self.assertEqual(validate_source_url(entry.url), entry.url)
            self.assertTrue(entry.notes)

    def load(self, entries=None, raw=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.yaml'
            path.write_text(raw if raw is not None else yaml.safe_dump({'sources': entries or [asdict(spec())]}, allow_unicode=True), encoding='utf-8')
            return load_registry(path)

    def test_valid_yaml_metadata_dates_and_disabled(self):
        source = spec(published_at=date(2026, 8, 1), enabled=False)
        self.assertEqual(self.load([asdict(source)]), [source])

    def test_date_string_and_unknown_date(self):
        item = asdict(spec())
        item['published_at'] = '2026-08-01'
        self.assertEqual(self.load([item])[0].published_at, date(2026, 8, 1))
        self.assertIsNone(self.load()[0].published_at)

    def test_missing_or_blank_required_metadata(self):
        for field in ('url', 'title', 'agency', 'region', 'category'):
            for missing in (True, False):
                with self.subTest(field=field, missing=missing):
                    item = asdict(spec())
                    if missing:
                        del item[field]
                    else:
                        item[field] = ' '
                    with self.assertRaisesMessage(CollectionError, field):
                        self.load([item])

    def test_duplicate_normalized_url_and_yaml_key(self):
        with self.assertRaisesMessage(CollectionError, '중복'):
            self.load([asdict(spec()), asdict(spec(url=URL.upper().replace('/TEST-FIXTURES/NOTICE?ID=1', '/test-fixtures/notice?id=1') + '#내용'))])
        with self.assertRaises(CollectionError):
            self.load(raw='sources: []\nsources: []')

    def test_invalid_structure_yaml_unknown_fields_and_types(self):
        for raw in ('[1, 2]', 'sources: null', 'sources: [1]', 'sources: [', 'sources: []\nextra: 1'):
            with self.subTest(raw=raw), self.assertRaises(CollectionError):
                self.load(raw=raw)
        for changes in ({'enabled': 'false'}, {'notes': []}, {'selector': 'body'}, {'published_at': '없는 날짜'}, {'region': '충남'}, {'category': '세금'}, {'title': '가' * 501}):
            with self.subTest(changes=changes), self.assertRaises(CollectionError):
                self.load([{**asdict(spec()), **changes}])

    def test_disallowed_urls_do_not_echo_sensitive_input(self):
        urls = [
            'http://www.asan.go.kr/notice', 'ftp://www.asan.go.kr/notice',
            'https://127.0.0.1/', 'https://[::1]/', 'https://localhost/',
            'https://169.254.169.254/latest/', 'https://10.0.0.1/',
            'https://www.asan.go.kr.evil.example/notice', 'https://evil.asan.go.kr/',
            'https://password@www.asan.go.kr/', 'https://www.asan.go.kr:8443/',
            'https://www.asan.go.kr/?access_token=secret-value',
            'https://www.asan.go.kr/a;jsessionid=secret-value',
            'https://www.asan.go.kr/\nnotice', 'https://www.asan.go.kr\\@127.0.0.1/',
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(CollectionError) as caught:
                validate_source_url(url)
            self.assertNotIn('secret-value', str(caught.exception))
            self.assertNotIn(url, str(caught.exception))

    def test_document_query_preserved_and_fragment_normalized(self):
        url = 'HTTPS://WWW.ASAN.GO.KR:443/notice?pds_no=1&no=131&utm_source=demo#본문'
        self.assertEqual(validate_source_url(url), 'https://www.asan.go.kr/notice?pds_no=1&no=131&utm_source=demo')

    def test_missing_file_and_empty_registry(self):
        with self.assertRaises(CollectionError):
            load_registry(Path(tempfile.gettempdir()) / 'missing-chungnamon-urls.yaml')
        self.assertEqual(self.load(raw='sources: []'), [])
