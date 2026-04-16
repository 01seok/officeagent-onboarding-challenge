import asyncio
import logging

from app.api.query.domain import ChunkResult, HybridSearchOutcome, RetrievalMode
from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode
from app.infra.bm25 import BM25Searcher
from app.infra.chroma import ChromaClient
from app.infra.doc_store import DocumentStore
from app.infra.embedding import EmbeddingService

logger = logging.getLogger(__name__)

# 검색 설정 상수
_BM25_N = 100   # BM25 후보 수
_VEC_N = 100    # 벡터 검색 후보 수
_RRF_K =  60    # RRF 상수 (높을수록 순위 차이 완화됨)
_BM25_W = 0.3   # BM25 가중치
_VEC_W = 0.7    # 벡터 가중치
_MIN_SCORE = 0.007  # min RRF score (노이즈 제거)
_FALLBACK_TOP_N = 3  # 단일 검색기로 degrade되면 상위 몇 개만 LLM에 전달


class QueryRepository:
    def __init__(
        self,
        chroma: ChromaClient,
        bm25: BM25Searcher,
        embedding: EmbeddingService,
        doc_store: DocumentStore
        ):
            self._chroma = chroma
            self._bm25 = bm25
            self._embedding = embedding
            self._doc_store = doc_store
    
    # 쿼리 임베딩 (L2 Semantic 캐시 조회용으로 서비스 계층에서 재사용)
    async def embed_query(self, query: str) -> list[float]:
        return await self._embedding.embed_query(query)

    # 하이브리드 검색
    async def hybrid_search(
        self,
        query: str,
        top_k: int,
        doc_id: str | None = None
    ) -> HybridSearchOutcome:
        query_embedding = await self._embedding.embed_query(query)
        
        loop = asyncio.get_running_loop()

        # BM25, 벡터 검색 병렬 실행 : 한쪽 실패해도 남은 결과로 RRF 진행 (결함 격리)
        bm25_results, vec_results = await asyncio.gather(
            loop.run_in_executor(None, self._bm25.search, query, _BM25_N, doc_id),
            loop.run_in_executor(
                None,
                self._chroma.vector_search,
                query_embedding,
                _VEC_N,
                {"doc_id": doc_id} if doc_id else None,
            ),
            return_exceptions=True,
        )

        bm25_failed = isinstance(bm25_results, Exception)
        vec_failed = isinstance(vec_results, Exception)

        # 한쪽 실패는 남은 검색기로 계속 진행
        if bm25_failed:
            logger.warning("BM25 검색 실패, Vector 결과만 사용: %r", bm25_results)
        if vec_failed:
            logger.warning("Vector 검색 실패, BM25 결과만 사용: %r", vec_results)

        # 양쪽 모두 실패면 검색 실패로 처리
        if bm25_failed and vec_failed:
            raise AppException(ErrorCode.SEARCH_FAILED)

        # 실패한 쪽만 빈 결과로 치환
        if bm25_failed:
            bm25_results = []
        if vec_failed:
            vec_results = []

        merged = self._rrf_merge(bm25_results, vec_results)
        mode = self._resolve_mode(bm25_failed=bm25_failed, vec_failed=vec_failed)
        top = self._select_candidates(merged, top_k=top_k, mode=mode)

        results: list[ChunkResult] = []
        for r in top:
            meta = r["metadata"]
            doc = self._doc_store.get(meta["doc_id"])
            results.append(
                ChunkResult(
                    chunk_id=r["chunk_id"],
                    doc_id=meta["doc_id"],
                    filename=doc["filename"] if doc else "",
                    text=r["document"],
                    score=r["score"],
                    chunk_index=meta.get("chunk_index", 0),
                )
            )
        return HybridSearchOutcome(chunks=results, mode=mode)

    def _rrf_merge(
        self,
        bm25_results: list[dict],
        vec_results: list[dict],
    ) -> list[dict]:
        scores: dict[str, float] = {}
        chunk_map: dict[str, dict] = {}

        for rank, chunk in enumerate(bm25_results):
            cid = chunk["chunk_id"]
            # RRF 공식 : 가중치 / (k + rank + 1)
            scores[cid] = scores.get(cid, 0.0) + _BM25_W / (_RRF_K + rank + 1)
            chunk_map[cid] = chunk

        for rank, chunk in enumerate(vec_results):
            cid = chunk["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + _VEC_W / (_RRF_K + rank + 1)
            chunk_map[cid] = chunk  # 벡터 결과가 ChromaDB 원본이므로 의도적으로 우선

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [{**chunk_map[cid], "score": score} for cid, score in ranked]

    # 어떤 검색기로 결과를 만들었는지 명시적으로 분류
    def _resolve_mode(
        self,
        *,
        bm25_failed: bool,
        vec_failed: bool,
    ) -> RetrievalMode:
        if vec_failed:
            return RetrievalMode.BM25_ONLY
        if bm25_failed:
            return RetrievalMode.VECTOR_ONLY
        return RetrievalMode.HYBRID

    # 하이브리드 점수와 단일 검색기 degrade 상황의 컷오프를 분리
    def _select_candidates(
        self,
        merged: list[dict],
        *,
        top_k: int,
        mode: RetrievalMode,
    ) -> list[dict]:
        if mode is RetrievalMode.HYBRID:
            # 두 검색기가 모두 살아있을 때만 하이브리드 전용 min score 사용
            return [r for r in merged if r["score"] >= _MIN_SCORE][:top_k]

        # 한쪽 검색기만 살아있으면 hybrid threshold 대신 상위 몇 개만 보수적으로 전달
        fallback_k = min(top_k, _FALLBACK_TOP_N)
        return merged[:fallback_k]
