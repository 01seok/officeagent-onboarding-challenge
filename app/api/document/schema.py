from pydantic import BaseModel


class DocumentUploadResponse(BaseModel):
    doc_id: str
    filename: str
    status: str


class DocumentListItem(BaseModel):
    doc_id: str
    filename: str
    status: str
    chunk_count: int
    created_at: str
