"""문서 챗봇 AI 모듈.

chatbot 앱(질문 API)이 쓰는 것
    generate_answer(document, message, history=..., language=...) -> dict
    generate_answer_dummy(...)  : 같은 형식의 더미 (AI 준비 전 연동용)
    GroundingStatus             : GROUNDED / UNVERIFIED / CONFLICT

sources 앱(수집기)이 쓰는 것
    prepare_chunks(body, title) -> list[str]
    embed_passages(chunks) -> list[list[float]]
"""
from .chunker import prepare_chunks
from .embedder import embed_passages
from .service import GroundingStatus, generate_answer, generate_answer_dummy

__all__ = ["generate_answer", "generate_answer_dummy", "GroundingStatus", "prepare_chunks", "embed_passages"]
