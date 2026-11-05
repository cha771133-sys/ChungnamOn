"""챗봇 AI 모듈 테스트. 인터넷·API 키·Ollama 없이 돌아간다 (hash 임베딩 + mock LLM).

    cd backend
    python -m unittest discover -s ai/tests -t .

DB 검색 SQL(pgvector)까지 보려면 pgvector가 깔린 Postgres 주소를 넣고 실행:
    AI_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/test python -m unittest discover -s ai/tests -t .
"""
import os
import unittest
from datetime import date

os.environ.update(
    AI_EMBEDDER="hash",
    AI_LLM_PROVIDER="mock",
    AI_EMBED_DIM="384",
    AI_RETRIEVER="memory",
    AI_MIN_SCORE="0.05",
    AI_KB_DIR=str(__import__("pathlib").Path(__file__).resolve().parent / "fixtures"),
)

from ai.chatbot import GroundingStatus, generate_answer, generate_answer_dummy, prepare_chunks  # noqa: E402
from ai.chatbot.config import reset_settings  # noqa: E402
from ai.chatbot.retriever import SearchResult, search  # noqa: E402
from ai.chatbot.service import _history_messages, decide_status, normalize_document, regions_for  # noqa: E402

DOC = {
    "documentId": 123,
    "docType": "고지서",
    "title": "주민세 납부 안내",
    "language": "vi",
    "summary": ["2026년 8월 31일까지 주민세 10,000원 납부"],
    "deadlines": [{"label": "납부 마감", "date": "2026-08-31"}],
    "requiredDocs": ["고지서"],
    "agency": {"name": "아산시청 세무과", "phone": "041-000-0000"},
}

KEYS = {"answer", "grounding_status", "sources", "agency_contact", "suggested_questions", "language"}


def _r(i):
    return SearchResult(i, f"text{i}", 0.9, f"https://x/{i}", "t", "a", "충남", None, date(2026, 10, 30))


class DecideStatusTests(unittest.TestCase):
    def test_grounded_keeps_only_used_sources(self):
        status, used = decide_status({"status": "GROUNDED", "used_sources": ["S2"]}, [_r(1), _r(2), _r(3)])
        self.assertEqual(status, GroundingStatus.GROUNDED)
        self.assertEqual([u.chunk_id for u in used], [2])

    def test_grounded_without_sources_downgrades(self):
        status, used = decide_status({"status": "GROUNDED", "used_sources": []}, [_r(1)])
        self.assertEqual((status, used), (GroundingStatus.UNVERIFIED, []))

    def test_no_results_is_unverified(self):
        status, _ = decide_status({"status": "GROUNDED", "used_sources": ["S1"]}, [])
        self.assertEqual(status, GroundingStatus.UNVERIFIED)

    def test_conflict_keeps_sources_and_ignores_bad_ids(self):
        status, used = decide_status({"status": "conflict", "used_sources": ["S1", "3", "S9"]}, [_r(1), _r(2), _r(3)])
        self.assertEqual(status, GroundingStatus.CONFLICT)
        self.assertEqual([u.chunk_id for u in used], [1, 3])

    def test_unknown_status(self):
        status, _ = decide_status({"status": "MAYBE", "used_sources": ["S1"]}, [_r(1)])
        self.assertEqual(status, GroundingStatus.UNVERIFIED)


