import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import UploadFile

from app.api.document.domain import Document
from app.api.document.repository import DocumentRepository
from app.common.exception.app_exception import AppException
from app.common.exception.error_code import ErrorCode
from app.infra.bm25 import BM25Searcher
from app.infra.cache import CacheService
from app.infra.chunker import RecursiveTextChunker
from app.infra.embedding import EmbeddingService
from app.infra.parser import DocumentParser


class DocumentService(ABC):
    @abstractmethod
    async def upload(self, file: UploadFile) -> Document:
        ...

    @abstractmethod
    async def process(self, doc_id: str, content_type: str, data: bytes) -> None:
        ...

    @abstractmethod
    def list_documents(self) -> list[dict]:
        ...

    @abstractmethod
    async def delete_document(self, doc_id: str) -> None:
        ...


class DocumentServiceImpl(DocumentService):
    def __init__(
        self,
        repository: DocumentRepository,
        embedding: EmbeddingService,
        bm25: BM25Searcher,
        cache: CacheService,
    ):
        self._repo = repository
        self._embedding = embedding
        self._bm25 = bm25
        self._cache = cache
        self._parser = DocumentParser()
        self._chunker = RecursiveTextChunker()

    async def upload(self, file: UploadFile) -> Document:
        doc = Document(
            doc_id=str(uuid.uuid4()),
            filename=file.filename or "unknown",
            content_type=file.content_type or "text/plain",
            status="processing",
            created_at=datetime.utcnow().isoformat(),
        )
        self._repo.save(doc)
        return doc

    async def process(self, doc_id: str, content_type: str, data: bytes) -> None:
        try:
            text = self._parser.parse(content_type, data)
            chunks = self._chunker.chunk(text, doc_id)

            if not chunks:
                self._repo.update_status(doc_id, "failed")
                return

            embeddings = await self._embedding.embed_batch([c.text for c in chunks])

            doc_meta = self._repo.get(doc_id)
            filename = doc_meta["filename"] if doc_meta else ""

            self._repo.add_chunks(
                chunk_ids=[c.chunk_id for c in chunks],
                embeddings=embeddings,
                documents=[c.text for c in chunks],
                metadatas=[
                    {
                        "doc_id": doc_id,
                        "filename": filename,
                        "chunk_index": c.chunk_index,
                        "original_text": c.text,
                    }
                    for c in chunks
                ],
            )
            self._repo.update_status(doc_id, "completed", chunk_count=len(chunks))
            self._bm25.invalidate()

        except AppException:
            self._repo.update_status(doc_id, "failed")
            raise
        except Exception:
            self._repo.update_status(doc_id, "failed")
            logger.exception("문서 처리 실패: doc_id=%s", doc_id)  # 원인 추적용

    def list_documents(self) -> list[dict]:
        return self._repo.list_all()

    async def delete_document(self, doc_id: str) -> None:
        if not self._repo.exists(doc_id):
            raise AppException(ErrorCode.DOCUMENT_NOT_FOUND)
        self._repo.delete(doc_id)
        self._bm25.invalidate()
        # 문서 삭제 시 해당 doc_id로 저장된 캐시 엔트리 무효화
        await self._cache.invalidate(doc_id)
