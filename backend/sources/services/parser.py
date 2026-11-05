import codecs
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString

from .errors import CollectionError
from .source_registry import OFFICIAL_HOSTS, SENSITIVE_KEY


# 사이트별 차이는 선택자에만 둔다. 외부 페이지가 선택자를 지정하지 못한다.
SITE_SELECTORS = {
    'www.asan.go.kr': ('.board_view_con', '.view_cont', '.page_content', '#skip-content'),
    'www.cheonan.go.kr': ('#txt', '.view_cont', '#contents'),
    'www.chungnam.go.kr': ('.sub-content', '.board-view-content', '.view_cont'),
    'www.gov.kr': ('.contentsWrap .wrap_col .left', '.contentsWrap .wrap_col', '.contentsWrap'),
    'www.hikorea.go.kr': ('.cont_wrap', '#content'),
    'www.immigration.go.kr': ('.artclView', '._contentBuilder', '#contents'),
    'www.nhis.or.kr': ('.contents', '#content'),
}
REMOVE = (
    'script, style, noscript, nav, footer, header, aside, form, iframe, svg, '
    '[hidden], [aria-hidden="true"], [role="navigation"], '
    '.gnb, .lnb, .snb, .breadcrumb, .breadcrumbs, .popup, .pop-up, .advertisement, '
    '.board-paging, .pagination, .related-links, .share, .satisfaction, '
    '.layer, .layer_wrap, .layer-popup, .popup_wrap, .content-ft, .content-ft-top, '
    '[style*="display:none"], [style*="display: none"]'
)

# 내려받기 크기 제한과 별개로 병합 셀 복제로 늘어나는 정제 원문을 제한한다.
MAX_TEXT_CHARS = 2 * 1024 * 1024


@dataclass(frozen=True)
class ParsedPage:
    title: str
    content_text: str


def decode_html(body, content_type):
    header = re.search(r'charset\s*=\s*["\']?([\w-]+)', content_type, re.I)
    meta = re.search(rb'<meta\b[^>]*charset\s*=\s*["\']?([\w-]+)', body[:8192], re.I)
    encoding = header.group(1) if header else None
    if meta and (not encoding or encoding.lower() in ('iso-8859-1', 'latin1')):
        encoding = meta.group(1).decode('ascii')
    candidates = [encoding] if encoding else ['utf-8-sig', 'cp949']
    for candidate in candidates:
        try:
            codecs.lookup(candidate)
            text = body.decode(candidate, errors='strict')
        except (UnicodeError, LookupError):
            continue
        if '\ufffd' in text or '\x00' in text or len(re.findall(r'[ÃÂìë][\x80-\xff]', text)) > 2:
            continue
        return text
    raise CollectionError('ENCODING_FAILED', '본문의 문자 인코딩을 안전하게 해석하지 못했습니다.')


def _table_text(table, max_chars=MAX_TEXT_CHARS):
    """행·열 순서와 병합 셀을 보존하여 조건/값의 대응이 사라지지 않게 한다."""
    caption = table.find('caption', recursive=False)
    title = ' '.join(caption.stripped_strings) if caption else '표 (각 줄은 행, |는 열 구분)'
    characters = len(title) + 3
    if characters > max_chars:
        raise CollectionError('BODY_TOO_LARGE', '정제 원문의 길이가 제한을 초과했습니다.')
    grid, carried, group = [], {}, None
    for row in table.find_all('tr'):
        if row.find_parent('table') is not table:
            continue
        if row.parent is not group:
            # rowspan은 thead/tbody/tfoot 또는 다른 tbody 경계를 넘지 않는다.
            carried.clear()
            group = row.parent
        cells, column = {}, 0
        for col, (remaining, text) in list(carried.items()):
            cells[col] = text
            if remaining <= 1:
                del carried[col]
            else:
                carried[col] = (remaining - 1, text)
        for cell in row.find_all(['th', 'td'], recursive=False):
            while column in cells:
                column += 1
            text = ' '.join(cell.stripped_strings)
            try:
                colspan, rowspan = int(cell.get('colspan', 1)), int(cell.get('rowspan', 1))
            except ValueError:
                raise CollectionError('TABLE_INVALID', '표의 병합 셀 구조를 해석하지 못했습니다.') from None
            if not 1 <= colspan <= 50 or not 1 <= rowspan <= 1000:
                raise CollectionError('TABLE_INVALID', '표의 병합 셀 크기가 제한을 초과했습니다.')
            for offset in range(colspan):
                col = column + offset
                cells[col] = text
                if rowspan > 1:
                    carried[col] = (rowspan - 1, text)
            column += colspan
        if cells:
            # join 전에 계산하여 큰 병합 문자열을 만들기 전에 중단한다.
            characters += sum(len(value) for value in cells.values()) + 3 * max(cells) + bool(grid)
            if characters > max_chars:
                raise CollectionError('BODY_TOO_LARGE', '정제 원문의 길이가 제한을 초과했습니다.')
            grid.append(' | '.join(cells.get(col, '') for col in range(max(cells) + 1)))
    return '\n' + title + '\n' + '\n'.join(grid) + '\n'


