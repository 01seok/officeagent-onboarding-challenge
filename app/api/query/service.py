import asyncio
from time import perf_counter
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from app.api.query.audit import audit_logger, summarize_sources
from app.api.query.domain import ChunkResult
from app.api.query.repository import QueryRepository
from app.infra.cache import CacheService
from app.infra.llm import LLMService

class QueryService(ABC):
    @abstractmethod
    async def answer(
        self, question: str, top_k: int, doc_id: str | None, audit_id: str | None = None
    ) -> tuple[str, bool, list[ChunkResult], bool]:
        ...

    @abstractmethod
    async def answer_stream(
        self, question: str, top_k: int, doc_id: str | None, audit_id: str | None = None
    ) -> AsyncGenerator[str, None]:
        ...

class QueryServiceImpl(QueryService):
    def __init__(self, repository: QueryRepository, llm: LLMService, cache: CacheService):
        self._repository = repository
        self._llm = llm
        self._cache = cache

    async def answer(
        self, question: str, top_k: int, doc_id: str | None, audit_id: str | None = None
    ) -> tuple[str, bool, list[ChunkResult], bool]:
        # 1단계 L1 Exact 캐시 조회, 완전 일치 질문이면 LLM 호출 없이 즉시 반환
        cached = await self._cache.get_exact(question, doc_id)
        if cached is not None:
            sources = [ChunkResult(**s) for s in cached["sources"]]
            audit_logger.info(
                "query_cache_hit audit_id=%s layer=exact doc_id=%s has_relevant_content=%s source_count=%s sources=\"%s\"",
                audit_id or "-",
                doc_id or "-",
                cached["has_relevant_content"],
                len(sources),
                summarize_sources(sources),
            )
            return cached["answer"], cached["has_relevant_content"], sources, True

        # 2단계 쿼리 임베딩 생성, L2 Semantic 캐시 조회용으로 1회만 계산
        query_embedding = await self._repository.embed_query(question)

        # 3단계 L2 Semantic 캐시 조회, 임베딩 코사인 유사도로 유사 질문 매칭
        cached = await self._cache.get_semantic(query_embedding, doc_id)
        if cached is not None:
            sources = [ChunkResult(**s) for s in cached["sources"]]
            audit_logger.info(
                "query_cache_hit audit_id=%s layer=semantic doc_id=%s has_relevant_content=%s source_count=%s sources=\"%s\"",
                audit_id or "-",
                doc_id or "-",
                cached["has_relevant_content"],
                len(sources),
                summarize_sources(sources),
            )
            return cached["answer"], cached["has_relevant_content"], sources, True

        # 4단계 캐시 미스, 하이브리드 검색 (BM25 + Vector + RRF)
        retrieval_started = perf_counter()
        search_outcome = await self._repository.hybrid_search(question, top_k, doc_id)
        chunks = search_outcome.chunks
        audit_logger.info(
            "query_retrieval audit_id=%s mode=%s partial_failure=%s doc_id=%s requested_top_k=%s selected_chunks=%s duration_ms=%d sources=\"%s\"",
            audit_id or "-",
            search_outcome.mode.value,
            search_outcome.is_partial_failure,
            doc_id or "-",
            top_k,
            len(chunks),
            int((perf_counter() - retrieval_started) * 1000),
            summarize_sources(chunks),
        )

        if not chunks:
            # 검색은 정상 수행됐지만 질문에 대응되는 근거 청크를 찾지 못한 상태
            no_content = {
                "answer": "제공된 문서에서 관련 내용을 찾을 수 없습니다.",
                "has_relevant_content": False,
                "sources": [],
            }
            audit_logger.info(
                "query_no_content audit_id=%s stage=retrieval_empty mode=%s doc_id=%s",
                audit_id or "-",
                search_outcome.mode.value,
                doc_id or "-",
            )
            # no-content는 false negative 전파를 막기 위해 exact cache만 저장
            self._schedule_cache_write(
                question=question,
                doc_id=doc_id,
                query_embedding=query_embedding,
                data=no_content,
                store_semantic=False,
            )
            return no_content["answer"], False, [], False

        # 5단계 LLM 컨텍스트 구성
        context = [{"filename": c.filename, "text": c.text} for c in chunks]

        # 6단계 LLM 호출
        llm_started = perf_counter()
        result = await self._llm.generate_answer(question, context)
        audit_logger.info(
            "query_llm_result audit_id=%s mode=%s has_relevant_content=%s source_indices=%s duration_ms=%d",
            audit_id or "-",
            search_outcome.mode.value,
            result.has_relevant_content,
            result.source_indices,
            int((perf_counter() - llm_started) * 1000),
        )

        # 7단계 LLM이 근거 없다고 판단한 경우 (검색은 됐지만 질문이 문서 범위 밖)
        # AppException이 아닌 정상 응답으로 반환, has_relevant_content=False가 신호 역할
        if not result.has_relevant_content:
            cache_data = {
                "answer": result.answer,
                "has_relevant_content": False,
                "sources": [],
            }
            audit_logger.info(
                "query_no_content audit_id=%s stage=llm_rejected mode=%s doc_id=%s",
                audit_id or "-",
                search_outcome.mode.value,
                doc_id or "-",
            )
            # no-content는 partial failure 여부와 무관하게 semantic cache에 퍼뜨리지 않음
            self._schedule_cache_write(
                question=question,
                doc_id=doc_id,
                query_embedding=query_embedding,
                data=cache_data,
                store_semantic=False,
            )
            return result.answer, False, [], False

        # 8단계 source_indices -> 실제 ChunkResult 매핑
        referenced = [
            chunks[i] for i in dict.fromkeys(result.source_indices)
            if i < len(chunks)
        ]
        # LLM이 source_indices를 주지 못했다면, 근거 있는 답변으로 보지 않음 (할루시네이션 방지)
        if not referenced:
            no_content = {
                "answer": "제공된 문서에서 관련 내용을 찾을 수 없습니다.",
                "has_relevant_content": False,
                "sources": [],
            }
            audit_logger.info(
                "query_no_content audit_id=%s stage=missing_source_indices mode=%s doc_id=%s",
                audit_id or "-",
                search_outcome.mode.value,
                doc_id or "-",
            )
            # 출처가 비어 있는 응답도 semantic cache로 일반화하지 않음
            self._schedule_cache_write(
                question=question,
                doc_id=doc_id,
                query_embedding=query_embedding,
                data=no_content,
                store_semantic=False,
            )
            return no_content["answer"], False, [], False

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
        self._schedule_cache_write(
            question=question,
            doc_id=doc_id,
            query_embedding=query_embedding,
            data=cache_data,
            # semantic cache는 근거가 있는 positive 응답에만 사용
            store_semantic=not search_outcome.is_partial_failure,
        )
        audit_logger.info(
            "query_cache_store audit_id=%s exact=true semantic=%s partial_failure=%s source_count=%s sources=\"%s\"",
            audit_id or "-",
            not search_outcome.is_partial_failure,
            search_outcome.is_partial_failure,
            len(sources),
            summarize_sources(sources),
        )

        return result.answer, True, sources, False


    async def answer_stream(
        self, question: str, top_k: int, doc_id: str | None, audit_id: str | None = None
    ) -> AsyncGenerator[str, None]:
        # 1단계 : 스트리밍도 동일한 하이브리드 검색으로 관련 청크 확보
        retrieval_started = perf_counter()
        search_outcome = await self._repository.hybrid_search(question, top_k, doc_id)
        chunks = search_outcome.chunks
        audit_logger.info(
            "query_stream_retrieval audit_id=%s mode=%s partial_failure=%s doc_id=%s requested_top_k=%s selected_chunks=%s duration_ms=%d sources=\"%s\"",
            audit_id or "-",
            search_outcome.mode.value,
            search_outcome.is_partial_failure,
            doc_id or "-",
            top_k,
            len(chunks),
            int((perf_counter() - retrieval_started) * 1000),
            summarize_sources(chunks),
        )

        if not chunks:
            # 검색 결과 없으면 안내 문구 1회 yield하고 종료
            audit_logger.info(
                "query_stream_no_content audit_id=%s stage=retrieval_empty mode=%s doc_id=%s",
                audit_id or "-",
                search_outcome.mode.value,
                doc_id or "-",
            )
            yield "제공된 문서에서 관련 내용을 찾을 수 없습니다."
            return

        # 2단계 : answer()와 동일한 컨텍스트 구성, 포맷 일관성 유지
        context = [{"filename": c.filename, "text": c.text} for c in chunks]

        # 3단계 : LLM 토큰을 그대로 통과 (가공 없음)
        async for token in self._llm.generate_answer_stream(question, context):
            yield token

    # exact / semantic 캐시 저장 정책을 한곳에 모아 중복을 줄임
    def _schedule_cache_write(
        self,
        *,
        question: str,
        doc_id: str | None,
        query_embedding: list[float],
        data: dict,
        store_semantic: bool,
    ) -> None:
        exact_key = CacheService._exact_key(question, doc_id)
        asyncio.create_task(self._cache.set_exact(question, doc_id, data))

        if store_semantic:
            asyncio.create_task(
                self._cache.set_semantic(query_embedding, doc_id, exact_key, data)
            )
