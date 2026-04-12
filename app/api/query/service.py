from abc import ABC, abstractmethod

from app.api.query.domain import ChunkResult
from app.api.query.repository import QueryRepository


class QueryService(ABC):
    @abstractmethod
    async def search(
        self, question: str, top_k: int, doc_id: str | None
    ) -> list[ChunkResult]:
        ...

class QueryServiceImpl(QueryService):
    def __init__(self, repository: QueryRepository):
        self._repository = repository
    
    async def search(
        self, question: str, top_k: int, doc_id: str | None
    ) -> list[ChunkResult]:
        return await self._repository.hybrid_search(question, top_k, doc_id)