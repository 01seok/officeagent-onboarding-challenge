import asyncio
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from app.api.query.domain import ChunkResult
from app.api.query.repository import QueryRepository
from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode
from app.infra.cache import CacheService
from app.infra.llm import LLMService

class QueryService(ABC):
    @abstractmethod
    async def answer(
        self, question: str, top_k: int, doc_id: str | None
    ) -> tuple[str, bool, list[ChunkResult], bool]:
        ...

    @abstractmethod
    async def answer_stream(
        self, question: str, top_k: int, doc_id: str | None
    ) -> AsyncGenerator[str, None]:
        ...

class QueryServiceImpl(QueryService):
    def __init__(self, repository: QueryRepository, llm: LLMService, cache: CacheService):
        self._repository = repository
        self._llm = llm
        self._cache = cache

    async def answer(
        self, question: str, top_k: int, doc_id: str | None
    ) -> tuple[str, bool, list[ChunkResult], bool]:
        # 1단계 L1 Exact 캐시 조회, 완전 일치 질문이면 LLM 호출 없이 즉시 반환
        cached = await self._cache.get_exact(question, doc_id)
        if cached is not None:
            sources = [ChunkResult(**s) for s in cached["sources"]]
            return cached["answer"], cached["has_relevant_content"], sources, True

        # 2단계 쿼리 임베딩 생성, L2 Semantic 캐시 조회용으로 1회만 계산
        query_embedding = await self._repository.embed_query(question)

        # 3단계 L2 Semantic 캐시 조회, 임베딩 코사인 유사도로 유사 질문 매칭
        cached = await self._cache.get_semantic(query_embedding, doc_id)
        if cached is not None:
            sources = [ChunkResult(**s) for s in cached["sources"]]
            return cached["answer"], cached["has_relevant_content"], sources, True

        # 4단계 캐시 미스, 하이브리드 검색 (BM25 + Vector + RRF)
        chunks = await self._repository.hybrid_search(question, top_k, doc_id)

        if not chunks:
            # 검색 인프라 레벨 실패, 색인이 비어있거나 문서가 없는 상태
            raise AppException(ErrorCode.NO_RELEVANT_CONTENT)

        # 5단계 LLM 컨텍스트 구성
        context = [{"filename": c.filename, "text": c.text} for c in chunks]

        # 6단계 LLM 호출
        result = await self._llm.generate_answer(question, context)

        # 7단계 LLM이 근거 없다고 판단한 경우 (검색은 됐지만 질문이 문서 범위 밖)
        # AppException이 아닌 정상 응답으로 반환, has_relevant_content=False가 신호 역할
        if not result.has_relevant_content:
            cache_data = {
                "answer": result.answer,
                "has_relevant_content": False,
                "sources": [],
            }
            # 캐시 저장은 fire-and-forget, 응답 지연에 영향 주지 않음
            exact_key = CacheService._exact_key(question, doc_id)
            asyncio.create_task(self._cache.set_exact(question, doc_id, cache_data))
            asyncio.create_task(
                self._cache.set_semantic(query_embedding, doc_id, exact_key, cache_data)
            )
            return result.answer, False, [], False

        # 8단계 source_indices -> 실제 ChunkResult 매핑
        referenced = [
            chunks[i] for i in dict.fromkeys(result.source_indices)
            if i < len(chunks)
        ]
        # LLM이 source_indices를 비워 반환하면 근거 없음 신호, 전체 청크를 출처로 내리지 않음
        sources = referenced

        # 9단계 L1, L2 캐시 저장 (직렬화 후 fire-and-forget)
        cache_data = {
            "answer": result.answer,
            "has_relevant_content": True,
            "sources": [
                {
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "filename": c.filename,
                    "text": c.text,
                    "score": c.score,
                    "chunk_index": c.chunk_index,
                }
                for c in sources
            ],
        }
        exact_key = CacheService._exact_key(question, doc_id)
        asyncio.create_task(self._cache.set_exact(question, doc_id, cache_data))
        asyncio.create_task(
            self._cache.set_semantic(query_embedding, doc_id, exact_key, cache_data)
        )

        return result.answer, True, sources, False


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
