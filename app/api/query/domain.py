from dataclasses import dataclass
from enum import Enum

@dataclass
# RRF로 병합된 최종 점수를 score에 담고, 어떤 문서의 몇 번째 청크인지 추적하기 위한 class
class ChunkResult:
    chunk_id: str
    doc_id: str
    filename: str
    text: str
    score: float    # RRF 병합 점수
    chunk_index: int


class RetrievalMode(str, Enum):
    # 검색기가 모두 정상일 때와 단일 검색기로 degrade된 상황을 구분
    HYBRID = "hybrid"
    BM25_ONLY = "bm25_only"
    VECTOR_ONLY = "vector_only"


@dataclass
# 검색 결과와 함께 현재 retrieval 상태를 service 계층으로 전달
class HybridSearchOutcome:
    chunks: list[ChunkResult]
    mode: RetrievalMode

    @property
    def is_partial_failure(self) -> bool:
        return self.mode is not RetrievalMode.HYBRID
