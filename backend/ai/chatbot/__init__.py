"""문서 챗봇 AI 모듈.

chatbot 앱(질문 API)이 쓰는 것
    generate_answer(document, message, history=..., language=...) -> dict
    generate_answer_dummy(...)  : 같은 형식의 더미 (AI 준비 전 연동용)
    GroundingStatus             : GROUNDED / UNVERIFIED / CONFLICT

sources 앱(수집기)이 쓰는 것
    prepare_chunks(body, title) -> list[str]
    embed_passages(chunks) -> list[list[float]]

Django 교체 자리 어댑터 (integration.py)
    chat_answer(...)            : chatbot.services.ai_client.generate_answer (CHATBOT_AI_MODE=rag)
    verify_interpretation(...)  : documents.services.pipeline.verify (DOCUMENTS_VERIFY_MODE=rag)

청크·임베딩 생성 (indexing.py, manage.py shell에서)
    build_source_chunks(source_ids=None)

S4 체크리스트·전화 대본 (checklist.py, DOCUMENTS_CHECKLIST_MODE=dummy|rag)
    build_actions(result, verification, language) -> {"checklist": [...], "call_script": str}
"""
from .checklist import build_actions
from .chunker import prepare_chunks
from .embedder import embed_passages
from .indexing import build_source_chunks
from .integration import chat_answer, verify_interpretation
from .service import GroundingStatus, generate_answer, generate_answer_dummy

__all__ = [
    "generate_answer", "generate_answer_dummy", "GroundingStatus", "prepare_chunks", "embed_passages",
    "build_source_chunks", "chat_answer", "verify_interpretation", "build_actions",
]