class InputTests(unittest.TestCase):
    def test_camel_and_snake_both_work(self):
        d = normalize_document(DOC)
        self.assertEqual(d["doc_type"], "고지서")
        self.assertEqual(d["required_docs"], ["고지서"])
        self.assertEqual(normalize_document({"agency": "아산시청"})["agency"], {"name": "아산시청"})

    def test_regions(self):
        self.assertEqual(regions_for(normalize_document(DOC)), ["아산시", "충청남도", "충남", "전국"])
        self.assertEqual(regions_for({"agency": {"name": "천안 서북구청"}})[0], "천안시")
        self.assertEqual(regions_for({"agency": {"name": "국민건강보험공단"}}), ["충청남도", "충남", "전국"])
        self.assertEqual(regions_for({"region": "충청남도"}), ["충청남도", "충남", "전국"])

    def test_history_cleanup(self):
        h = [
            {"role": "assistant", "content": "안녕"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2 (답 없음)"},
        ]
        self.assertEqual([m["role"] for m in _history_messages(h)], ["user", "assistant"])

    def test_prepare_chunks_merges_short_sections_and_adds_title(self):
        chunks = prepare_chunks("# 안내\n" + "가" * 200 + "\n# 문의\n세무과", title="[샘플] 주민세")
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].startswith("[주민세]\n"))
        self.assertIn("# 문의", chunks[0])


class ConfigTests(unittest.TestCase):
    def test_empty_env_values_use_defaults(self):
        # .env.example을 그대로 복사한 .env처럼 AI_ 변수가 빈 문자열로 들어온 경우
        from unittest import mock

        from ai.chatbot.config import get_settings

        empty = {k: "" for k in ("AI_LLM_PROVIDER", "AI_EMBEDDER", "AI_EMBED_DIM", "AI_RETRIEVER",
                                 "AI_KB_DIR", "AI_TOP_K", "AI_MIN_SCORE", "AI_OLLAMA_URL")}
        with mock.patch.dict(os.environ, empty):
            reset_settings()
            try:
                s = get_settings()
            finally:
                reset_settings()
        self.assertEqual((s.llm_provider, s.embedder, s.embed_dim, s.retriever), ("", "ollama", 1024, "db"))
        self.assertEqual((s.top_k, s.min_score, s.ollama_url), (5, 0.35, "http://localhost:11434"))
        self.assertEqual(s.kb_dir.parts[-3:], ("sources", "data", "pages"))


class MemorySearchTests(unittest.TestCase):
    def test_region_filter(self):
        rs = search("주민세 납부 기간", regions=["아산시", "충남", "전국"], top_k=20, min_score=-1)
        self.assertTrue(rs)
        self.assertTrue(all(r.region in ("아산시", "충남", "전국") for r in rs))
        self.assertFalse(any("천안" in (r.title or "") for r in rs))


class GenerateAnswerTests(unittest.TestCase):
    def test_grounded(self):
        out = generate_answer(DOC, "주민세 안 내면 어떻게 돼요?", language="ko")
        self.assertEqual(set(out), KEYS)
        self.assertEqual(out["grounding_status"], "GROUNDED")
        self.assertTrue(out["sources"] and out["sources"][0]["checked_at"])
        self.assertIsNone(out["agency_contact"])
        self.assertEqual(out["language"], "ko")

    def test_unverified_has_agency_and_doc_language(self):
        os.environ["AI_MIN_SCORE"] = "0.999"
        reset_settings()
        try:
            out = generate_answer(DOC, "Tôi có được miễn thuế không?")
        finally:
            os.environ["AI_MIN_SCORE"] = "0.05"
            reset_settings()
        self.assertEqual(out["grounding_status"], "UNVERIFIED")
        self.assertEqual(out["sources"], [])
        self.assertEqual(out["agency_contact"], {"name": "아산시청 세무과", "phone": "041-000-0000"})
        self.assertEqual(out["language"], "vi")

    def test_empty_message(self):
        with self.assertRaises(ValueError):
            generate_answer(DOC, "  ")

    def test_dummy_same_shape_all_states(self):
        got = {generate_answer_dummy(DOC, q)["grounding_status"] for q in ("기한?", "감면 돼요?", "아무거나")}
        self.assertEqual(got, {"GROUNDED", "CONFLICT", "UNVERIFIED"})
        self.assertEqual(set(generate_answer_dummy(DOC, "x")), KEYS)


