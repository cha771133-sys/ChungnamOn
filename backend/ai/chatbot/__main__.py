"""Django 없이 터미널에서 바로 질문해 보는 개발용 도구.

    cd backend
    python -m ai.chatbot "주민세 안 내면 어떻게 돼요?"
    python -m ai.chatbot --lang vi "Tôi trả ở đâu?"
    python -m ai.chatbot --search "주민세 납부 기간"     # 검색 점수만 보기 (AI_MIN_SCORE 튜닝용)
    python -m ai.chatbot --kb ai/tests/fixtures "..."    # 테스트 샘플로 해 보기
    python -m ai.chatbot --search --db "주민세 납부 기간"  # 수집된 공식 자료(DB)에서 점수 보기

기본은 Django DB 대신 .md 폴더를 메모리에서 검색한다 (기본: sources/data/pages, 없으면 --kb로 지정).
--db면 manage.py와 같은 설정으로 sources 앱 테이블을 검색한다. backend/.env 값(LLM·임베딩)을 읽는다.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def main():
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND / ".env")
    except ImportError:
        pass

    ap = argparse.ArgumentParser(prog="python -m ai.chatbot")
    ap.add_argument("question")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--doc", help="해석 결과 JSON 파일 (없으면 아산시 주민세 고지서 샘플)")
    ap.add_argument("--search", action="store_true", help="답변 없이 검색 결과·점수만 출력")
    ap.add_argument("--kb", help="검색할 .md 폴더 (예: ai/tests/fixtures)")
    ap.add_argument("--db", action="store_true", help="수집된 공식 자료(Django DB)를 검색. 청크·임베딩이 있어야 함")
    args = ap.parse_args()

    if args.db:
        # manage.py와 같은 설정으로 Django를 켜고 sources 앱 테이블을 검색
        sys.path.insert(0, str(BACKEND))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django

        django.setup()
        os.environ["AI_RETRIEVER"] = "db"
    else:
        os.environ["AI_RETRIEVER"] = "memory"  # 기본은 Django 없이 .md 폴더를 검색
    if args.kb:
        os.environ["AI_KB_DIR"] = str(Path(args.kb).resolve())

    from . import generate_answer
    from .retriever import search
    from .service import regions_for
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    doc = (
        json.loads(Path(args.doc).read_text(encoding="utf-8"))
        if args.doc
        else {
            "title": "주민세 납부 안내",
            "doc_type": "고지서",
            "summary": ["2026년 8월 31일까지 주민세 10,000원 납부"],
            "deadlines": [{"label": "납부 마감", "date": "2026-08-31"}],
            "required_docs": ["고지서"],
            "agency": {"name": "아산시청 세무과", "phone": "041-000-0000"},
            "original_text": "2026년도 주민세(개인분) 납세고지서\n납부기한: 2026.08.31.\n납부금액: 10,000원\n발행: 아산시장",
        }
    )

    if args.search:
        for r in search(args.question, regions=regions_for(doc), min_score=-1.0, top_k=10):
            doc_id = f"#{r.source_document_id} " if r.source_document_id is not None else ""
            print(f"{r.score:.4f}  {doc_id}[{r.region}] {r.title}  | {r.text[:80]!r}")
        return

    out = generate_answer(doc, args.question, language=args.lang)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
