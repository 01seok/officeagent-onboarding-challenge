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
    sources: list[SourceItem]