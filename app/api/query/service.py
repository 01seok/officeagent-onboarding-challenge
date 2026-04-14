from abc import ABC, abstractmethod
from typing import AsyncGenerator

from app.api.query.domain import ChunkResult
from app.api.query.repository import QueryRepository
from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode
from app.infra.llm import LLMService

class QueryService(ABC):
    @abstractmethod
    async def answer(
        self, question: str, top_k: int, doc_id: str | None
    ) -> tuple[str, bool, list[ChunkResult]]:
        ...
    
    @abstractmethod
    async def answer_stream(
        self, question: str, top_k: int, doc_id: str | None
    ) -> AsyncGenerator[str, None]:
        ...

class QueryServiceImpl(QueryService):
    def __init__(self, repository: QueryRepository, llm: LLMService):
        self._repository = repository
        self._llm = llm

    async def answer(
        self, question: str, top_k: int, doc_id: str | None
    ) -> tuple[str, bool, list[ChunkResult]]:
        # 1단계 하이브리드 검색 (BM25 + Vector + RRF)
        chunks = await self._repository.hybrid_search(question, top_k, doc_id)

        if not chunks:
            # 검색 인프라 레벨 실패, 색인이 비어있거나 문서가 없는 상태
            raise AppException(ErrorCode.NO_RELEVANT_CONTENT)

        # 2단계 LLM 컨텍스트 구성
        context = [{"filename": c.filename, "text": c.text} for c in chunks]

        # 3단계 LLM 호출
        result = await self._llm.generate_answer(question, context)

        # 4단계 LLM이 근거 없다고 판단한 경우 (검색은 됐지만 질문이 문서 범위 밖)
        # AppException이 아닌 정상 응답으로 반환, has_relevant_content=False가 신호 역할
        if not result.has_relevant_content:
            return result.answer, False, []

        # 5단계 source_indices -> 실제 ChunkResult 매핑
        referenced = [
            chunks[i] for i in dict.fromkeys(result.source_indices)
            if i < len(chunks)
        ]
        # LLM이 source_indices를 비워 반환하면 근거 없음 신호, 전체 청크를 출처로 내리지 않음
        sources = referenced

        return result.answer, True, sources
    
    
    async def answer_stream(
        self, question: str, top_k: int, doc_id: str | None
    ) -> AsyncGenerator[str, None]:
        # 1단계 : 스트리밍도 동일한 하이브리드 검색으로 관련 청크 확보
        chunks = await self._repository.hybrid_search(question, top_k, doc_id)

        if not chunks:
            # 검색 결과 없으면 안내 문구 1회 yield하고 종료
            yield "제공된 문서에서 관련 내용을 찾을 수 없습니다."
            return

        # 2단계 : answer()와 동일한 컨텍스트 구성, 포맷 일관성 유지
        context = [{"filename": c.filename, "text": c.text} for c in chunks]

        # 3단계 : LLM 토큰을 그대로 통과 (가공 없음)
        async for token in self._llm.generate_answer_stream(question, context):
            yield token