import unittest

from app.api.query.domain import RetrievalMode
from app.api.query.repository import QueryRepository


# QueryRepository의 fallback 선택 로직만 단위로 점검하기 위한 최소 stub
# 실제 Redis/Chroma/LLM까지 붙이지 않고, 검색 결과 선택 규칙이 의도대로 동작하는지만 봄
class _EmbeddingStub:
    async def embed_query(self, query: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class _BM25Stub:
    def search(self, query: str, top_n: int, doc_id: str | None) -> list[dict]:
        return [
            {
                "chunk_id": "chunk-1",
                "document": "BM25 fallback candidate",
                "metadata": {"doc_id": "doc-1", "chunk_index": 0},
            },
            {
                "chunk_id": "chunk-2",
                "document": "Another fallback candidate",
                "metadata": {"doc_id": "doc-1", "chunk_index": 1},
            },
        ]


class _ChromaVectorFailureStub:
    def vector_search(self, *_args, **_kwargs) -> list[dict]:
        raise RuntimeError("vector is down")


class _DocumentStoreStub:
    def get(self, doc_id: str) -> dict:
        return {"filename": f"{doc_id}.txt"}


# 이 테스트의 목적
# 1. vector 장애 시 BM25 fallback이 비어 버리지 않는지 확인
# 2. hybrid 전용 min score가 single-retriever fallback에 잘못 적용되지 않는지 확인
# 3. repository 계층이 service/LLM 없이도 retrieval mode와 후보 선택을 올바르게 반환하는지 확인

class QueryRepositoryFallbackTest(unittest.IsolatedAsyncioTestCase):
    # vector 장애 시 BM25 fallback이 hybrid threshold에 막히지 않는지 검증
    async def test_bm25_only_fallback_returns_candidates(self) -> None:
        repository = QueryRepository(
            chroma=_ChromaVectorFailureStub(),
            bm25=_BM25Stub(),
            embedding=_EmbeddingStub(),
            doc_store=_DocumentStoreStub(),
        )

        result = await repository.hybrid_search("연차는 어떻게 발생하나요?", top_k=5)

        # vector 장애가 partial failure로 분류되는지 확인
        self.assertEqual(result.mode, RetrievalMode.BM25_ONLY)
        
        # BM25 후보가 실제로 살아남는지 확인
        self.assertEqual(len(result.chunks), 2)
        
        # hybrid min score보다 낮아도 fallback 후보는 전달될 수 있어야 함
        self.assertLess(result.chunks[0].score, 0.007)
        
        # doc metadata 재구성이 유지되는지도 함께 확인
        self.assertEqual(result.chunks[0].filename, "doc-1.txt")


if __name__ == "__main__":
    unittest.main()
