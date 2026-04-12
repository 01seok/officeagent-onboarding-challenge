from dataclasses import dataclass

@dataclass
# RRF로 병합된 최종 점수를 score에 담고, 어떤 문서의 몇 번째 청크인지 추적하기 위한 class
class ChunkResult:
    chunk_id: str
    doc_id: str
    filename: str
    text: str
    score: float    # RRF 병합 점수
    chunk_index: int