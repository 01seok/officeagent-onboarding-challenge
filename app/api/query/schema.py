from pydantic import BaseModel, Field

# 사용자 질문
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_id: str | None = None

class SourceItem(BaseModel):
    doc_id: str
    filename: str
    chunk_index: int
    text: str
    score: float

class QueryResponse(BaseModel):
    question: str
    answer: str                 # LLM이 생성한 최종 답변
    has_relevant_content: bool  # 문서에 근거가 있는지에 대한 여부
    sources: list[SourceItem]
    cache_hit: bool = False      # L1/L2 캐시 히트 여부를 클라이언트에 전달
    