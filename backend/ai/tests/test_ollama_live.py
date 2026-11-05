"""실제 Ollama(bge-m3 + qwen2.5:7b)로 끝까지 돌려 보는 선택 테스트. 기본은 건너뜀.

    AI_LIVE_OLLAMA=1 python manage.py test ai.tests.test_ollama_live -v 2

테스트 DB에 ai/tests/fixtures의 [샘플] 자료(지어낸 내용)를 넣어서 쓰므로 개발 DB는 건드리지 않는다.
샘플 URL은 sources 앱 공식 URL 검사를 통과하도록 테스트 안에서만 공식 도메인으로 바꾼다.
LLM 출력은 매번 달라질 수 있어서 형식·출처 범위만 검사하고 답변은 출력해서 사람이 본다.
"""
import os
import unittest
from pathlib import Path
from unittest import mock

LIVE = os.environ.get("AI_LIVE_OLLAMA") == "1"

if LIVE:
    from django.test import TestCase, override_settings
else:
    TestCase = unittest.TestCase

    def override_settings(**_):
        return lambda f: f

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOSTS = {"아산시": "www.asan.go.kr", "천안시": "www.cheonan.go.kr", "충남": "www.chungnam.go.kr", "전국": "www.hikorea.go.kr"}
# CPU만 쓰는 PC에서는 답변 한 번에 몇 분 걸릴 수 있어서 timeout을 넉넉히 (AI_REQUEST_TIMEOUT으로 덮어쓰기 가능)
ENV = {"AI_EMBEDDER": "ollama", "AI_LLM_PROVIDER": "ollama", "AI_RETRIEVER": "db", "AI_EMBED_DIM": "1024",
       "AI_REQUEST_TIMEOUT": os.environ.get("AI_REQUEST_TIMEOUT", "900")}


@unittest.skipUnless(LIVE, "AI_LIVE_OLLAMA=1 일 때만 실행 (Ollama에 bge-m3, qwen2.5:7b 필요)")
@override_settings(CHATBOT_AI_MODE="rag", DOCUMENTS_VERIFY_MODE="rag", DOCUMENTS_STEP_DELAY=0)
class OllamaLiveTests(TestCase):
    def setUp(self):
        from django.utils import timezone

        from ai.chatbot import build_source_chunks
        from ai.chatbot.chunker import parse_front_matter
        from ai.chatbot.config import reset_settings
        from sources.models import SourceDocument

        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_settings()
        self.addCleanup(reset_settings)

        self.docs = {}
        for path in sorted(FIXTURES.glob("*.md")):
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
            region = meta["region"]
            self.docs[path.stem] = SourceDocument.objects.create(
                source_url=f"https://{HOSTS[region]}/sample/{path.stem}", title=meta["title"],
                agency=meta["agency"], region="충청남도" if region == "충남" else region,
                category=meta.get("category", ""), status="ACTIVE", content_text=body, collected_at=timezone.now(),
            )
        self.stats = build_source_chunks()

    def test_end_to_end(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import AccessToken

        from documents.models import Document, InterpretationResult
        from documents.services import pipeline
        from sources.models import SourceChunk

        # 1) 청크·임베딩
        print("\n[청크]", self.stats)
        self.assertEqual(self.stats["documents"], len(self.docs))
        self.assertTrue(all(len(c.embedding) == 1024 for c in SourceChunk.objects.all()))

        # 2) 파이프라인 (더미 OCR·해석 = 아산시 주민세 고지서) → rag verify
        user = get_user_model().objects.create_user("live@example.com")
        document = Document.objects.create(owner=user, file="documents/t.pdf", file_name="t.pdf", file_type="PDF")
        self.assertTrue(pipeline.process_document(document.pk))
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.DONE, document.fail_reason)
        verification = InterpretationResult.objects.get(document=document).verification
        print("[검증]", verification)
        ids = [s["sourceDocumentId"] for s in verification["sources"]]
        self.assertIn(verification["status"], ("MATCHED", "MISMATCH", "UNVERIFIED"))
        self.assertLessEqual(len(ids), 10)
        self.assertIn(self.docs["asan_resident_tax"].pk, ids)
        self.assertNotIn(self.docs["cheonan_resident_tax"].pk, ids)  # 다른 시 자료는 검색 범위 밖

        # 3) 챗봇 API (rag)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
        url = f"/api/documents/{document.pk}/chat/"
        for question in ("Hạn nộp thuế cư trú là khi nào?", "기한 지나면 어떻게 돼요?", "체류지 변경신고는 며칠 안에 해야 해요?"):
            res = client.post(url, {"message": question}, format="json")
            self.assertEqual(res.status_code, 201, res.content)
            body = res.json()
            print(f"[챗봇] Q={question}\n  status={body['status']} sources="
                  f"{[s['sourceDocumentId'] for s in body['sources']]}\n  A={body['answer']}\n"
                  f"  추천={body['suggestedQuestions']}")
            self.assertTrue({s["sourceDocumentId"] for s in body["sources"]} <= set(ids))
            if verification["status"] == "UNVERIFIED":
                self.assertEqual(body["status"], "UNVERIFIED")
