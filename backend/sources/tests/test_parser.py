from django.test import SimpleTestCase

from sources.services.errors import CollectionError
from sources.services.parser import parse_html

from .pipeline_helpers import HTML


class ParserTests(SimpleTestCase):
    def test_body_headings_procedure_phone_and_title_preserved(self):
        page = parse_html(HTML)
        self.assertEqual(page.title, '주민세 납부 안내')
        for text in ('납부 방법과 준비서류', '신분증', '8월 31일', '041-000-0000', '지방세법'):
            self.assertIn(text, page.content_text)

    def test_scripts_style_navigation_footer_related_links_removed(self):
        text = parse_html(HTML).content_text
        for noise in ('추적스크립트', 'display:none', '메뉴 공통', '사이트 공통', '공통 푸터', '추천 링크'):
            self.assertNotIn(noise, text)

    def test_table_rows_columns_and_rowspan_preserved(self):
        text = parse_html(HTML).content_text
        self.assertIn('구분 | 조건 | 기한', text)
        self.assertIn('개인 | 세대주 | 8월 31일', text)
        self.assertIn('개인 | 조건 확인 필요 | 관할 기관 문의', text)

    def test_deterministic_and_menu_only_changes_do_not_change_body(self):
        self.assertEqual(parse_html(HTML), parse_html(HTML))
        changed = HTML.replace('메뉴 공통'.encode(), '완전히 다른 메뉴'.encode())
        self.assertEqual(parse_html(HTML).content_text, parse_html(changed).content_text)

    def test_utf8_cp949_euc_kr_and_meta_encoding(self):
        text = HTML.decode().replace('utf-8', 'cp949')
        for encoding in ('cp949', 'euc-kr'):
            with self.subTest(encoding=encoding):
                page = parse_html(text.encode(encoding), f'text/html; charset={encoding}')
                self.assertIn('주민세 납부', page.content_text)
        self.assertIn('주민세', parse_html(text.encode('cp949'), 'text/html').content_text)

    def test_broken_or_unknown_encoding_rejected(self):
        for body, content_type in ((b'\xff\xfe\xfd', 'text/html; charset=utf-8'), (HTML, 'text/html; charset=unknown'), (HTML + b'\x00', 'text/html; charset=utf-8')):
            with self.subTest(content_type=content_type), self.assertRaises(CollectionError):
                parse_html(body, content_type)

    def test_empty_short_attachment_menu_only_or_login_rejected(self):
        for body in (b'', b'<html><main>menu</main></html>', '<html><nav>외국인등록 신청서류 주민세 납부</nav></html>'.encode(), HTML.replace(b'<main>', b'<main><input type="password">')):
            with self.subTest(body=body[:40]), self.assertRaises(CollectionError):
                parse_html(body)

    def test_error_page_rejected_even_if_it_contains_admin_words(self):
        with self.assertRaisesMessage(CollectionError, 'PAGE_REJECTED'):
            parse_html(HTML.replace('<title>주민세 납부 안내'.encode(), '<title>접근 차단'.encode()))

    def test_fallback_requires_meaningful_paragraphs(self):
        no_main = HTML.replace(b'<main>', b'<div>').replace(b'</main>', b'</div>')
        self.assertIn('주민세', parse_html(no_main).content_text)

    def test_official_legal_reference_preserved_without_secret_or_script_links(self):
        extra = (
            '<a href="https://www.law.go.kr/법령/지방세법">근거법령</a>'
            '<a href="https://www.asan.go.kr/notice?token=secret">민감한 링크</a>'
            '<a href="javascript:alert(1)">실행 링크</a>'
        ).encode()
        text = parse_html(HTML.replace(b'</main>', extra + b'</main>')).content_text
        self.assertIn('https://www.law.go.kr/법령/지방세법', text)
        self.assertNotIn('token=secret', text)
        self.assertNotIn('javascript:', text)

    def test_site_specific_body_excludes_long_menu_and_hidden_popups(self):
        notice = HTML.decode().split('<main>')[1].split('</main>')[0]
        body = ('<html><title>행정 안내</title><body><div class="contentsWrap">'
                '<div class="wrap_col"><div class="left">' + notice + '</div></div></div>'
                '<div class="popup_wrap">서비스 차단 팝업</div>'
                '<div style="display: none">숨긴 공통문구</div></body></html>')
        page = parse_html(body.encode(), url='https://www.gov.kr/mw/AA020InfoCappView.do?id=1')
        self.assertIn('8월 31일', page.content_text)
        self.assertNotIn('팝업', page.content_text)
