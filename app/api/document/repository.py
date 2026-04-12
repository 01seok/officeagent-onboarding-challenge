from app.api.document.domain import Document
from app.infra.chroma import ChromaClient
from app.infra.doc_store import DocumentStore


class DocumentRepository:
    def __init__(self, chroma: ChromaClient, doc_store: DocumentStore):
        self._chroma = chroma
        self._store = doc_store

    def save(self, doc: Document) -> None:
        self._store.save(
            {
                "doc_id": doc.doc_id,
                "filename": doc.filename,
                "content_type": doc.content_type,
                "status": doc.status,
                "chunk_count": doc.chunk_count,
                "created_at": doc.created_at,
            }
        )

    def update_status(self, doc_id: str, status: str, chunk_count: int = 0) -> None:
        self._store.update(doc_id, status=status, chunk_count=chunk_count)

    def list_all(self) -> list[dict]:
        return self._store.list_all()

    def get(self, doc_id: str) -> dict | None:
        return self._store.get(doc_id)

    def delete(self, doc_id: str) -> None:
        self._chroma.delete_by_doc_id(doc_id)
        self._store.delete(doc_id)

    def exists(self, doc_id: str) -> bool:
        return self._store.exists(doc_id)

    def add_chunks(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        self._chroma.add_chunks(chunk_ids, embeddings, documents, metadatas)

    def get_all_chunks(self, doc_id: str) -> list[dict]:
        return self._chroma.get_all_chunks(doc_id)
