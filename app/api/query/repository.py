import asyncio

from app.api.query.domain import ChunkResult
from app.infra.bm25 import BM25Searcher
from app.infra.chroma import ChromaClient
from app.infra.doc_store import DocumentStore
from app.infra.embedding import EmbeddingService

# 검색 설정 상수
_BM25_N = 100   # BM25 후보 수
_VEC_N = 100    # 벡터 검색 후보 수
_RRF_K =  60    # RRF 상수 (높을수록 순위 차이 완화됨)
_BM25_W = 0.3   # BM25 가중치
_VEC_W = 0.7    # 벡터 가중치
_MIN_SCORE = 0.007  # min RRF score (노이즈 제거)


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
    ) -> list[ChunkResult]:
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

        # 예외 수신 시 빈 리스트로 대체 -> 벡터 실패 시 BM25만, BM25 실패 시 벡터만 사용
        if isinstance(bm25_results, Exception):
            bm25_results = []
        if isinstance(vec_results, Exception):
            vec_results = []

        merged = self._rrf_merge(bm25_results, vec_results)
        # RRF 최소 점수 미만 노이즈 제거 후 top_k 선택
        top = [r for r in merged if r["score"] >= _MIN_SCORE][:top_k]

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
        return results

    def _rrf_merge(
        self,
        bm25_results: list[dict],
        vec_results: list[dict],
    ) -> list[dict]:
        scores: dict[str, float] = {}
        chunk_map: dict[str, dict] = {}

        for rank, chunk in enumerate(bm25_results):
            cid = chunk["chunk_id"]
            # RRF 공식: 가중치 / (k + rank + 1)
            scores[cid] = scores.get(cid, 0.0) + _BM25_W / (_RRF_K + rank + 1)
            chunk_map[cid] = chunk

        for rank, chunk in enumerate(vec_results):
            cid = chunk["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + _VEC_W / (_RRF_K + rank + 1)
            chunk_map[cid] = chunk  # 벡터 결과가 ChromaDB 원본이므로 의도적으로 우선

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [{**chunk_map[cid], "score": score} for cid, score in ranked]