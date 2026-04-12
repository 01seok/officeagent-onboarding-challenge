from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Document:
    doc_id: str
    filename: str
    content_type: str
    status: str  # "processing" | "completed" | "failed"
    chunk_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