@unittest.skipUnless(os.environ.get("AI_TEST_DATABASE_URL"), "AI_TEST_DATABASE_URL 없음")
class PgVectorSearchTests(unittest.TestCase):
    """sources 앱 테이블 모양(명세서 6장)을 흉내 내서 검색 SQL을 확인."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        from ai.chatbot.embedder import embed_passages, to_pgvector

        cls.conn = psycopg.connect(os.environ["AI_TEST_DATABASE_URL"], autocommit=True)
        c = cls.conn
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        c.execute("DROP TABLE IF EXISTS ai_test_chunk, ai_test_doc")
        c.execute(
            """CREATE TABLE ai_test_doc (id serial primary key, source_url text, title text, agency text,
               region text, published_at date, collected_at timestamptz default now(), status text)"""
        )
        c.execute(
            """CREATE TABLE ai_test_chunk (id serial primary key, source_document_id int references ai_test_doc(id),
               chunk_text text, chunk_order int, embedding vector(384))"""
        )
        rows = [
            ("u1", "아산 주민세", "아산시청", "아산시", "ACTIVE", "아산시 주민세 납부 기간은 8월 16일부터 31일"),
            ("u2", "천안 주민세", "천안시청", "천안시", "ACTIVE", "천안시 주민세 납부 기간은 8월 16일부터 31일"),
            ("u3", "충남 납부 방법", "충남도청", "충남", "ACTIVE", "지방세는 가상계좌 위택스 은행에서 납부"),
            ("u4", "폐기 자료", "충남도청", "충남", "DISCARDED", "아산시 주민세 납부 기간은 8월 16일부터 31일"),
        ]
        for url, title, agency, region, status, text in rows:
            did = c.execute(
                "INSERT INTO ai_test_doc (source_url,title,agency,region,status) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (url, title, agency, region, status),
            ).fetchone()[0]
            c.execute(
                "INSERT INTO ai_test_chunk (source_document_id,chunk_text,chunk_order,embedding) VALUES (%s,%s,0,%s::vector)",
                (did, text, to_pgvector(embed_passages([text])[0])),
            )

        os.environ.update(AI_RETRIEVER="db", AI_SOURCE_DOCUMENT_TABLE="ai_test_doc", AI_SOURCE_CHUNK_TABLE="ai_test_chunk")
        reset_settings()
        from ai.chatbot.retriever import DbRetriever

        class _Conn:  # Django connection처럼 vendor·cursor()만 흉내
            vendor = "postgresql"

            def cursor(self):
                return cls.conn.cursor()

        cls._orig = DbRetriever._connection
        DbRetriever._connection = staticmethod(lambda: _Conn())

    @classmethod
    def tearDownClass(cls):
        from ai.chatbot.retriever import DbRetriever

        DbRetriever._connection = cls._orig
        cls.conn.execute("DROP TABLE IF EXISTS ai_test_chunk, ai_test_doc")
        cls.conn.close()
        os.environ.update(AI_RETRIEVER="memory", AI_SOURCE_DOCUMENT_TABLE="sources_sourcedocument", AI_SOURCE_CHUNK_TABLE="sources_sourcechunk")
        reset_settings()

    def test_filters_region_and_discarded(self):
        rs = search("아산시 주민세 납부 기간", regions=["아산시", "충남", "전국"], min_score=-1, top_k=10)
        self.assertEqual([r.title for r in rs][0], "아산 주민세")
        self.assertNotIn("천안 주민세", [r.title for r in rs])
        self.assertNotIn("폐기 자료", [r.title for r in rs])
        self.assertIsInstance(rs[0].checked_at, date)

    def test_generate_answer_over_pgvector(self):
        out = generate_answer(DOC, "주민세 납부 기간", language="ko")
        self.assertEqual(out["grounding_status"], "GROUNDED")
        self.assertEqual(out["sources"][0]["url"], "u1")


if __name__ == "__main__":
    unittest.main()
