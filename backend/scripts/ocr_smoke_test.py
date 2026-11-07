"""CLOVA OCR General 응답을 직접 확인하는 W1 테스트 스크립트. Django 없이 실행한다.

사용법 (backend/에서, 가상환경 활성화 후):
    python scripts/ocr_smoke_test.py C:\\샘플\\notice.jpg

backend/.env의 CLOVA_OCR_INVOKE_URL, CLOVA_OCR_SECRET을 읽는다.
응답 JSON은 문서 내용을 담고 있으므로 저장소 밖(기본 ~/ocr-test-output)에 저장한다.
호출할 때마다 요금이 발생할 수 있고, PDF는 페이지 수만큼 부과된다.
"""

import argparse
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = Path.home() / 'ocr-test-output'
ALLOWED_FORMATS = {'jpg', 'jpeg', 'png', 'pdf'}
TIMEOUT = (5, 60)  # (연결, 읽기) 초
LOW_CONFIDENCE = 0.7


def call_clova(invoke_url, secret, path, file_format):
    body = {
        'version': 'V2',
        'requestId': str(uuid.uuid4()),
        'timestamp': int(time.time() * 1000),
        'lang': 'ko',
        'images': [{
            'format': file_format,
            'name': 'sample',
            # base64: 파일 내용을 JSON에 넣을 수 있도록 글자로 바꾼 형식
            'data': base64.b64encode(path.read_bytes()).decode('ascii'),
        }],
    }
    headers = {'Content-Type': 'application/json', 'X-OCR-SECRET': secret}
    # 리디렉션을 따라가면 다른 서버로 Secret Key와 문서가 전송될 수 있다.
    return requests.post(invoke_url, headers=headers, json=body, timeout=TIMEOUT, allow_redirects=False)


def save_response(data, out_dir, source):
    """응답을 새 파일로 저장한다. 기존 파일을 덮어쓰지 않고, 소유자만 읽도록 권한을 지정한다.

    권한 값은 macOS·Linux에 적용된다. Windows에서는 사용자 폴더의 접근 권한을 따른다.
    """
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    out_path = out_dir / f'{source.stem}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}.json'
    file_descriptor = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(file_descriptor, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    return out_path


def validate_images(data):
    """응답·images·이미지·단어 항목의 형식을 확인하고 images를 돌려준다."""
    if not isinstance(data, dict) or not isinstance(data.get('images'), list):
        sys.exit('응답 형식이 올바르지 않습니다.')
    for image in data['images']:
        if not isinstance(image, dict) or not isinstance(image.get('fields', []), list):
            sys.exit('응답 형식이 올바르지 않습니다.')
        for field in image.get('fields', []):
            if (
                not isinstance(field, dict)
                or not isinstance(field.get('inferText'), str)
                or not isinstance(field.get('inferConfidence'), (int, float))
            ):
                sys.exit('응답 형식이 올바르지 않습니다.')
    return data['images']


def restore_lines(fields):
    """lineBreak가 true인 단어까지를 한 줄로 묶어 원문 줄 구조를 복원한다."""
    lines, words = [], []
    for field in fields:
        words.append(field.get('inferText', ''))
        if field.get('lineBreak'):
            lines.append(' '.join(words))
            words = []
    if words:
        lines.append(' '.join(words))
    return lines


def print_image(index, image):
    """이미지 하나의 인식 결과와 단어별 신뢰도를 출력한다."""
    fields =image.get('fields', [])
    print(f'\n===== images[{index}] =====')
    print(f"inferResult: {image.get('inferResult')}  message: {image.get('message')}")
    print(f"convertedImageInfo: {image.get('convertedImageInfo')}")
    print(f'단어 수: {len(fields)}')

    print('\n--- 복원한 텍스트 ---')
    print('\n'.join(restore_lines(fields)))

    print('\n--- 단어별 신뢰도 (! 표시는 0.7 미만, ↵ 표시는 줄 끝) ---')
    for field in fields:
        confidence = field.get('inferConfidence', 0)
        mark = '!' if confidence < LOW_CONFIDENCE else ' '
        line_end = '↵' if field.get('lineBreak') else ' '
        print(f"{mark} {confidence:.4f} {line_end} {field.get('inferText', '')}")

    if fields:
        values = [field.get('inferConfidence', 0) for field in fields]
        print(f'\n신뢰도 최솟값 {min(values):.4f} / 평균 {sum(values) / len(values):.4f}')


def main():
    """파일 하나로 CLOVA를 호출하고 응답을 저장·출력한다."""
    # 콘솔에서 표시할 수 없는 글자가 있어도 중단하지 않는다.
    sys.stdout.reconfigure(errors='replace')

    parser = argparse.ArgumentParser(description='CLOVA OCR 응답 확인')
    parser.add_argument('file', type=Path, help='jpg, jpeg, png, pdf 파일 경로')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT_DIR, help='응답 JSON 저장 폴더')
    args = parser.parse_args()

    load_dotenv(BACKEND_DIR / '.env')
    invoke_url = os.environ.get('CLOVA_OCR_INVOKE_URL')
    secret = os.environ.get('CLOVA_OCR_SECRET')
    if not invoke_url or not secret:
        sys.exit('backend/.env에 CLOVA_OCR_INVOKE_URL과 CLOVA_OCR_SECRET을 넣어 주세요.')
    # http로 보내면 원본 문서와 Secret Key가 암호화되지 않은 채 전송된다.
    if urlsplit(invoke_url).scheme.lower() != 'https':
        sys.exit('CLOVA_OCR_INVOKE_URL은 HTTPS 주소여야 합니다.')

    if not args.file.is_file():
        sys.exit(f'파일을 찾을 수 없습니다: {args.file}')
    file_format = args.file.suffix.lower().lstrip('.')
    if file_format not in ALLOWED_FORMATS:
        sys.exit('jpg, jpeg, png, pdf 파일만 사용할 수 있습니다.')

    try:
        response = call_clova(invoke_url, secret, args.file, file_format)
    except requests.RequestException as error:
        # 오류 문구에 Invoke URL이 들어갈 수 있어 오류 종류만 출력한다.
        sys.exit(f'CLOVA 호출 실패: {type(error).__name__}')

    print(f'HTTP 상태: {response.status_code}')
    if 300 <= response.status_code < 400:
        sys.exit('리디렉션 응답은 따라가지 않습니다.')
    try:
        data = response.json()
    except ValueError:
        sys.exit('응답이 JSON 형식이 아닙니다.')

    print(f'응답 저장: {save_response(data, args.out, args.file)}')
    if not response.ok:
        sys.exit(f'CLOVA 오류 응답: {json.dumps(data, ensure_ascii=False)}')

    images = validate_images(data)
    print(f'images 개수: {len(images)}')
    for index, image in enumerate(images):
        print_image(index, image)


if __name__ == '__main__':
    main()
