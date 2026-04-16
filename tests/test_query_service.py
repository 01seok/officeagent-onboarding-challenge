import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.query.domain import ChunkResult, HybridSearchOutcome, RetrievalMode
from app.api.query.service import QueryServiceImpl
from app.infra.llm import LLMAnswerResult


# 이 테스트 파일의 목적
# 1. service 계층의 cache write 정책이 retrieval 상태에 따라 달라지는지 확인
# 2. no-content 응답이 semantic cache로 퍼지지 않도록 막는지 확인 (가장 중요)
# 3. 정상 hybrid + grounded positive 응답에서만 semantic cache가 저장되는지 확인
# 검색 후보 선택 보다 한 단계 위에서 응답/캐시 정책을 점검하는 단위 테스트

class QueryServiceCachePolicyTest(unittest.IsolatedAsyncioTestCase):
    # no-content 응답은 semantic cache로 일반화하지 않는지 검증
    async def test_no_content_response_uses_exact_cache_only(self) -> None:
        repository = SimpleNamespace(
            embed_query=AsyncMock(return_value=[0.1, 0.2]),
            hybrid_search=AsyncMock(
                return_value=HybridSearchOutcome(
                    chunks=[],
                    mode=RetrievalMode.HYBRID,
                )
            ),
        )
        llm = SimpleNamespace(generate_answer=AsyncMock())
        cache = SimpleNamespace(
            get_exact=AsyncMock(return_value=None),
            get_semantic=AsyncMock(return_value=None),
            set_exact=AsyncMock(),
            set_semantic=AsyncMock(),
        )

        service = QueryServiceImpl(repository=repository, llm=llm, cache=cache)

        answer, has_relevant_content, sources, cache_hit = await service.answer(
            "이메일 주소가 무엇인가요?",
            top_k=5,
            doc_id=None,
        )
        # fire-and-forget cache task가 event loop에서 한 번 돌 수 있게 양보
        await asyncio.sleep(0)

        # no-content는 exact cache만 남고 semantic cache는 비워야 함
        self.assertEqual(answer, "제공된 문서에서 관련 내용을 찾을 수 없습니다.")
        self.assertFalse(has_relevant_content)
        self.assertEqual(sources, [])
        self.assertFalse(cache_hit)
        cache.set_exact.assert_awaited_once()
        cache.set_semantic.assert_not_awaited()
        llm.generate_answer.assert_not_called()

    # partial failure에서 나온 positive 응답도 semantic cache는 보수적으로 막는지 검증
    async def test_partial_failure_positive_response_uses_exact_cache_only(self) -> None:
        chunk = ChunkResult(
            chunk_id="chunk-1",
            doc_id="doc-1",
            filename="resume.pdf",
            text="PKM 프로젝트에서 Elasticsearch와 Neo4j를 결합했습니다.",
            score=0.0049,
            chunk_index=0,
        )
        repository = SimpleNamespace(
            embed_query=AsyncMock(return_value=[0.1, 0.2]),
            hybrid_search=AsyncMock(
                return_value=HybridSearchOutcome(
                    chunks=[chunk],
                    mode=RetrievalMode.BM25_ONLY,
                )
            ),
        )
        llm = SimpleNamespace(
            generate_answer=AsyncMock(
                return_value=LLMAnswerResult(
                    answer="Elasticsearch와 Neo4j를 결합한 하이브리드 검색입니다.",
                    source_indices=[0],
                    has_relevant_content=True,
                )
            )
        )
        cache = SimpleNamespace(
            get_exact=AsyncMock(return_value=None),
            get_semantic=AsyncMock(return_value=None),
            set_exact=AsyncMock(),
            set_semantic=AsyncMock(),
        )

        service = QueryServiceImpl(repository=repository, llm=llm, cache=cache)

        answer, has_relevant_content, sources, cache_hit = await service.answer(
            "PKM 프로젝트 검색 방식이 뭐였나요?",
            top_k=5,
            doc_id=None,
        )
        # fire-and-forget cache task가 event loop에서 한 번 돌 수 있게 양보
        await asyncio.sleep(0)

        # partial failure에서 나온 positive 응답도 semantic cache로 일반화하지 않음
        self.assertTrue(has_relevant_content)
        self.assertEqual(answer, "Elasticsearch와 Neo4j를 결합한 하이브리드 검색입니다.")
        self.assertEqual(len(sources), 1)
        self.assertFalse(cache_hit)
        cache.set_exact.assert_awaited_once()
        cache.set_semantic.assert_not_awaited()

    # 정상 hybrid + grounded positive 응답은 semantic cache까지 저장되는지 검증
    async def test_hybrid_positive_response_stores_semantic_cache(self) -> None:
        chunk = ChunkResult(
            chunk_id="chunk-1",
            doc_id="doc-1",
            filename="company-policy.txt",
            text="입사 1년 미만: 매월 1일 발생",
            score=0.016,
            chunk_index=0,
        )
        repository = SimpleNamespace(
            embed_query=AsyncMock(return_value=[0.1, 0.2]),
            hybrid_search=AsyncMock(
                return_value=HybridSearchOutcome(
                    chunks=[chunk],
                    mode=RetrievalMode.HYBRID,
                )
            ),
        )
        llm = SimpleNamespace(
            generate_answer=AsyncMock(
                return_value=LLMAnswerResult(
                    answer="입사 1년 미만이면 매월 1일 연차가 발생합니다.",
                    source_indices=[0],
                    has_relevant_content=True,
                )
            )
        )
        cache = SimpleNamespace(
            get_exact=AsyncMock(return_value=None),
            get_semantic=AsyncMock(return_value=None),
            set_exact=AsyncMock(),
            set_semantic=AsyncMock(),
        )

        service = QueryServiceImpl(repository=repository, llm=llm, cache=cache)

        answer, has_relevant_content, sources, cache_hit = await service.answer(
            "연차는 어떻게 발생하나요?",
            top_k=5,
            doc_id=None,
        )
        # fire-and-forget cache task가 event loop에서 한 번 돌 수 있게 양보
        await asyncio.sleep(0)

        # 두 검색기가 정상이고 근거가 있는 응답일 때만 semantic cache 저장 허용
        self.assertTrue(has_relevant_content)
        self.assertEqual(answer, "입사 1년 미만이면 매월 1일 연차가 발생합니다.")
        self.assertEqual(len(sources), 1)
        self.assertFalse(cache_hit)
        cache.set_exact.assert_awaited_once()
        cache.set_semantic.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
