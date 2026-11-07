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
    return requests.post(invoke_url, headers=headers, json=body, timeout=TIMEOUT)


def save_response(data, out_dir, source):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{source.stem}_{time.strftime("%Y%m%d_%H%M%S")}.json'
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_path


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
    fields = image.get('fields', [])
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
    try:
        data = response.json()
    except ValueError:
        sys.exit('응답이 JSON 형식이 아닙니다.')

    print(f'응답 저장: {save_response(data, args.out, args.file)}')
    if not response.ok:
        sys.exit(f'CLOVA 오류 응답: {json.dumps(data, ensure_ascii=False)}')

    images = data.get('images', [])
    print(f'images 개수: {len(images)}')
    for index, image in enumerate(images):
        print_image(index, image)


if __name__ == '__main__':
    main()