def parse_html(body, content_type='text/html; charset=utf-8', url=''):
    soup = BeautifulSoup(decode_html(body, content_type), 'html.parser')
    document_title = ' '.join(soup.title.stripped_strings) if soup.title else ''
    if re.search(r'오류|에러|not found|access denied|captcha|보안문자|접근.{0,8}(?:차단|제한)|\b40[34]\b|서비스.{0,8}(?:점검|중단)', document_title, re.I):
        raise CollectionError('PAGE_REJECTED', '오류·접근 제한 안내 페이지입니다.')
    if soup.select_one('input[type="password"]'):
        raise CollectionError('PAGE_REJECTED', '로그인 페이지는 수집하지 않습니다.')
    for node in soup.select(REMOVE):
        node.decompose()
    selectors = (*SITE_SELECTORS.get(urlsplit(url).hostname, ()), 'article', 'main', '[role="main"]')
    root = next((node for selector in selectors if (node := soup.select_one(selector)) is not None), None)
    if root is None:
        # 의미 있는 문단이 있는 body만 제한적으로 허용한다.
        root = soup.body
        if root is None or not root.find(['p', 'table', 'dl']):
            raise CollectionError('BODY_MISSING', '행정 안내 본문 영역을 찾지 못했습니다.')
    heading = root.find(['h1', 'h2'])
    title = document_title or (' '.join(heading.stripped_strings) if heading else '')
    # 근거법령·공식 출처 링크는 텍스트와 함께 보존하되 추가 요청하지 않는다.
    for anchor in root.select('a[href]'):
        try:
            reference = urljoin(url, anchor['href'])
            parts = urlsplit(reference)
            if (
                parts.scheme == 'https' and not parts.username and not parts.password
                and parts.hostname in (OFFICIAL_HOSTS | {'law.go.kr', 'www.law.go.kr'})
                and not any(SENSITIVE_KEY.search(key) for key, _ in parse_qsl(parts.query))
                and not re.search(r';[^/]*(?:session|token|password)', unquote(parts.path), re.I)
                and anchor.get_text(strip=True) and len(reference) <= 2048
            ):
                anchor.append(NavigableString(f' ({reference})'))
        except ValueError:
            pass
    table_characters = 0
    for table in list(root.find_all('table', recursive=True)):
        if table.find_parent('table') is None:
            table_text = _table_text(table, MAX_TEXT_CHARS - table_characters)
            table_characters += len(table_text)
            table.replace_with(NavigableString(table_text))
    for br in root.find_all('br'):
        br.replace_with(NavigableString('\n'))
    lines = [' '.join(line.split()) for line in root.get_text('\n').splitlines()]
    text = '\n'.join(line for line in lines if line)
    if len(text) > MAX_TEXT_CHARS:
        raise CollectionError('BODY_TOO_LARGE', '정제 원문의 길이가 제한을 초과했습니다.')
    if (
        len(text) < 80 or len(re.findall('[가-힣]', text)) < 15
        or not re.search(r'주민세|보험|외국인|체류|등록', text)
        or not re.search(r'납부|신청|서류|대상|기한|절차|등록|기간', text)
    ):
        raise CollectionError('BODY_INVALID', '충분한 행정 안내 본문이 없습니다. 메뉴·첨부 전용 페이지를 확인하세요.')
    return ParsedPage(title=title[:500], content_text=text)
